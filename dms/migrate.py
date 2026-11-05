"""Lightweight schema upgrades for existing databases.

``db.create_all()`` creates new tables but never changes existing ones. This adds any
missing columns (all new columns are nullable or have a server default), seeds the
predefined roles and back-fills derived data.
"""
import logging

from sqlalchemy import inspect, text

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


def _backfill():
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE document_versions SET major = version_number, minor = 0 WHERE major IS NULL"))


def upgrade(app):
    from .security.auth import ensure_predefined_roles
    from .services import search
    _add_missing_columns()
    _backfill()
    ensure_predefined_roles()
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
