"""Lightweight schema upgrades for existing databases.

``db.create_all()`` creates new tables but never changes existing ones. This adds any
missing columns (all new columns are nullable or have a server default), seeds the
predefined roles and back-fills derived data.
"""
import logging

from sqlalchemy import inspect, select, text

from .extensions import db

log = logging.getLogger(__name__)


def _add_missing_columns():
    engine = db.engine
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    quote = engine.dialect.identifier_preparer.quote  # `name` on MySQL
    added = []
    with engine.begin() as conn:
        for table in db.Model.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.server_default is not None:
                    arg = col.server_default.arg
                    arg = arg if isinstance(arg, str) else arg.text
                    default = f" DEFAULT {arg}" if arg.isdigit() else f" DEFAULT '{arg}'"
                conn.execute(text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(col.name)} {ddl_type}{default}"))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.info("Database upgraded: added columns %s", ", ".join(added))
    return added


def _add_missing_indexes():
    """Create model indexes that ``create_all`` skips on tables that already exist."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    added = []
    for table in db.Model.metadata.sorted_tables:
        if table.name != "activity_log" or table.name not in existing_tables:
            continue
        have = {ix["name"] for ix in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name and index.name not in have:
                index.create(db.engine)
                added.append(index.name)
    if added:
        log.info("Database upgraded: added indexes %s", ", ".join(added))


def _audit_objects():
    """Audit procedure and the triggers that make activity_log append-only."""
    from .sp import ensure_audit_objects
    try:
        with db.engine.begin() as conn:
            created = ensure_audit_objects(conn)
        if created:
            log.info("Installed audit log objects: %s", ", ".join(created))
    except Exception as exc:  # e.g. the database account may not create triggers
        log.warning("Could not install the audit log procedure/triggers (%s). Run 'flask install-procedures' "
                    "with an account that has CREATE ROUTINE and TRIGGER privileges.", exc)


def _backfill():
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE document_versions SET major = version_number, minor = 0 WHERE major IS NULL"))


def _admin_owner_grants():
    """Give administrators an explicit owner grant on folders and documents they created.

    Administrators used to reach everything through their repository rights, so no owner grant was
    written for what they created. Those rights no longer flow down to content (privacy), so without
    this they would lose access to their own items. Idempotent: only adds missing grants.
    """
    from .models import AccessEntry, Document, Folder, User
    from .security.permissions import CONTENT_PERMS, parse_perms
    admin_ids = {u.id for u in db.session.execute(select(User)).scalars() if u.is_admin}
    if not admin_ids:
        return
    have = set(db.session.execute(select(AccessEntry.target_type, AccessEntry.target_id, AccessEntry.principal_id)
                                  .where(AccessEntry.principal_type == "user")).all())
    added = 0
    for ttype, model in (("folder", Folder), ("document", Document)):
        rows = db.session.execute(select(model.id, model.created_by_id)
                                  .where(model.created_by_id.in_(admin_ids))).all()
        for oid, uid in rows:
            if (ttype, oid, uid) not in have:
                db.session.add(AccessEntry(target_type=ttype, target_id=oid, principal_type="user",
                                           principal_id=uid, permissions=parse_perms(CONTENT_PERMS),
                                           created_by_id=uid))
                added += 1
    if added:
        db.session.commit()
        log.info("Privacy upgrade: added %s owner grant(s) for administrators' own items", added)


def _restricted_repository():
    """Restricted by default: Contributor, Reader and Auditor lose their repository-wide grants.

    So that nobody loses access to what they can open today, each role's old repository grant is
    copied onto every existing top-level Shared library folder and root document that inherits from
    the repository (including items in the recycle bin, so a restore keeps them). The personal-files
    container never inherits, so My Files are untouched. Runs once (a setting records it), so an
    administrator may later give a role repository access again on purpose.
    """
    from .models import AccessEntry, Document, Folder, Role
    from .security.permissions import LEGACY_REPOSITORY_PERMS, parse_perms
    from .services import settings
    if settings.get("restricted_repository_migrated"):
        return
    home_container = settings.get("home_container_folder_id")
    folder_ids = [fid for fid in db.session.execute(select(Folder.id).where(
        Folder.parent_id.is_(None), Folder.inherit_permissions.is_(True))).scalars() if fid != home_container]
    doc_ids = list(db.session.execute(select(Document.id).where(
        Document.folder_id.is_(None), Document.inherit_permissions.is_(True))).scalars())
    moved = []
    for name in LEGACY_REPOSITORY_PERMS:
        role = db.session.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
        if role is None:
            continue
        entry = db.session.execute(select(AccessEntry).where(
            AccessEntry.target_type == "repository", AccessEntry.target_id.is_(None),
            AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id)).scalar_one_or_none()
        if entry is None:
            continue
        perms = entry.permission_set
        have = {(t, i): e for t, i, e in (
            (e.target_type, e.target_id, e) for e in db.session.execute(select(AccessEntry).where(
                AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id)).scalars())}
        for ttype, ids in (("folder", folder_ids), ("document", doc_ids)):
            for oid in ids:
                existing = have.get((ttype, oid))
                if existing is not None:
                    existing.permissions = parse_perms(existing.permission_set | perms)
                else:
                    db.session.add(AccessEntry(target_type=ttype, target_id=oid, principal_type="role",
                                               principal_id=role.id, permissions=parse_perms(perms)))
        db.session.delete(entry)
        moved.append(name)
    settings.set_value("restricted_repository_migrated", True)
    db.session.commit()
    if moved:
        log.info("Restricted repository: moved repository-wide access of %s onto %s top-level folder(s) and "
                 "%s root document(s)", ", ".join(moved), len(folder_ids), len(doc_ids))


def upgrade(app):
    from .security.auth import ensure_predefined_roles
    from .services import search
    _add_missing_columns()
    _add_missing_indexes()
    _audit_objects()
    _backfill()
    ensure_predefined_roles()
    _admin_owner_grants()
    _restricted_repository()
    # Index existing content the first time search is available.
    from .models import ArchivedEmail, Document, SearchEntry
    if db.session.query(SearchEntry.id).first() is None and (
            db.session.query(Document.id).first() is not None or db.session.query(ArchivedEmail.id).first() is not None):
        try:
            n = search.reindex_all()
            log.info("Built the search index for %s item(s)", n)
        except Exception:
            log.exception("Initial search indexing failed; run 'flask reindex' later")
            db.session.rollback()
