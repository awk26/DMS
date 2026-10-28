"""Administration console: users, roles, settings, metadata model, devices, system status."""
from flask import Blueprint, current_app, render_template, request, url_for
from sqlalchemy import func, select

from ..config import oidc_provider_configs
from ..extensions import db
from ..models import (AccessEntry, DocumentType, MetadataField, Role, Tag, User, UserDevice, Vocabulary)
from ..security import ldap_auth, saml
from ..security.auth import require_capability
from ..security.malware import defender_path, engine_name
from ..security.permissions import (CAP_LABELS, CONTENT_PERMS, PERM_HELP, PERM_LABELS, SYSTEM_CAPS, parse_perms,
                                    reset_resolver)
from ..services import metadata as meta_svc
from ..services import settings
from ..services.activity import log_activity
from ..services.folders import folder_tree
from ..services.preview import libreoffice_path
from ..services.users import apply_user_data as _apply_user_form
from ..validation import KNOWN_TYPES, ValidationError
from ._helpers import handle_errors, ok

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@require_capability("admin")
def _only_admins():
    return None


def _roles():
    return db.session.execute(select(Role).order_by(Role.name)).scalars().all()


# --------------------------------------------------------------------------- users

@bp.get("/")
def index():
    return render_template("admin/index.html", **_status())


def _status():
    storage = current_app.extensions["dms_storage"]
    cfg = current_app.config
    return {
        "users": db.session.execute(select(func.count(User.id))).scalar(),
        "pending_devices": db.session.execute(select(func.count(UserDevice.id)).where(UserDevice.status == "pending")).scalar(),
        "encryption": storage.encrypted, "key_id": storage.ring.active_key_id if storage.ring else None,
        "scanner": engine_name(), "clamav": bool(cfg.get("CLAMAV_HOST") or cfg.get("CLAMAV_SOCKET")),
        "defender": bool(defender_path()), "libreoffice": libreoffice_path(),
        "ldap": ldap_auth.enabled(), "saml": saml.enabled(), "oidc": list(oidc_provider_configs(cfg.get("OIDC_PROVIDERS") or []).values()),
        "https": cfg.get("FORCE_HTTPS"), "cert": bool(cfg.get("SSL_CERT_FILE")),
        "secure_cookie": cfg.get("SESSION_COOKIE_SECURE"),
    }


@bp.get("/users")
def users():
    q = (request.args.get("q") or "").strip()
    stmt = select(User).order_by(func.lower(User.username))
    if q:
        stmt = stmt.where(User.username.ilike(f"%{q}%") | User.email.ilike(f"%{q}%") | User.display_name.ilike(f"%{q}%"))
    return render_template("admin/users.html", users=db.session.execute(stmt).scalars().all(), q=q, roles=_roles())


@bp.route("/users/new", methods=["GET", "POST"])
@handle_errors(lambda **_: url_for("admin.users"))
def user_new():
    if request.method == "POST":
        user = User()
        _apply_user_form(user, request.form, creating=True)
        db.session.add(user)
        db.session.flush()
        log_activity("admin", "user_created", f"User '{user.username}' created", user.id)
        db.session.commit()
        return ok(f"User '{user.username}' created.", url_for("admin.users"))
    return render_template("admin/user_edit.html", user=None, roles=_roles())


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@handle_errors(lambda **kw: url_for("admin.user_edit", user_id=kw.get("user_id")))
def user_edit(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise ValidationError("User not found.")
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "reset_mfa":
            user.mfa_enabled, user.mfa_secret, user.mfa_recovery_json = False, None, "[]"
            log_activity("security", "mfa_reset", f"MFA reset for '{user.username}' by an administrator", user.id)
            db.session.commit()
            return ok("Multi-factor authentication reset. The user can enrol again.", url_for("admin.user_edit", user_id=user.id))
        if action == "unlock":
            user.failed_logins, user.locked_until = 0, None
            db.session.commit()
            return ok("Account unlocked.", url_for("admin.user_edit", user_id=user.id))
        was_admin = user.is_admin
        _apply_user_form(user, request.form, creating=False)
        db.session.flush()
        if was_admin and not user.is_admin or (was_admin and not user.is_active):
            remaining = [u for u in db.session.execute(select(User).where(User.is_active.is_(True))).scalars() if u.is_admin]
            if not remaining:
                raise ValidationError("At least one active administrator must remain.")
        log_activity("admin", "user_updated",
                     f"User '{user.username}' updated (roles: {', '.join(r.name for r in user.roles) or 'none'})", user.id)
        db.session.commit()
        reset_resolver()
        return ok(f"User '{user.username}' saved.", url_for("admin.users"))
    devices = db.session.execute(select(UserDevice).where(UserDevice.user_id == user.id)
                                 .order_by(UserDevice.last_seen.desc())).scalars().all()
    return render_template("admin/user_edit.html", user=user, roles=_roles(), devices=devices)


# --------------------------------------------------------------------------- roles

def _repo_entry(role):
    return db.session.execute(select(AccessEntry).where(
        AccessEntry.target_type == "repository", AccessEntry.target_id.is_(None),
        AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id)).scalar_one_or_none()


@bp.get("/roles")
def roles():
    rows = [(r, (_repo_entry(r).permission_set if _repo_entry(r) else set()), len(r.users)) for r in _roles()]
    return render_template("admin/roles.html", rows=rows, perms=CONTENT_PERMS, perm_labels=PERM_LABELS,
                           cap_labels=CAP_LABELS)


@bp.route("/roles/new", methods=["GET", "POST"])
@bp.route("/roles/<int:role_id>", methods=["GET", "POST"])
@handle_errors(lambda **_: url_for("admin.roles"))
def role_edit(role_id=None):
    role = db.session.get(Role, role_id) if role_id else None
    if role_id and role is None:
        raise ValidationError("Role not found.")
    if request.method == "POST":
        if request.form.get("action") == "delete":
            if role is None or role.is_system:
                raise ValidationError("Predefined roles cannot be deleted.")
            name = role.name
            db.session.query(AccessEntry).filter(AccessEntry.principal_type == "role",
                                                 AccessEntry.principal_id == role.id).delete(synchronize_session=False)
            db.session.delete(role)
            log_activity("admin", "role_deleted", f"Role '{name}' deleted")
            db.session.commit()
            return ok(f"Role '{name}' deleted.", url_for("admin.roles"))
        name = (request.form.get("name") or "").strip()[:80]
        if not name:
            raise ValidationError("A role name is required.")
        clash = db.session.execute(select(Role).where(func.lower(Role.name) == name.lower())).scalar_one_or_none()
        if clash is not None and clash is not role:
            raise ValidationError(f"A role named '{name}' already exists.")
        if role is None:
            role = Role(is_system=False)
            db.session.add(role)
        elif role.is_system and name != role.name:
            raise ValidationError("Predefined roles cannot be renamed.")
        role.name = name
        role.description = (request.form.get("description") or "").strip()[:500]
        caps = [c for c in SYSTEM_CAPS if c in request.form.getlist("caps")]
        if role.is_system and role.name == "Admin" and "admin" not in caps:
            raise ValidationError("The Admin role must keep the administration capability.")
        role.capabilities = ",".join(caps)
        db.session.flush()
        perms = [p for p in CONTENT_PERMS if p in request.form.getlist("perms")]
        entry = _repo_entry(role)
        if perms:
            if entry is None:
                entry = AccessEntry(target_type="repository", target_id=None, principal_type="role", principal_id=role.id)
                db.session.add(entry)
            entry.permissions = parse_perms(perms)
        elif entry is not None:
            db.session.delete(entry)
        log_activity("admin", "role_saved", f"Role '{role.name}' saved (capabilities: {', '.join(caps) or 'none'}; "
                                            f"repository: {', '.join(perms) or 'none'})", role.id)
        db.session.commit()
        reset_resolver()
        return ok(f"Role '{role.name}' saved.", url_for("admin.roles"))
    entry = _repo_entry(role) if role else None
    return render_template("admin/role_edit.html", role=role, repo_perms=entry.permission_set if entry else set(),
                           perms=CONTENT_PERMS, perm_labels=PERM_LABELS, perm_help=PERM_HELP, caps=SYSTEM_CAPS,
                           cap_labels=CAP_LABELS)


# --------------------------------------------------------------------------- settings

@bp.route("/settings", methods=["GET", "POST"])
@handle_errors(lambda **_: url_for("admin.settings_page"))
def settings_page():
    if request.method == "POST":
        settings.update_from_form(request.form)
        log_activity("admin", "settings_changed", "System settings updated")
        db.session.commit()
        settings.clear_cache()
        return ok("Settings saved.", url_for("admin.settings_page"))
    return render_template("admin/settings.html", s=settings.all_settings(), roles=_roles(),
                           known=sorted(KNOWN_TYPES), platforms=settings.PLATFORMS,
                           server_max=current_app.config["MAX_REQUEST_MB"])


# --------------------------------------------------------------------------- metadata model

@bp.get("/metadata")
def metadata():
    return render_template("admin/metadata.html", types=meta_svc.document_types(), fields=meta_svc.fields(),
                           vocabularies=meta_svc.vocabularies(), field_types=meta_svc.FIELD_TYPES,
                           folders=folder_tree(), tags=meta_svc.all_tags(),
                           tags_controlled=settings.get("tags_controlled"))


@bp.post("/metadata/types")
@handle_errors(lambda **_: url_for("admin.metadata"))
def type_save():
    type_id = request.form.get("id", type=int)
    dt = db.session.get(DocumentType, type_id) if type_id else DocumentType()
    if request.form.get("action") == "delete" and type_id:
        db.session.delete(dt)
        db.session.commit()
        return ok("Document type deleted.", url_for("admin.metadata"))
    name = (request.form.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError("A name is required.")
    clash = db.session.execute(select(DocumentType).where(func.lower(DocumentType.name) == name.lower())).scalar_one_or_none()
    if clash is not None and clash is not dt:
        raise ValidationError(f"A document type named '{name}' already exists.")
    dt.name, dt.description = name, (request.form.get("description") or "").strip()[:500]
    db.session.add(dt)
    log_activity("admin", "doctype_saved", f"Document type '{name}' saved")
    db.session.commit()
    return ok(f"Document type '{name}' saved.", url_for("admin.metadata"))


@bp.post("/metadata/fields")
@handle_errors(lambda **_: url_for("admin.metadata"))
def field_save():
    field_id = request.form.get("id", type=int)
    field = db.session.get(MetadataField, field_id) if field_id else None
    if request.form.get("action") == "delete" and field is not None:
        db.session.delete(field)
        db.session.commit()
        return ok("Field deleted.", url_for("admin.metadata"))
    field = meta_svc.save_field(field, request.form)
    log_activity("admin", "field_saved", f"Metadata field '{field.label}' saved")
    db.session.commit()
    return ok(f"Field '{field.label}' saved.", url_for("admin.metadata"))


@bp.post("/metadata/vocabularies")
@handle_errors(lambda **_: url_for("admin.metadata"))
def vocabulary_save():
    vocab_id = request.form.get("id", type=int)
    vocab = db.session.get(Vocabulary, vocab_id) if vocab_id else None
    if request.form.get("action") == "delete" and vocab is not None:
        db.session.delete(vocab)
        db.session.commit()
        return ok("Vocabulary deleted.", url_for("admin.metadata"))
    vocab = meta_svc.save_vocabulary(vocab, request.form.get("name"), request.form.get("description"),
                                     request.form.get("terms"))
    log_activity("admin", "vocabulary_saved", f"Vocabulary '{vocab.name}' saved")
    db.session.commit()
    return ok(f"Vocabulary '{vocab.name}' saved.", url_for("admin.metadata"))


@bp.post("/metadata/tags")
@handle_errors(lambda **_: url_for("admin.metadata"))
def tags_save():
    if request.form.get("action") == "delete":
        tag = db.session.get(Tag, request.form.get("id", type=int) or 0)
        if tag is not None:
            db.session.delete(tag)
            db.session.commit()
        return ok("Tag deleted.", url_for("admin.metadata"))
    names = meta_svc.parse_tags(request.form.get("names"))
    for name in names:
        if not db.session.execute(select(Tag).where(func.lower(Tag.name) == name.lower())).first():
            db.session.add(Tag(name=name))
    log_activity("admin", "tags_added", f"Approved tags added: {', '.join(names)}"[:500])
    db.session.commit()
    return ok(f"{len(names)} tag(s) saved.", url_for("admin.metadata"))


# --------------------------------------------------------------------------- devices

@bp.get("/devices")
def devices():
    rows = db.session.execute(select(UserDevice).order_by(UserDevice.status != "pending", UserDevice.last_seen.desc())
                              ).scalars().all()
    return render_template("admin/devices.html", devices=rows, policy=settings.get("device_policy"),
                           blocked=settings.get("blocked_platforms"))


@bp.post("/devices/<int:device_id>")
@handle_errors(lambda **_: url_for("admin.devices"))
def device_action(device_id):
    device = db.session.get(UserDevice, device_id)
    if device is None:
        raise ValidationError("Device not found.")
    action = request.form.get("action")
    if action == "remove":
        db.session.delete(device)
    elif action in ("approved", "blocked"):
        device.status = action
    else:
        raise ValidationError("Unknown action.")
    log_activity("security", f"device_{action}", f"{device.platform} device of '{device.user.username}': {action}",
                 device.user_id)
    db.session.commit()
    return ok("Device updated.", url_for("admin.devices"))


@bp.post("/maintenance")
def maintenance():
    from ..services.retention import run_maintenance
    result = run_maintenance()
    log_activity("admin", "maintenance", f"Maintenance run: {result}")
    db.session.commit()
    return ok(f"Maintenance complete: {result['recycle_bin_purged']} expired item(s) purged, "
              f"{result['versions_pruned']} old version(s) removed.", url_for("admin.index"))


@bp.post("/reindex")
def reindex():
    from ..services.search import reindex_all
    n = reindex_all()
    log_activity("admin", "reindex", f"Search index rebuilt ({n} items)")
    db.session.commit()
    return ok(f"Search index rebuilt for {n} item(s).", url_for("admin.index"))
