"""REST API (v1) for the desktop sync client, mobile app and integrations (FR-DOC-01).

Authenticate with a personal access token created under *My account*:

    Authorization: Bearer dms_xxxxxxxx

The same permissions, IP restrictions, file-type rules, malware scanning, check-out locks
and audit logging apply as in the web interface. Sync clients use ``changed_since`` to
fetch only what changed since their last run.
"""
from datetime import datetime, timezone
from functools import wraps
import io
import math
import re
import secrets

from flask import Blueprint, Response, abort, current_app, g, jsonify, request, send_file, session, url_for
from sqlalchemy import func, or_, select
from werkzeug.datastructures import MultiDict
from werkzeug.datastructures import FileStorage

from ..extensions import db
from ..models import (
    AccessEntry,
    ApiToken,
    ArchivedEmail,
    Document,
    DocumentType,
    DocumentLink,
    DocumentVersion,
    EmailAttachment,
    Favourite,
    Folder,
    FolderActivity,
    MetadataField,
    Role,
    Tag,
    User,
    UserDevice,
    Vocabulary,
    utcnow,
)
from ..security import ldap_auth, mfa
from ..security.auth import (
    AuthError,
    _load_session_user,
    check_password_policy,
    device_problem,
    find_user,
    hash_password,
    login_user,
    logout_user,
    provision_external,
    register_failure,
    user_for_token,
    verify_local,
)
from ..security.permissions import CONTENT_PERMS, SYSTEM_CAPS, parse_perms, resolver, reset_resolver
from ..services import access as access_svc
from ..services import documents as svc, emails as email_svc, metadata as meta_svc, search as search_svc
from ..services.activity import log_activity
from ..services.folders import (
    NotFound,
    create_folder,
    copy_folder,
    delete_folder,
    get_folder,
    move_folder,
    purge_folder,
    rename_folder,
    restore_folder,
    subtree,
)
from ..services.compare import compare_versions, supported as compare_supported
from ..services.preview import html_rendition, office_preview_mode, pdf_rendition
from ..services.retention import recycle_expiry
from ..validation import NEVER_INLINE, ValidationError, preview_kind
from ..services.recycle_bin import can_manage_trashed, recycle_bin_items
from ..services.users import apply_user_data
from ._helpers import folder_zip_response, send_version, stream_stored

bp = Blueprint("api", __name__, url_prefix="/api/v1")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EMAIL_STATES = ("archived", "inbox", "trashed", "spam", "deleted")
RENDER_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; sandbox; "


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return request.form

    def form_value(value):
        if isinstance(value, bool):
            return "1" if value else "0"
        if value is None:
            return ""
        if isinstance(value, (str, int, float)):
            return str(value)
        return value

    return MultiDict(
        (key, form_value(value))
        for key, values in data.items()
        for value in (values if isinstance(values, list) else [values])
    )


def _integer(value, name, minimum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer.")
    if minimum is not None and result < minimum:
        raise ValidationError(f"{name} must be at least {minimum}.")
    return result


def _query_integer(name):
    value = request.args.get(name)
    return _integer(value, name, 1) if value not in (None, "") else None


def _check_csrf():
    expected = session.get("api_csrf")
    supplied = request.headers.get("X-CSRFToken", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return jsonify(ok=False, error="A valid X-CSRFToken header is required.", code="csrf_failed"), 400
    return None


def _new_csrf_token():
    token = secrets.token_urlsafe(32)
    session["api_csrf"] = token
    return token


def api_auth(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        user = user_for_token(token)
        token_auth = user is not None
        if token_auth:
            db.session.commit()  # last_used_at
        else:
            user, reason = _load_session_user()
            if user is None:
                return jsonify(ok=False, error=reason or "Authentication is required.",
                               code="authentication_required"), 401
            if not session.get("mfa_ok"):
                return jsonify(ok=False, error="Complete the multi-factor challenge.",
                               code="mfa_required"), 401
            device = db.session.get(UserDevice, session.get("device_id") or 0)
            problem = device_problem(user, device)
            if problem:
                session.clear()
                return jsonify(ok=False, error="This device is not permitted.", code=problem), 403
            from ..services import settings
            if settings.get("mfa_required") and user.auth_source in ("local", "ldap") and not user.mfa_enabled \
                    and request.endpoint not in ("api.mfa_setup", "api.mfa_setup_enable"):
                return jsonify(ok=False, error="Multi-factor authentication setup is required.",
                               code="mfa_setup_required"), 403
            if request.method in _UNSAFE_METHODS:
                failure = _check_csrf()
                if failure is not None:
                    return failure
        g.user = user
        reset_resolver()
        try:
            return view(*args, **kwargs)
        except ValidationError as exc:
            db.session.rollback()
            return jsonify(ok=False, error=str(exc), code="validation_error"), 400
        except NotFound as exc:
            db.session.rollback()
            return jsonify(ok=False, error=str(exc), code="not_found"), 404
    return wrapper


@bp.get("/auth/csrf")
def csrf_token():
    token = session.get("api_csrf") or _new_csrf_token()
    return jsonify(csrf_token=token)


@bp.post("/auth/login")
def login():
    failure = _check_csrf()
    if failure is not None:
        return failure
    data = _body()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    try:
        user = verify_local(username, password)
        method = "password"
        if user is None and ldap_auth.enabled():
            existing = find_user(username)
            if existing is not None and existing.locked_until and existing.locked_until > utcnow():
                raise AuthError("This account is temporarily locked after repeated failed sign-ins.")
            if existing is None or existing.auth_source == "ldap":
                info = ldap_auth.authenticate(username, password)
                if info is None and existing is not None:
                    register_failure(existing)
                if info is not None:
                    user = provision_external("ldap", info["dn"], info["email"], info["name"], info["groups"])
                    db.session.flush()
                    method = "ldap"
        if user is None:
            log_activity("security", "login_failed", f"Failed sign-in for '{username[:100]}'")
            db.session.commit()
            return jsonify(ok=False, error="The user name or password is incorrect.",
                           code="invalid_credentials"), 401
        if not user.is_active:
            return jsonify(ok=False, error="Your account is disabled.", code="account_disabled"), 403
        device = login_user(user, method)
    except (AuthError, ldap_auth.LdapUnavailable) as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc), code="authentication_failed"), 401

    problem = device_problem(user, device)
    if problem:
        session.clear()
        db.session.commit()
        return jsonify(ok=False, error="This device is not permitted.", code=problem), 403
    token = _new_csrf_token()
    if not session.get("mfa_ok"):
        return jsonify(ok=True, mfa_required=True, csrf_token=token), 202
    from ..services import settings
    if settings.get("mfa_required") and user.auth_source in ("local", "ldap") and not user.mfa_enabled:
        return jsonify(ok=True, mfa_setup_required=True, csrf_token=token), 202
    return jsonify(ok=True, user={"id": user.id, "username": user.username,
                                  "display_name": user.display_name, "email": user.email,
                                  "roles": [role.name for role in user.roles]},
                   csrf_token=token)


@bp.post("/auth/mfa")
def mfa_challenge():
    failure = _check_csrf()
    if failure is not None:
        return failure
    uid = session.get("uid")
    user = db.session.get(User, uid) if uid else None
    if user is None:
        return jsonify(ok=False, error="Sign in before completing multi-factor authentication.",
                       code="authentication_required"), 401
    if session.get("mfa_ok"):
        return jsonify(ok=True, error="The multi-factor challenge is already complete.",
                       user={"id": user.id, "username": user.username})
    attempts = session.get("mfa_attempts", 0) + 1
    session["mfa_attempts"] = attempts
    if attempts > 5:
        session.clear()
        return jsonify(ok=False, error="Too many incorrect codes. Sign in again.",
                       code="too_many_attempts"), 429
    code = (_body().get("code") or "").strip()
    secret = mfa.unseal(user.mfa_secret)
    if not secret or not (mfa.verify_totp(secret, code) or mfa.use_recovery_code(user, code)):
        return jsonify(ok=False, error="The multi-factor code is incorrect.", code="invalid_mfa_code"), 401
    session["mfa_ok"] = True
    session.pop("mfa_attempts", None)
    log_activity("security", "mfa_challenge", f"'{user.username}' completed multi-factor authentication", user.id)
    db.session.commit()
    return jsonify(ok=True, user={"id": user.id, "username": user.username,
                                  "display_name": user.display_name, "email": user.email,
                                  "roles": [role.name for role in user.roles]})


@bp.post("/auth/logout")
def api_logout():
    if session.get("uid"):
        failure = _check_csrf()
        if failure is not None:
            return failure
        g.user = db.session.get(User, session["uid"])
    logout_user()
    return jsonify(ok=True)


@bp.get("/auth/mfa/setup")
@api_auth
def mfa_setup():
    if g.user.auth_source not in ("local", "ldap"):
        return jsonify(ok=False, error="Multi-factor authentication is managed by your identity provider.",
                       code="unsupported_operation"), 409
    if g.user.mfa_enabled:
        return jsonify(ok=False, error="Multi-factor authentication is already enabled.",
                       code="conflict"), 409
    secret = session.get("mfa_pending_secret")
    if not secret:
        secret = mfa.new_secret()
        session["mfa_pending_secret"] = secret
    uri = mfa.provisioning_uri(secret, g.user.email or g.user.username, current_app.config["APP_NAME"])
    return jsonify(secret=secret, provisioning_uri=uri)


@bp.post("/auth/mfa/setup")
@api_auth
def mfa_setup_enable():
    secret = session.get("mfa_pending_secret")
    if not secret or not mfa.verify_totp(secret, (_body().get("code") or "").strip()):
        raise ValidationError("That multi-factor code is not correct.")
    g.user.mfa_secret = mfa.seal(secret)
    g.user.mfa_enabled = True
    codes, g.user.mfa_recovery_json = mfa.new_recovery_codes()
    session.pop("mfa_pending_secret", None)
    log_activity("security", "mfa_enabled", f"'{g.user.username}' enabled multi-factor authentication", g.user.id)
    db.session.commit()
    return jsonify(ok=True, recovery_codes=codes)


@bp.get("/auth/options")
def auth_options():
    from ..routes.auth import sso_options
    from ..services import settings
    return jsonify(
        setup_required=db.session.execute(select(func.count(User.id))).scalar() == 0,
        signup_enabled=bool(settings.get("self_signup")),
        sso=sso_options(),
        ldap_enabled=ldap_auth.enabled(),
        ldap_label=current_app.config["LDAP_LABEL"] if ldap_auth.enabled() else None,
    )


@bp.post("/auth/setup")
def initial_setup():
    from .auth import _setup_allowed
    if db.session.execute(select(func.count(User.id))).scalar() > 0:
        return jsonify(ok=False, error="Initial setup has already been completed.", code="conflict"), 409
    if not _setup_allowed():
        return jsonify(ok=False, error="Initial setup must be completed from the server or explicitly enabled.",
                       code="setup_forbidden"), 403
    failure = _check_csrf()
    if failure is not None:
        return failure
    data = _body()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    if not username or len(username) > 150:
        raise ValidationError("Choose a user name.")
    if password != (data.get("password2") or ""):
        raise ValidationError("The passwords do not match.")
    check_password_policy(password)
    from ..security.auth import role_by_name
    admin = role_by_name("Admin")
    if admin is None:
        raise ValidationError("The predefined Admin role is unavailable.")
    user = User(username=username, email=(data.get("email") or "").strip().lower() or None,
                display_name=(data.get("display_name") or "").strip()[:200],
                password_hash=hash_password(password), roles=[admin])
    db.session.add(user)
    db.session.flush()
    log_activity("admin", "setup", f"Initial administrator '{username}' created", user.id)
    db.session.commit()
    return jsonify(ok=True, user={"id": user.id, "username": user.username}), 201


@bp.post("/auth/register")
def register():
    failure = _check_csrf()
    if failure is not None:
        return failure
    from ..services import settings
    if not settings.get("self_signup"):
        return jsonify(ok=False, error="Self-registration is disabled.", code="registration_disabled"), 403
    data = _body()
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower()
    display_name = (data.get("display_name") or "").strip()[:200]
    password = data.get("password") or ""
    if not display_name:
        raise ValidationError("Enter your full name.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 320:
        raise ValidationError("Enter a valid email address.")
    if not re.match(r"^[a-z0-9][a-z0-9._-]{2,59}$", username):
        raise ValidationError("User names must be 3–60 characters and use lower-case letters, digits, dots, hyphens or underscores.")
    if find_user(username) is not None or find_user(email) is not None:
        raise ValidationError("An account with this user name or email already exists.")
    if password != (data.get("password2") or ""):
        raise ValidationError("The passwords do not match.")
    check_password_policy(password)
    from ..security.auth import role_by_name
    role = role_by_name(settings.get("signup_default_role")) or role_by_name("Contributor")
    if role and role.capability_set & {"admin", "records"}:
        role = role_by_name("Contributor")
    user = User(username=username, email=email, display_name=display_name,
                password_hash=hash_password(password), roles=[role] if role else [])
    db.session.add(user)
    db.session.flush()
    log_activity("security", "signup", f"'{username}' created an account", user.id)
    db.session.commit()
    device = login_user(user, "password")
    problem = device_problem(user, device)
    if problem:
        session.clear()
        return jsonify(ok=False, error="This device is not permitted.", code=problem), 403
    csrf = _new_csrf_token()
    from ..services import settings
    return jsonify(ok=True, user={"id": user.id, "username": user.username,
                                  "display_name": user.display_name, "email": user.email,
                                  "roles": [item.name for item in user.roles]},
                   csrf_token=csrf, mfa_required=not session.get("mfa_ok"),
                   mfa_setup_required=bool(settings.get("mfa_required") and user.auth_source in ("local", "ldap")
                                           and not user.mfa_enabled)), 201


def _need(perm, obj):
    if not resolver().can(perm, obj):
        if obj is not None and not resolver().can("view", obj):
            abort(404)
        abort(403)


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _folder_json(f):
    data = {"id": f.id, "name": f.name, "parent_id": f.parent_id, "path": f.path,
            "updated_at": f.updated_at.isoformat() + "Z", "permissions": sorted(resolver().folder(f))}
    if f.deleted_at is not None:
        data.update(deleted_at=_iso(f.deleted_at), expires_at=_iso(recycle_expiry(f.deleted_at)))
    return data


def _doc_json(d):
    cv = d.current_version
    data = {
        "id": d.id, "name": d.name, "extension": d.extension, "folder_id": d.folder_id,
        "size": cv.size if cv else 0, "sha256": cv.sha256 if cv else None,
        "mime_type": cv.mime_type if cv else None,
        "version": cv.label if cv else None, "version_number": cv.version_number if cv else None,
        "author": d.created_by.username if d.created_by else None,
        "created_at": d.created_at.isoformat() + "Z", "updated_at": d.updated_at.isoformat() + "Z",
        "deleted": d.deleted_at is not None,
        "checked_out_by": d.checked_out_by.username if d.checked_out_by else None,
        "checked_out_at": _iso(d.checked_out_at),
        "tags": [t.name for t in d.tags], "document_type": d.document_type.name if d.document_type else None,
        "document_type_id": d.document_type_id,
        "metadata": {mv.field.name: mv.value for mv in d.metadata_values},
        "permissions": sorted(resolver().document(d)),
    }
    if d.deleted_at is not None:
        data.update(deleted_at=_iso(d.deleted_at), expires_at=_iso(recycle_expiry(d.deleted_at)))
    return data


def _version_json(v):
    return {"number": v.version_number, "label": v.label, "size": v.size, "sha256": v.sha256,
            "note": v.note, "created_at": v.created_at.isoformat() + "Z",
            "created_by": v.created_by.username if v.created_by else None,
            "restored_from": v.restored_from, "current": v.id == v.document.current_version_id}


def _crumbs(folder):
    """Breadcrumbs from the top down to ``folder``; inside My Files the trail starts there (FR-FLD-04)."""
    if folder is None:
        return []
    chain = folder.ancestors() + [folder]
    home = g.user.home_folder_id
    ids = [f.id for f in chain]
    if home in ids:
        chain = chain[ids.index(home):]
    res = resolver()
    return [{"id": f.id, "name": "My Files" if f.id == home else f.name, "can_view": "view" in res.folder(f)}
            for f in chain]


def _visible_links(folder):
    clause = resolver().visible_documents_clause()
    return db.session.execute(
        select(DocumentLink).join(Document, DocumentLink.document_id == Document.id)
        .where(Document.deleted_at.is_(None), clause,
               DocumentLink.folder_id.is_(None) if folder is None else DocumentLink.folder_id == folder.id)
        .order_by(func.lower(Document.name))
    ).scalars().all()


def _email_json(record):
    return {
        "id": record.id,
        "gmail_message_id": record.gmail_message_id,
        "gmail_thread_id": record.gmail_thread_id,
        "subject": record.subject,
        "sender": record.sender,
        "recipients": record.recipients,
        "cc": record.cc,
        "snippet": record.snippet,
        "received_at": record.received_at.isoformat() + "Z" if record.received_at else None,
        "imported_at": record.imported_at.isoformat() + "Z",
        "size": record.size,
        "sha256": record.sha256,
        "attachment_count": record.attachment_count,
        "gmail_state": record.gmail_state,
        "scan_status": record.scan_status,
        "attachments": [
            {"part_index": item.part_index, "filename": item.filename, "content_type": item.content_type,
             "size": item.size, "is_inline": item.is_inline}
            for item in record.attachments
        ],
    }


def _target(kind, obj_id, number=None):
    if kind == "repository":
        return None
    if kind == "folder":
        return get_folder(obj_id)
    if kind == "document":
        return svc.get_document(obj_id)
    if kind == "version":
        return svc.get_version(svc.get_document(obj_id), number)
    abort(404)


def _visible_documents(folder_id=None, limit=500, offset=0):
    res = resolver()
    stmt = select(Document).where(Document.deleted_at.is_(None), res.visible_documents_clause())
    if folder_id is not None:
        stmt = stmt.where(Document.folder_id == folder_id)
    rows = db.session.execute(
        stmt.order_by(func.lower(Document.name), Document.id).limit(limit).offset(offset)
    ).scalars().all()
    return rows


@bp.get("/me")
@api_auth
def me():
    u = g.user
    return jsonify(id=u.id, username=u.username, display_name=u.display_name, email=u.email,
                   roles=[r.name for r in u.roles])


@bp.get("/folders")
@api_auth
def folders():
    res = resolver()
    rows = db.session.execute(select(Folder).where(Folder.deleted_at.is_(None))
                              .order_by(func.lower(Folder.name))).scalars().all()
    return jsonify(folders=[_folder_json(f) for f in rows if "view" in res.folder(f)])


@bp.post("/folders")
@api_auth
def new_folder():
    data = _body()
    parent = get_folder(data.get("parent_id"))
    _need("edit", parent)
    folder = create_folder(data.get("name", ""), parent, g.user)
    return jsonify(ok=True, folder=_folder_json(folder)), 201


@bp.get("/folders/root")
@api_auth
def root_folder():
    """Top level of the library: root folders (plus shared folders whose parent is hidden) and root documents."""
    from ..services.folders import folder_tree
    res = resolver()
    tree = folder_tree(None if res.is_admin else res.visible_folder_ids())
    documents = db.session.execute(
        select(Document).where(Document.deleted_at.is_(None), Document.folder_id.is_(None),
                               res.visible_documents_clause())
        .order_by(func.lower(Document.name), Document.id)
    ).scalars().all()
    return jsonify(folder=None, breadcrumbs=[], permissions=sorted(res.repository()),
                   folders=[_folder_json(f) for f, depth in tree if depth == 0],
                   documents=[_doc_json(doc) for doc in documents],
                   shortcuts=[{"link_id": link.id, "document": _doc_json(link.document)}
                              for link in _visible_links(None)])


@bp.get("/folders/<int:folder_id>")
@api_auth
def folder_detail(folder_id):
    folder = get_folder(folder_id)
    _need("view", folder)
    visible = resolver().visible_folder_ids()
    children = sorted((child for child in folder.live_children if visible is None or child.id in visible),
                      key=lambda child: child.name.lower())
    documents = _visible_documents(folder.id)
    return jsonify(folder=_folder_json(folder), breadcrumbs=_crumbs(folder),
                   folders=[_folder_json(child) for child in children],
                   documents=[_doc_json(doc) for doc in documents],
                   shortcuts=[{"link_id": link.id, "document": _doc_json(link.document)}
                              for link in _visible_links(folder)])


@bp.patch("/folders/<int:folder_id>")
@api_auth
def update_folder(folder_id):
    data = _body()
    folder = get_folder(folder_id)
    _need("edit", folder)
    if "name" in data:
        rename_folder(folder, data.get("name", ""))
    if "parent_id" in data:
        _need("delete", folder)
        destination = get_folder(data.get("parent_id"))
        _need("edit", destination)
        move_folder(folder, destination)
    return jsonify(ok=True, folder=_folder_json(folder))


@bp.post("/folders/<int:folder_id>/copy")
@api_auth
def copy_folder_api(folder_id):
    data = _body()
    folder = get_folder(folder_id)
    _need("download", folder)
    destination = get_folder(data.get("parent_id"))
    _need("edit", destination)
    copied = copy_folder(folder, destination, g.user)
    return jsonify(ok=True, folder=_folder_json(copied)), 201


@bp.patch("/folders/<int:folder_id>/retention")
@api_auth
def update_folder_retention(folder_id):
    from ..services.activity import log_activity
    folder = get_folder(folder_id)
    if not g.user.has("records") and not g.user.is_admin:
        abort(403)
    data = _body()

    def retention_value(name):
        raw = (data.get(name) or "").strip()
        if not raw:
            return None
        return _integer(raw, name, 0)

    folder.version_keep_last = retention_value("keep_last")
    folder.version_keep_years = retention_value("keep_years")
    log_activity("folder", "retention_set", f"Version retention updated for '{folder.path}'", folder.id)
    db.session.commit()
    return jsonify(ok=True, keep_last=folder.version_keep_last, keep_years=folder.version_keep_years)


@bp.delete("/folders/<int:folder_id>")
@api_auth
def remove_folder(folder_id):
    folder = get_folder(folder_id)
    _need("delete", folder)
    res = resolver()
    for node in folder_tree_subtree(folder):
        if "delete" not in res.folder(node):
            raise ValidationError(f"You do not have permission to delete the subfolder '{node.path}'.")
    delete_folder(folder, g.user)
    return jsonify(ok=True)


def folder_tree_subtree(folder):
    return subtree(folder)


@bp.post("/folders/<int:folder_id>/restore")
@api_auth
def restore_folder_api(folder_id):
    folder = get_folder(folder_id, include_deleted=True)
    if not can_manage_trashed(folder, g.user, resolver()):
        abort(403)
    restore_folder(folder)
    return jsonify(ok=True, folder=_folder_json(folder))


@bp.delete("/folders/<int:folder_id>/purge")
@api_auth
def purge_folder_api(folder_id):
    folder = get_folder(folder_id, include_deleted=True)
    if not can_manage_trashed(folder, g.user, resolver()):
        abort(403)
    purge_folder(folder, reason="permanently deleted through the API")
    return jsonify(ok=True)


@bp.get("/folders/<int:folder_id>/history")
@api_auth
def folder_history(folder_id):
    folder = get_folder(folder_id)
    _need("view", folder)
    rows = db.session.execute(
        select(FolderActivity).where(FolderActivity.folder_id == folder.id)
        .order_by(FolderActivity.created_at.desc(), FolderActivity.id.desc())
    ).scalars().all()
    return jsonify(events=[{
        "id": row.id, "folder_id": row.folder_id, "folder_name": row.folder_name,
        "action": row.action, "details": row.details, "user_name": row.user_name,
        "created_at": row.created_at.isoformat() + "Z",
    } for row in rows])


@bp.get("/folders/<int:folder_id>/download")
@api_auth
def download_folder(folder_id):
    folder = get_folder(folder_id)
    return folder_zip_response(folder)


@bp.get("/folders/download")
@api_auth
def download_root_folders():
    return folder_zip_response(None)


@bp.get("/documents")
@api_auth
def documents():
    """Documents visible to the user. ``changed_since`` (ISO 8601) includes deletions for sync."""
    res = resolver()
    stmt = select(Document).where(res.visible_documents_clause())
    since = request.args.get("changed_since")
    if since:
        try:
            ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            raise ValidationError("changed_since must be an ISO 8601 timestamp.")
        stmt = stmt.where((Document.updated_at >= ts) | (Document.deleted_at >= ts))
    else:
        stmt = stmt.where(Document.deleted_at.is_(None))
    folder_id = request.args.get("folder_id")
    if folder_id is not None:
        if folder_id in ("", "0", "root"):
            stmt = stmt.where(Document.folder_id.is_(None))
        else:
            stmt = stmt.where(Document.folder_id == _integer(folder_id, "folder_id", 1))
    limit = min(1000, max(1, request.args.get("limit", 500, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    rows = db.session.execute(stmt.order_by(Document.updated_at, Document.id).limit(limit).offset(offset)).scalars().all()
    return jsonify(documents=[_doc_json(d) for d in rows], server_time=utcnow().isoformat() + "Z",
                   next_offset=offset + len(rows) if len(rows) == limit else None)


@bp.get("/documents/<int:doc_id>")
@api_auth
def document(doc_id):
    doc = svc.get_document(doc_id)
    _need("view", doc)
    svc.record_view(doc, g.user)  # feeds /recent (FR-FLD-04)
    cv = doc.current_version
    kind = preview_kind(doc.extension)
    links = [link for link in doc.links if link.folder is None or link.folder.deleted_at is None]
    return jsonify(document=_doc_json(doc),
                   breadcrumbs=_crumbs(doc.folder),
                   versions=[_version_json(v) for v in doc.versions if "view" in resolver().version(v)],
                   links=[{"id": link.id, "folder_id": link.folder_id,
                           "folder_path": link.folder.path if link.folder else "Root"} for link in links],
                   preview={"kind": kind,
                            "office_mode": office_preview_mode(cv) if kind == "office" and cv else None,
                            "compare_supported": compare_supported(doc.extension)})


@bp.get("/documents/<int:doc_id>/content")
@api_auth
def content(doc_id):
    doc = svc.get_document(doc_id)
    _need("download", doc)
    raw_number = request.args.get("version")
    number = _integer(raw_number, "version", 1) if raw_number else None
    version = svc.get_version(doc, number) if number else doc.current_version
    if version is None or "download" not in resolver().version(version):
        abort(403)
    return stream_stored(version.storage_key, version.mime_type, doc.name, as_attachment=True, etag=version.sha256)


def _frameable(resp, csp=""):
    """Let the frontend origins in API_CORS_ALLOWED_ORIGINS show this preview in an iframe.

    Browsers ignore X-Frame-Options when CSP frame-ancestors is present.
    """
    origins = " ".join(sorted(current_app.config.get("API_CORS_ALLOWED_ORIGINS") or ()))
    resp.headers["Content-Security-Policy"] = f"{csp}frame-ancestors 'self' {origins}".rstrip()
    return resp


def _readable_version(doc, perm):
    raw_number = request.args.get("version")
    version = svc.get_version(doc, _integer(raw_number, "version", 1)) if raw_number else doc.current_version
    if version is None:
        raise NotFound("Document version not found.")
    if perm not in resolver().version(version):
        abort(403, description="You do not have permission to access this version.")
    return version


@bp.get("/documents/<int:doc_id>/preview")
@api_auth
def preview_document(doc_id):
    """Inline content for PDF, image, text, video and audio; Office files as a PDF rendition (FR-DOC-03)."""
    doc = svc.get_document(doc_id)
    _need("view", doc)
    version = _readable_version(doc, "view")
    kind = preview_kind(doc.extension)
    if kind in ("pdf", "image", "text", "video", "audio") and doc.extension not in NEVER_INLINE:
        return _frameable(send_version(doc, version, inline=True))
    if kind == "office":
        key = pdf_rendition(version)
        if key:
            return _frameable(stream_stored(key, "application/pdf", doc.name.rsplit(".", 1)[0] + ".pdf",
                                            as_attachment=False))
        raise NotFound("No PDF rendition is available for this file; use the /render endpoint instead.")
    raise NotFound("Preview is not available for this file type.")


@bp.get("/documents/<int:doc_id>/render")
@api_auth
def render_document(doc_id):
    """Sanitised standalone HTML rendering of an Office file (show it in a sandboxed iframe)."""
    doc = svc.get_document(doc_id)
    _need("view", doc)
    version = _readable_version(doc, "view")
    html_doc = html_rendition(version)
    if html_doc is None:
        raise NotFound("An HTML preview is not available for this file.")
    return _frameable(Response(html_doc, mimetype="text/html"), RENDER_CSP)


@bp.get("/documents/<int:doc_id>/compare")
@api_auth
def compare_document_versions(doc_id):
    """Side-by-side rows and a redline of two versions (FR-VER-03). Defaults to the latest two."""
    doc = svc.get_document(doc_id)
    _need("view", doc)
    numbers = sorted(v.version_number for v in doc.versions if "view" in resolver().version(v))
    if len(numbers) < 2:
        raise ValidationError("At least two versions are needed for a comparison.")
    b = _integer(request.args["b"], "b", 1) if request.args.get("b") else numbers[-1]
    a = _integer(request.args["a"], "a", 1) if request.args.get("a") else \
        next((n for n in reversed(numbers) if n < b), numbers[0])
    if a == b:
        raise ValidationError("Choose two different versions to compare.")
    old, new = svc.get_version(doc, a), svc.get_version(doc, b)
    for version in (old, new):
        if "view" not in resolver().version(version):
            abort(403, description="You do not have permission to access this version.")
    result = compare_versions(doc, old, new)
    return jsonify(document_id=doc.id, a=_version_json(old), b=_version_json(new), **result)


@bp.post("/documents")
@api_auth
def upload():
    form = _body()
    folder = get_folder(form.get("folder_id"))
    _need("edit", folder)
    files = [f for f in request.files.getlist("file") if f.filename]
    if not files:
        raise ValidationError("Choose a file to upload.")
    document_type = meta_svc.document_type_from_form(form)
    values = meta_svc.values_from_form(form, folder, document_type.id if document_type else None)
    tags = meta_svc.resolve_tags(meta_svc.parse_tags(form.get("tags")))
    if len(files) > 1:
        # Several files in one request: each is an independent upload with its own outcome.
        done, errors = [], []
        for file in files:
            try:
                doc, _ = svc.upload_document(file, folder, note=form.get("note"), user=g.user,
                                             document_type=document_type, metadata=values, tags=tags)
                done.append(doc)
            except ValidationError as exc:
                db.session.rollback()
                errors.append({"filename": file.filename, "error": str(exc)})
        if not done:
            return jsonify(ok=False, error="None of the files could be uploaded.", code="validation_error",
                           documents=[], errors=errors), 400
        return jsonify(ok=True, documents=[_doc_json(doc) for doc in done], errors=errors), 201
    doc, created = svc.upload_document(files[0], folder, form.get("idempotency_key"),
                                       form.get("note"), user=g.user, document_type=document_type,
                                       metadata=values, tags=tags)
    return jsonify(ok=True, created=created, document=_doc_json(doc)), 201 if created else 200


@bp.post("/documents/<int:doc_id>/versions")
@api_auth
def new_version(doc_id):
    doc = svc.get_document(doc_id)
    _need("edit", doc)
    form = _body()
    version, created = svc.add_version(doc, request.files.get("file"), form.get("idempotency_key"),
                                       form.get("note"), user=g.user, major=form.get("major") in ("1", "true", True))
    return jsonify(ok=True, created=created, version=version.label, document=_doc_json(doc)), 201 if created else 200


@bp.post("/documents/<int:doc_id>/checkout")
@api_auth
def checkout(doc_id):
    doc = svc.get_document(doc_id)
    _need("edit", doc)
    svc.checkout(doc, g.user)
    return jsonify(ok=True, document=_doc_json(doc))


@bp.post("/documents/<int:doc_id>/checkin")
@api_auth
def checkin(doc_id):
    doc = svc.get_document(doc_id)
    _need("edit", doc)
    form = _body()
    version = svc.checkin(doc, g.user, request.files.get("file"), form.get("note"),
                          major=form.get("major") in ("1", "true", True), idempotency_key=form.get("idempotency_key"))
    return jsonify(ok=True, version=version.label if version else None, document=_doc_json(doc))


@bp.delete("/documents/<int:doc_id>")
@api_auth
def delete(doc_id):
    doc = svc.get_document(doc_id)
    _need("delete", doc)
    svc.trash_document(doc, g.user)
    return jsonify(ok=True)


@bp.patch("/documents/<int:doc_id>")
@api_auth
def update_document(doc_id):
    data = _body()
    doc = svc.get_document(doc_id)
    _need("edit", doc)
    if "name" in data:
        svc.rename_document(doc, data.get("name", ""), g.user)
    if "folder_id" in data:
        _need("delete", doc)
        folder = get_folder(data.get("folder_id"))
        _need("edit", folder)
        svc.move_document(doc, folder, g.user)
    if "document_type_id" in data or "tags" in data or any(key.startswith("meta_") for key in data):
        if "document_type_id" not in data and doc.document_type_id is not None:
            data.add("document_type_id", str(doc.document_type_id))
        if "tags" not in data:
            data.add("tags", ", ".join(tag.name for tag in doc.tags))
        existing_values = {f"meta_{value.field_id}": value.value for value in doc.metadata_values}
        for key, value in existing_values.items():
            if key not in data:
                data.add(key, value)
        document_type = meta_svc.document_type_from_form(data)
        values = meta_svc.values_from_form(data, doc.folder, document_type.id if document_type else None)
        tags = meta_svc.resolve_tags(meta_svc.parse_tags(data.get("tags")))
        svc.update_metadata(doc, document_type, values, tags, g.user)
    return jsonify(ok=True, document=_doc_json(doc))


@bp.post("/documents/<int:doc_id>/copy")
@api_auth
def copy_document_api(doc_id):
    data = _body()
    doc = svc.get_document(doc_id)
    _need("download", doc)
    destination = get_folder(data.get("folder_id"))
    _need("edit", destination)
    copied = svc.copy_document(doc, destination, g.user)
    return jsonify(ok=True, document=_doc_json(copied)), 201


@bp.post("/documents/<int:doc_id>/versions/<int:number>/restore")
@api_auth
def restore_document_version(doc_id, number):
    doc = svc.get_document(doc_id)
    _need("edit", doc)
    version = svc.get_version(doc, number)
    _need("view", version)
    form = _body()
    restored, created = svc.restore_version(
        doc, number, form.get("idempotency_key"), user=g.user,
        major=form.get("major") in ("1", "true", True), note=form.get("note")
    )
    return jsonify(ok=True, created=created, version=restored.label, document=_doc_json(doc)), \
        (201 if created else 200)


@bp.post("/documents/<int:doc_id>/restore")
@api_auth
def restore_document_api(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    if not can_manage_trashed(doc, g.user, resolver()):
        abort(403)
    svc.restore_document(doc)
    return jsonify(ok=True, document=_doc_json(doc))


@bp.delete("/documents/<int:doc_id>/purge")
@api_auth
def purge_document_api(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    if not can_manage_trashed(doc, g.user, resolver()):
        abort(403)
    svc.purge_document(doc, reason="permanently deleted through the API")
    return jsonify(ok=True)


@bp.post("/documents/<int:doc_id>/release")
@api_auth
def release_document(doc_id):
    """Discard a check-out. Records managers (or users who manage the document) may break another user's lock."""
    doc = svc.get_document(doc_id)
    _need("view", doc)
    force = doc.checked_out_by_id not in (None, g.user.id)
    if force and not (g.user.has("records") or resolver().can("manage", doc)):
        abort(403, description="Only records managers can release another user's check-out.")
    if not force:
        _need("edit", doc)
    svc.release(doc, g.user, force=force)
    return jsonify(ok=True, document=_doc_json(doc))


@bp.post("/documents/<int:doc_id>/links")
@api_auth
def add_document_link(doc_id):
    data = _body()
    doc = svc.get_document(doc_id)
    _need("view", doc)
    folder = get_folder(data.get("folder_id"))
    _need("edit", folder)
    link = svc.add_link(doc, folder, g.user)
    return jsonify(ok=True, link_id=link.id), 201


@bp.delete("/documents/links/<int:link_id>")
@api_auth
def remove_document_link(link_id):
    link = db.session.get(DocumentLink, link_id)
    if link is None:
        raise NotFound("Shortcut not found.")
    _need("edit", link.folder)
    svc.remove_link(link)
    return jsonify(ok=True)


@bp.get("/search")
@api_auth
def search():
    args = request.args
    params = search_svc.SearchParams(
        q=(args.get("q") or "").strip(),
        kind=args.get("kind") if args.get("kind") in ("document", "email") else "",
        types=[value for value in args.getlist("type") if value],
        folder_id=_query_integer("folder_id"),
        include_subfolders=args.get("include_subfolders", "1") != "0",
        tags=[value for value in args.getlist("tag") if value],
        document_type_id=_query_integer("document_type_id"),
        date_from=_search_date(args.get("date_from")),
        date_to=_search_date(args.get("date_to")),
        date_field="created" if args.get("date_field") == "created" else "modified",
        size_min=_search_size(args.get("size_min"), args.get("size_unit", "mb")),
        size_max=_search_size(args.get("size_max"), args.get("size_unit", "mb")),
        author_id=_query_integer("author_id"),
        meta_field_id=_query_integer("meta_field_id"),
        meta_value=(args.get("meta_value") or "").strip(),
        sort=args.get("sort") if args.get("sort") in ("relevance", "date", "name", "size") else "relevance",
    )
    hits, facets = search_svc.search(params, resolver(), g.user) if params.q or params.kind or params.types \
        or params.folder_id or params.tags or params.document_type_id or params.date_from or params.date_to \
        or params.size_min or params.size_max or params.author_id or params.meta_value else ([], {})
    page = max(1, args.get("page", 1, type=int))
    per_page = min(100, max(1, args.get("per_page", 20, type=int)))
    rows = hits[(page - 1) * per_page:page * per_page]
    return jsonify(results=[{
        "kind": hit.kind,
        "id": hit.obj.id,
        "title": hit.obj.name if hit.kind == "document" else hit.obj.subject,
        "snippet": hit.snippet_html,
        "date": hit.date.isoformat() + "Z" if hit.date else None,
        "size": hit.size,
        "type": hit.type_label,
        "document": _doc_json(hit.obj) if hit.kind == "document" else None,
        "email": _email_json(hit.obj) if hit.kind == "email" else None,
    } for hit in rows], facets=facets, total=len(hits), page=page, per_page=per_page)


def _search_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError("Search dates must use YYYY-MM-DD.")


def _search_size(value, unit):
    if not value:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError("Search sizes must be numeric.")
    if not math.isfinite(number) or number < 0:
        raise ValidationError("Search sizes cannot be negative.")
    scale = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}.get(unit.lower())
    if scale is None:
        raise ValidationError("Search size units must be kb, mb, or gb.")
    return int(number * scale)


@bp.get("/metadata/schema")
@api_auth
def metadata_schema():
    """Fields, document types and tags that apply to an upload into a folder (FR-MET-01, FR-MET-02)."""
    from ..services import settings
    folder = get_folder(request.args.get("folder_id"))
    _need("view", folder)
    return jsonify(fields=meta_svc.schema_json(folder, _query_integer("document_type_id")),
                   types=[{"id": t.id, "name": t.name, "description": t.description}
                          for t in meta_svc.document_types()],
                   tags=[t.name for t in meta_svc.all_tags()],
                   tags_controlled=bool(settings.get("tags_controlled")))


@bp.get("/directory")
@api_auth
def directory():
    """Active users and roles to pick from when sharing or granting permissions."""
    query = (request.args.get("q") or "").strip()
    stmt = select(User).where(User.is_active.is_(True)).order_by(func.lower(User.username))
    if query:
        like = f"%{query}%"
        stmt = stmt.where(User.username.ilike(like) | User.email.ilike(like) | User.display_name.ilike(like))
    users = db.session.execute(stmt.limit(50)).scalars().all()
    roles = db.session.execute(select(Role).order_by(Role.name)).scalars().all()
    return jsonify(users=[{"id": u.id, "username": u.username, "display_name": u.display_name, "label": u.label}
                          for u in users],
                   roles=[{"id": r.id, "name": r.name} for r in roles])


@bp.get("/emails")
@api_auth
def emails():
    _need_emails()
    args = request.args
    q = (args.get("q") or "").strip()
    state = args.get("state") or ""
    sort = args.get("sort") if args.get("sort") in ("received", "imported", "subject", "sender") else "imported"
    stmt = select(ArchivedEmail)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(ArchivedEmail.subject.ilike(like), ArchivedEmail.sender.ilike(like),
                              ArchivedEmail.recipients.ilike(like), ArchivedEmail.snippet.ilike(like)))
    if state:
        if state not in EMAIL_STATES:
            raise ValidationError("state must be one of: " + ", ".join(EMAIL_STATES))
        stmt = stmt.where(ArchivedEmail.gmail_state == state)
    if args.get("attachments") == "1":
        stmt = stmt.where(ArchivedEmail.attachment_count > 0)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    col = {"received": ArchivedEmail.received_at, "imported": ArchivedEmail.imported_at,
           "subject": func.lower(ArchivedEmail.subject), "sender": func.lower(ArchivedEmail.sender)}[sort]
    page = max(1, args.get("page", 1, type=int))
    per_page = min(100, max(1, args.get("per_page", 25, type=int)))
    rows = db.session.execute(stmt.order_by(col.asc() if sort in ("subject", "sender") else col.desc(),
                                            ArchivedEmail.id.desc())
                              .limit(per_page).offset((page - 1) * per_page)).scalars().all()
    return jsonify(emails=[_email_json(record) for record in rows], total=total,
                   page=page, per_page=per_page)


def _need_emails():
    if not g.user.has("emails"):
        abort(403, description="You do not have access to the email archive.")


@bp.get("/emails/<int:email_id>")
@api_auth
def email_detail(email_id):
    _need_emails()
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    return jsonify(email=_email_json(record))


@bp.get("/emails/<int:email_id>/body")
@api_auth
def email_body(email_id):
    _need_emails()
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    try:
        html_doc = email_svc.render_preview_html(
            record, lambda idx: url_for("api.email_inline_part", email_id=record.id, part_index=idx)
        )
    except FileNotFoundError:
        raise NotFound("The original message file is missing.")
    return jsonify(body_html=html_doc)


@bp.get("/emails/<int:email_id>/eml")
@api_auth
def email_eml(email_id):
    _need_emails()
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", record.subject or "").strip(" .")[:80] or "message"
    return stream_stored(record.storage_key, "message/rfc822", f"{name} [{record.gmail_message_id}].eml",
                         as_attachment=True, etag=record.sha256)


@bp.get("/emails/<int:email_id>/attachments/<int:part_index>")
@api_auth
def email_attachment(email_id, part_index):
    _need_emails()
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    if record.scan_status == "infected":
        abort(403, description="This email was flagged as containing malware; its attachments cannot be downloaded.")
    attachment = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index
    )).scalar_one_or_none()
    if attachment is None:
        raise NotFound("Attachment not found.")
    content = email_svc.attachment_bytes(record, part_index)
    if content is None:
        raise NotFound("Attachment not found in the original message.")
    filename = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", attachment.filename or "").strip(" .")[:200] or "attachment"
    return send_file(io.BytesIO(content), mimetype="application/octet-stream", as_attachment=True,
                     download_name=filename, max_age=0)


@bp.get("/emails/<int:email_id>/inline/<int:part_index>")
@api_auth
def email_inline_part(email_id, part_index):
    _need_emails()
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    attachment = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index
    )).scalar_one_or_none()
    if attachment is None or attachment.content_type not in email_svc.SAFE_INLINE_IMAGE_TYPES:
        raise NotFound("Inline image not found.")
    content = email_svc.attachment_bytes(record, part_index)
    if content is None:
        raise NotFound("Inline image not found in the original message.")
    response = send_file(io.BytesIO(content), mimetype=attachment.content_type, max_age=3600)
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


@bp.post("/documents/import-email-attachment")
@api_auth
def import_email_attachment():
    _need_emails()
    data = _body()
    email_id = _integer(data.get("email_id"), "email_id", 1)
    part_index = _integer(data.get("part_index"), "part_index", 0)
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        raise NotFound("Email not found.")
    if record.scan_status == "infected":
        raise ValidationError("This email was flagged as containing malware; its attachments cannot be imported.")
    attachment = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index
    )).scalar_one_or_none()
    if attachment is None:
        raise NotFound("Attachment not found.")
    content = email_svc.attachment_bytes(record, part_index)
    if not content:
        raise ValidationError("The attachment is empty or could not be read from the original message.")
    folder = get_folder(data.get("folder_id"))
    _need("edit", folder)
    file = FileStorage(stream=io.BytesIO(content), filename=attachment.filename)
    subject = (record.subject or "(no subject)")[:200]
    doc, created = svc.upload_document(
        file, folder, data.get("idempotency_key"),
        note=f"Imported from archived email: {subject}", user=g.user,
    )
    return jsonify(ok=True, created=created, document=_doc_json(doc)), (201 if created else 200)


@bp.get("/favourites")
@api_auth
def favourites():
    rows = db.session.execute(select(Favourite).where(Favourite.user_id == g.user.id)
                              .order_by(Favourite.created_at.desc())).scalars().all()
    result = []
    for favourite in rows:
        if favourite.target_type == "folder":
            item = db.session.get(Folder, favourite.target_id)
            permitted = item is not None and item.deleted_at is None and "view" in resolver().folder(item)
            serialized = _folder_json(item) if permitted else None
        else:
            item = db.session.get(Document, favourite.target_id)
            permitted = item is not None and item.deleted_at is None and "view" in resolver().document(item)
            serialized = _doc_json(item) if permitted else None
        if permitted:
            result.append({"type": favourite.target_type, "id": favourite.target_id, "item": serialized})
    return jsonify(favourites=result)


@bp.put("/favourites/<kind>/<int:target_id>")
@api_auth
def add_favourite(kind, target_id):
    if kind == "folder":
        item = get_folder(target_id)
        _need("view", item)
    elif kind == "document":
        item = svc.get_document(target_id)
        _need("view", item)
    else:
        raise ValidationError("Favourite type must be folder or document.")
    existing = db.session.execute(select(Favourite).where(
        Favourite.user_id == g.user.id, Favourite.target_type == kind, Favourite.target_id == target_id
    )).scalar_one_or_none()
    if existing is None:
        db.session.add(Favourite(user_id=g.user.id, target_type=kind, target_id=target_id))
        db.session.commit()
    return jsonify(ok=True, favourite=True)


@bp.delete("/favourites/<kind>/<int:target_id>")
@api_auth
def remove_favourite(kind, target_id):
    if kind not in ("folder", "document"):
        raise ValidationError("Favourite type must be folder or document.")
    entry = db.session.execute(select(Favourite).where(
        Favourite.user_id == g.user.id, Favourite.target_type == kind, Favourite.target_id == target_id
    )).scalar_one_or_none()
    if entry is not None:
        db.session.delete(entry)
        db.session.commit()
    return jsonify(ok=True, favourite=False)


@bp.get("/shared")
@api_auth
def shared_items():
    entries = db.session.execute(select(AccessEntry).where(
        AccessEntry.principal_type == "user", AccessEntry.principal_id == g.user.id,
        AccessEntry.target_type.in_(("folder", "document"))
    ).order_by(AccessEntry.created_at.desc())).scalars().all()
    result = []
    for entry in entries:
        if entry.created_by_id == g.user.id:
            continue
        model = Folder if entry.target_type == "folder" else Document
        item = db.session.get(model, entry.target_id)
        if item is None or item.deleted_at is not None or item.created_by_id == g.user.id \
                or getattr(item, "id", None) == g.user.home_folder_id or "view" not in resolver().perms(item):
            continue
        sharer = db.session.get(User, entry.created_by_id) if entry.created_by_id else None
        result.append({
            "type": entry.target_type,
            "id": item.id,
            "shared_by": sharer.label if sharer else None,
            "permissions": sorted(resolver().perms(item)),
            "item": _folder_json(item) if entry.target_type == "folder" else _doc_json(item),
        })
    return jsonify(items=result)


@bp.get("/recycle-bin")
@api_auth
def recycle_bin():
    folders, documents = recycle_bin_items(g.user, resolver())
    return jsonify(
        folders=[_folder_json(item) for item in folders],
        documents=[_doc_json(item) for item in documents],
    )


@bp.delete("/recycle-bin")
@api_auth
def empty_recycle_bin():
    folders, documents = recycle_bin_items(g.user, resolver())
    for folder in folders:
        if db.session.get(Folder, folder.id) is not None:
            purge_folder(folder, reason="recycle bin emptied through the API")
    for document in documents:
        if db.session.get(Document, document.id) is not None:
            svc.purge_document(document, reason="recycle bin emptied through the API")
    db.session.commit()
    return jsonify(ok=True, purged_folders=len(folders), purged_documents=len(documents))


@bp.get("/permissions/<kind>/<int:obj_id>")
@bp.get("/permissions/<kind>/<int:obj_id>/<int:number>")
@api_auth
def permissions(kind, obj_id, number=None):
    target = _target(kind, obj_id, number)
    _need("view", target)
    entries = []
    for entry in access_svc.entries_for(target):
        entries.append({
            "id": entry.id,
            "principal_type": entry.principal_type,
            "principal_id": entry.principal_id,
            "principal": access_svc.principal_label(entry),
            "permissions": sorted(entry.permission_set),
            "created_by_id": entry.created_by_id,
        })
    return jsonify(permissions=sorted(resolver().perms(target)),
                   can_manage=resolver().can("manage", target),
                   inherit_permissions=getattr(target, "inherit_permissions", None),
                   entries=entries)


@bp.post("/permissions/<kind>/<int:obj_id>/grant")
@bp.post("/permissions/<kind>/<int:obj_id>/<int:number>/grant")
@api_auth
def grant_permissions(kind, obj_id, number=None):
    data = _body()
    target = _target(kind, obj_id, number)
    _need("manage", target)
    principal_type = data.get("principal_type")
    principal_id = _integer(data.get("principal_id"), "principal_id", 1)
    permissions_value = data.getlist("permissions")
    if len(permissions_value) == 1 and isinstance(permissions_value[0], str):
        permissions_value = [part.strip() for part in permissions_value[0].split(",") if part.strip()]
    perms = [perm for perm in CONTENT_PERMS if perm in permissions_value]
    if target is None and principal_type == "user" and principal_id == g.user.id and "manage" not in perms \
            and not g.user.is_admin:
        raise ValidationError("You cannot remove your own permission to manage the repository.")
    access_svc.set_entry(target, principal_type, principal_id, perms, g.user)
    return jsonify(ok=True, permissions=sorted(resolver().perms(target)))


@bp.delete("/permissions/<kind>/<int:obj_id>/entries/<int:entry_id>")
@bp.delete("/permissions/<kind>/<int:obj_id>/<int:number>/entries/<int:entry_id>")
@api_auth
def remove_permission(kind, obj_id, entry_id, number=None):
    target = _target(kind, obj_id, number)
    _need("manage", target)
    access_svc.remove_entry(target, entry_id)
    return jsonify(ok=True)


@bp.patch("/permissions/<kind>/<int:obj_id>/inheritance")
@bp.patch("/permissions/<kind>/<int:obj_id>/<int:number>/inheritance")
@api_auth
def update_inheritance(kind, obj_id, number=None):
    data = _body()
    target = _target(kind, obj_id, number)
    _need("manage", target)
    inherit = str(data.get("inherit", "")).lower() in ("1", "true", "yes")
    copy_inherited = str(data.get("copy", "1")).lower() not in ("0", "false", "no")
    access_svc.set_inheritance(target, inherit, copy_inherited=copy_inherited, actor=g.user)
    return jsonify(ok=True, inherit_permissions=inherit)


@bp.post("/permissions/<kind>/<int:obj_id>/share")
@bp.post("/permissions/<kind>/<int:obj_id>/<int:number>/share")
@api_auth
def share_item(kind, obj_id, number=None):
    data = _body()
    target = _target(kind, obj_id, number)
    actor_perms = resolver().perms(target)
    if "share" not in actor_perms and "manage" not in actor_perms:
        abort(403)
    principal_type = data.get("principal_type")
    principal_id = _integer(data.get("principal_id"), "principal_id", 1)
    if data.get("level"):
        if principal_type != "user":
            raise ValidationError("Choose a user when sharing by access level.")
        access_svc.share_level(target, actor_perms, principal_id, data.get("level"), g.user)
    else:
        access_svc.share(target, actor_perms, principal_type, principal_id, data.getlist("permissions"), g.user)
    return jsonify(ok=True)


@bp.get("/account")
@api_auth
def account_profile():
    tokens = db.session.execute(select(ApiToken).where(
        ApiToken.user_id == g.user.id, ApiToken.revoked_at.is_(None)
    ).order_by(ApiToken.created_at.desc())).scalars().all()
    devices = db.session.execute(select(UserDevice).where(UserDevice.user_id == g.user.id)
                                 .order_by(UserDevice.last_seen.desc())).scalars().all()
    return jsonify(
        user={"id": g.user.id, "username": g.user.username, "display_name": g.user.display_name,
              "email": g.user.email, "roles": [role.name for role in g.user.roles],
              "mfa_enabled": g.user.mfa_enabled},
        api_tokens=[{"id": token.id, "name": token.name, "prefix": token.prefix,
                     "created_at": token.created_at.isoformat() + "Z",
                     "expires_at": token.expires_at.isoformat() + "Z" if token.expires_at else None,
                     "last_used_at": token.last_used_at.isoformat() + "Z" if token.last_used_at else None}
                    for token in tokens],
        devices=[{"id": item.id, "platform": item.platform, "status": item.status,
                  "first_seen": item.first_seen.isoformat() + "Z", "last_seen": item.last_seen.isoformat() + "Z"}
                 for item in devices],
    )


@bp.post("/account/tokens")
@api_auth
def create_api_token():
    data = _body()
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("A token name is required.")
    days = _integer(data.get("days"), "days", 1) if data.get("days") else None
    if days is not None and days > 3650:
        raise ValidationError("Token expiry cannot exceed 3650 days.")
    from ..security.auth import new_api_token
    raw, token = new_api_token(g.user, name, days)
    return jsonify(ok=True, token=raw, id=token.id, name=token.name), 201


@bp.delete("/account/tokens/<int:token_id>")
@api_auth
def revoke_api_token(token_id):
    token = db.session.get(ApiToken, token_id)
    if token is None or token.user_id != g.user.id:
        raise NotFound("Token not found.")
    token.revoked_at = utcnow()
    log_activity("security", "token_revoked", f"API token '{token.name}' revoked", g.user.id)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/recent")
@api_auth
def recent_documents():
    from ..models import RecentItem
    visible = resolver().visible_documents_clause()
    viewed = db.session.execute(
        select(RecentItem).join(Document, RecentItem.document_id == Document.id)
        .where(RecentItem.user_id == g.user.id, Document.deleted_at.is_(None), visible)
        .order_by(RecentItem.viewed_at.desc()).limit(100)
    ).scalars().all()
    return jsonify(viewed=[{"viewed_at": item.viewed_at.isoformat() + "Z", "document": _doc_json(item.document)}
                           for item in viewed])


@bp.get("/versions")
@api_auth
def recent_versions():
    from ..models import DocumentVersion
    visible = resolver().visible_documents_clause()
    versions = db.session.execute(
        select(DocumentVersion).join(Document, DocumentVersion.document_id == Document.id)
        .where(Document.deleted_at.is_(None), visible)
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .limit(min(200, max(1, request.args.get("limit", 50, type=int))))
    ).scalars().all()
    return jsonify(versions=[{
        "document_id": version.document_id, "document_name": version.document.name,
        "number": version.version_number, "label": version.label, "size": version.size,
        "sha256": version.sha256, "note": version.note,
        "created_at": version.created_at.isoformat() + "Z",
        "created_by": version.created_by.username if version.created_by else None,
    } for version in versions if "view" in resolver().version(version)])


@bp.get("/activity")
@api_auth
def activity():
    from ..models import ActivityLog
    if not g.user.has("audit"):
        abort(403)
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 50, type=int)))
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.limit(per_page).offset((page - 1) * per_page)).scalars().all()
    return jsonify(events=[{
        "id": item.id, "category": item.category, "action": item.action, "message": item.message,
        "entity_id": item.entity_id, "user_id": item.user_id, "user_name": item.user_name,
        "created_at": item.created_at.isoformat() + "Z",
    } for item in rows], total=total, page=page, per_page=per_page)


@bp.get("/dashboard")
@api_auth
def dashboard():
    res = resolver()
    visible = res.visible_documents_clause()
    active = Document.deleted_at.is_(None)
    can_emails = g.user.has("emails")
    return jsonify(stats={
        "documents": db.session.execute(select(func.count(Document.id)).where(active, visible)).scalar(),
        "folders": len(res.visible_folder_ids()),
        "emails": db.session.execute(select(func.count(ArchivedEmail.id))).scalar() if can_emails else None,
        "versions": db.session.execute(select(func.count(DocumentVersion.id))
                                       .join(Document, DocumentVersion.document_id == Document.id)
                                       .where(active, visible)).scalar(),
    })


@bp.patch("/account/password")
@api_auth
def change_password():
    from werkzeug.security import check_password_hash
    data = _body()
    if g.user.auth_source != "local":
        raise ValidationError("Your password is managed by your organisation's identity provider.")
    if not check_password_hash(g.user.password_hash or "", data.get("current", "")):
        raise ValidationError("The current password is incorrect.")
    new_password = data.get("new") or ""
    if new_password != (data.get("new2") or ""):
        raise ValidationError("The new passwords do not match.")
    check_password_policy(new_password)
    g.user.password_hash = hash_password(new_password)
    log_activity("security", "password_changed", f"'{g.user.username}' changed their password", g.user.id)
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/account/mfa")
@api_auth
def disable_mfa():
    from ..services import settings
    if settings.get("mfa_required") and g.user.auth_source in ("local", "ldap"):
        raise ValidationError("Your organisation requires multi-factor authentication.")
    secret = mfa.unseal(g.user.mfa_secret)
    if not mfa.verify_totp(secret, (_body().get("code") or "").strip()):
        raise ValidationError("Enter a current authenticator code to turn MFA off.")
    g.user.mfa_enabled = False
    g.user.mfa_secret = None
    g.user.mfa_recovery_json = "[]"
    log_activity("security", "mfa_disabled", f"'{g.user.username}' disabled multi-factor authentication", g.user.id)
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/account/devices/<int:device_id>")
@api_auth
def remove_account_device(device_id):
    device = db.session.get(UserDevice, device_id)
    if device is None or device.user_id != g.user.id:
        raise NotFound("Device not found.")
    if device.id == session.get("device_id"):
        raise ValidationError("You cannot remove the device you are using.")
    db.session.delete(device)
    db.session.commit()
    return jsonify(ok=True)


def _admin_user():
    if not g.user.has("admin"):
        abort(403, description="Administrator access is required.")


def _user_json(user):
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "auth_source": user.auth_source,
        "is_active": user.is_active,
        "mfa_enabled": user.mfa_enabled,
        "roles": [{"id": role.id, "name": role.name} for role in user.roles],
        "created_at": user.created_at.isoformat() + "Z",
        "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
    }


@bp.get("/admin/users")
@api_auth
def admin_users():
    _admin_user()
    query = (request.args.get("q") or "").strip()
    stmt = select(User).order_by(func.lower(User.username))
    if query:
        like = f"%{query}%"
        stmt = stmt.where(User.username.ilike(like) | User.email.ilike(like) | User.display_name.ilike(like))
    rows = db.session.execute(stmt).scalars().all()
    return jsonify(users=[_user_json(user) for user in rows])


@bp.post("/admin/users")
@api_auth
def admin_create_user():
    _admin_user()
    data = _body()
    new_user = User()
    apply_user_data(new_user, data, creating=True)
    db.session.add(new_user)
    db.session.flush()
    log_activity("admin", "user_created", f"User '{new_user.username}' created", new_user.id)
    db.session.commit()
    return jsonify(ok=True, user=_user_json(new_user)), 201


@bp.get("/admin/users/<int:user_id>")
@api_auth
def admin_get_user(user_id):
    _admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    devices = db.session.execute(select(UserDevice).where(UserDevice.user_id == user.id)
                                 .order_by(UserDevice.last_seen.desc())).scalars().all()
    return jsonify(user=_user_json(user), devices=[
        {"id": device.id, "platform": device.platform, "status": device.status,
         "first_seen": device.first_seen.isoformat() + "Z", "last_seen": device.last_seen.isoformat() + "Z"}
        for device in devices
    ])


@bp.patch("/admin/users/<int:user_id>")
@api_auth
def admin_update_user(user_id):
    _admin_user()
    data = _body()
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    action = data.get("action", "save")
    if action == "reset_mfa":
        user.mfa_enabled, user.mfa_secret, user.mfa_recovery_json = False, None, "[]"
    elif action == "unlock":
        user.failed_logins, user.locked_until = 0, None
    elif action == "save":
        was_admin = user.is_admin
        defaults = {
            "display_name": user.display_name,
            "email": user.email or "",
            "auth_source": user.auth_source,
            "is_active": "1" if user.is_active else "0",
        }
        for key, value in defaults.items():
            if key not in data:
                data.add(key, value)
        if "roles" not in data:
            data.setlist("roles", [str(role.id) for role in user.roles])
        apply_user_data(user, data, creating=False)
        db.session.flush()
        if was_admin and (not user.is_admin or not user.is_active):
            admins = db.session.execute(select(User).where(User.is_active.is_(True))).scalars().all()
            if not any(other.id != user.id and other.is_admin for other in admins):
                raise ValidationError("At least one active administrator must remain.")
    else:
        raise ValidationError("Unknown user action.")
    log_activity("admin", "user_updated", f"User '{user.username}' updated", user.id)
    db.session.commit()
    reset_resolver()
    return jsonify(ok=True, user=_user_json(user))


def _role_json(role):
    entry = db.session.execute(select(AccessEntry).where(
        AccessEntry.target_type == "repository", AccessEntry.target_id.is_(None),
        AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id
    )).scalar_one_or_none()
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "capabilities": sorted(role.capability_set),
        "permissions": sorted(entry.permission_set) if entry else [],
        "user_count": len(role.users),
    }


@bp.get("/admin/roles")
@api_auth
def admin_roles():
    _admin_user()
    roles = db.session.execute(select(Role).order_by(Role.name)).scalars().all()
    return jsonify(roles=[_role_json(role) for role in roles],
                   available_capabilities=sorted(SYSTEM_CAPS),
                   available_permissions=sorted(CONTENT_PERMS))


@bp.post("/admin/roles")
@api_auth
def admin_create_role():
    _admin_user()
    data = _body()
    name = (data.get("name") or "").strip()[:80]
    if not name:
        raise ValidationError("A role name is required.")
    if db.session.execute(select(Role).where(func.lower(Role.name) == name.lower())).first():
        raise ValidationError(f"A role named '{name}' already exists.")
    role = Role(name=name, description=(data.get("description") or "").strip()[:500],
                capabilities=",".join(cap for cap in SYSTEM_CAPS if cap in data.getlist("capabilities")))
    db.session.add(role)
    db.session.flush()
    perms = [perm for perm in CONTENT_PERMS if perm in data.getlist("permissions")]
    if perms:
        db.session.add(AccessEntry(target_type="repository", target_id=None, principal_type="role",
                                   principal_id=role.id, permissions=parse_perms(perms)))
    log_activity("admin", "role_saved", f"Role '{role.name}' created", role.id)
    db.session.commit()
    return jsonify(ok=True, role=_role_json(role)), 201


@bp.patch("/admin/roles/<int:role_id>")
@api_auth
def admin_update_role(role_id):
    _admin_user()
    data = _body()
    role = db.session.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found.")
    name = (data.get("name", role.name) or "").strip()[:80]
    if role.is_system and name != role.name:
        raise ValidationError("Predefined roles cannot be renamed.")
    clash = db.session.execute(select(Role).where(func.lower(Role.name) == name.lower(),
                                                   Role.id != role.id)).first()
    if clash:
        raise ValidationError(f"A role named '{name}' already exists.")
    capabilities = data.getlist("capabilities") if "capabilities" in data else list(role.capability_set)
    if role.is_system and role.name == "Admin" and "admin" not in capabilities:
        raise ValidationError("The Admin role must keep the administration capability.")
    role.name = name
    role.description = (data.get("description", role.description) or "").strip()[:500]
    role.capabilities = ",".join(cap for cap in SYSTEM_CAPS if cap in capabilities)
    if "permissions" in data:
        permissions_value = data.getlist("permissions")
        entry = db.session.execute(select(AccessEntry).where(
            AccessEntry.target_type == "repository", AccessEntry.target_id.is_(None),
            AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id
        )).scalar_one_or_none()
        perms = [perm for perm in CONTENT_PERMS if perm in permissions_value]
        if perms:
            if entry is None:
                entry = AccessEntry(target_type="repository", target_id=None, principal_type="role",
                                    principal_id=role.id)
                db.session.add(entry)
            entry.permissions = parse_perms(perms)
        elif entry is not None:
            db.session.delete(entry)
    log_activity("admin", "role_saved", f"Role '{role.name}' updated", role.id)
    db.session.commit()
    reset_resolver()
    return jsonify(ok=True, role=_role_json(role))


@bp.delete("/admin/roles/<int:role_id>")
@api_auth
def admin_delete_role(role_id):
    _admin_user()
    role = db.session.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found.")
    if role.is_system:
        raise ValidationError("Predefined roles cannot be deleted.")
    db.session.query(AccessEntry).filter(
        AccessEntry.principal_type == "role", AccessEntry.principal_id == role.id
    ).delete(synchronize_session=False)
    name = role.name
    db.session.delete(role)
    log_activity("admin", "role_deleted", f"Role '{name}' deleted")
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/admin/settings")
@api_auth
def admin_settings():
    _admin_user()
    from ..services import settings
    return jsonify(settings=settings.all_settings())


def _settings_form(payload):
    """Turn a (partial) JSON settings object into the admin form, keeping every value not supplied."""
    from ..services import settings
    current = settings.all_settings()
    unknown = sorted(set(payload) - set(current))
    if unknown:
        raise ValidationError("Unknown settings: " + ", ".join(unknown))
    form = MultiDict()
    for key, value in {**current, **payload}.items():
        if key == "blocked_platforms":
            form.setlist(key, [str(item) for item in (value or [])])
        elif isinstance(value, bool):
            form[key] = "1" if value else "0"
        elif isinstance(value, dict):
            form[key] = "\n".join(f"{k} = {v}" for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            form[key] = "\n".join(str(item) for item in value)
        elif value is None:
            form[key] = ""
        else:
            form[key] = str(value)
    return form


@bp.put("/admin/settings")
@bp.patch("/admin/settings")
@api_auth
def admin_update_settings():
    _admin_user()
    from ..services import settings
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Send the settings to change as a JSON object.")
    settings.update_from_form(_settings_form(payload))
    log_activity("admin", "settings_changed", "System settings updated through the API")
    db.session.commit()
    settings.clear_cache()
    return jsonify(ok=True, settings=settings.all_settings())


@bp.get("/admin/metadata")
@api_auth
def admin_metadata():
    _admin_user()
    from ..services import metadata as metadata_service
    return jsonify(
        types=[{"id": item.id, "name": item.name, "description": item.description}
               for item in metadata_service.document_types()],
        fields=[{"id": item.id, "name": item.name, "label": item.label, "field_type": item.field_type,
                 "required": item.required, "help_text": item.help_text, "sort_order": item.sort_order,
                 "vocabulary_id": item.vocabulary_id, "document_type_id": item.document_type_id,
                 "folder_id": item.folder_id}
                for item in metadata_service.fields()],
        vocabularies=[{"id": item.id, "name": item.name, "description": item.description,
                       "terms": [term.value for term in item.terms]}
                      for item in metadata_service.vocabularies()],
        tags=[{"id": item.id, "name": item.name} for item in metadata_service.all_tags()],
    )


@bp.post("/admin/metadata/types")
@api_auth
def admin_save_document_type():
    _admin_user()
    data = _body()
    item_id = _integer(data.get("id"), "id", 1) if data.get("id") else None
    item = db.session.get(DocumentType, item_id) if item_id else DocumentType()
    if item is None:
        raise NotFound("Document type not found.")
    name = (data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError("A document type name is required.")
    clash = db.session.execute(select(DocumentType).where(func.lower(DocumentType.name) == name.lower())).scalar_one_or_none()
    if clash is not None and clash is not item:
        raise ValidationError(f"A document type named '{name}' already exists.")
    item.name, item.description = name, (data.get("description") or "").strip()[:500]
    db.session.add(item)
    db.session.commit()
    return jsonify(ok=True, id=item.id, name=item.name), (200 if item_id else 201)


@bp.post("/admin/metadata/fields")
@api_auth
def admin_save_metadata_field():
    _admin_user()
    from ..services import metadata as metadata_service
    data = _body()
    item_id = _integer(data.get("id"), "id", 1) if data.get("id") else None
    field = db.session.get(MetadataField, item_id) if item_id else None
    if item_id and field is None:
        raise NotFound("Metadata field not found.")
    field = metadata_service.save_field(field, data)
    db.session.commit()
    return jsonify(ok=True, id=field.id, name=field.name, label=field.label), (200 if item_id else 201)


@bp.post("/admin/metadata/vocabularies")
@api_auth
def admin_save_vocabulary():
    _admin_user()
    from ..services import metadata as metadata_service
    data = _body()
    item_id = _integer(data.get("id"), "id", 1) if data.get("id") else None
    vocabulary = db.session.get(Vocabulary, item_id) if item_id else None
    if item_id and vocabulary is None:
        raise NotFound("Vocabulary not found.")
    vocabulary = metadata_service.save_vocabulary(
        vocabulary, data.get("name"), data.get("description"), data.get("terms")
    )
    db.session.commit()
    return jsonify(ok=True, id=vocabulary.id, name=vocabulary.name), (200 if item_id else 201)


@bp.post("/admin/metadata/tags")
@api_auth
def admin_save_tags():
    _admin_user()
    data = _body()
    if data.get("action") == "delete":
        tag = db.session.get(Tag, _integer(data.get("id"), "id", 1))
        if tag is not None:
            db.session.delete(tag)
            db.session.commit()
        return jsonify(ok=True)
    names = meta_svc.parse_tags(data.get("names"))
    for name in names:
        if not db.session.execute(select(Tag).where(func.lower(Tag.name) == name.lower())).first():
            db.session.add(Tag(name=name))
    db.session.commit()
    return jsonify(ok=True, tags=names)


@bp.delete("/admin/metadata/types/<int:type_id>")
@api_auth
def admin_delete_document_type(type_id):
    _admin_user()
    item = db.session.get(DocumentType, type_id)
    if item is None:
        raise NotFound("Document type not found.")
    db.session.delete(item)
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/admin/metadata/fields/<int:field_id>")
@api_auth
def admin_delete_metadata_field(field_id):
    _admin_user()
    field = db.session.get(MetadataField, field_id)
    if field is None:
        raise NotFound("Metadata field not found.")
    db.session.delete(field)
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/admin/metadata/vocabularies/<int:vocabulary_id>")
@api_auth
def admin_delete_vocabulary(vocabulary_id):
    _admin_user()
    item = db.session.get(Vocabulary, vocabulary_id)
    if item is None:
        raise NotFound("Vocabulary not found.")
    db.session.delete(item)
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/admin/metadata/tags/<int:tag_id>")
@api_auth
def admin_delete_tag(tag_id):
    _admin_user()
    item = db.session.get(Tag, tag_id)
    if item is None:
        raise NotFound("Tag not found.")
    db.session.delete(item)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/admin/devices")
@api_auth
def admin_devices():
    _admin_user()
    rows = db.session.execute(select(UserDevice).order_by(UserDevice.status != "pending",
                                                           UserDevice.last_seen.desc())).scalars().all()
    return jsonify(devices=[{"id": device.id, "user_id": device.user_id, "username": device.user.username,
                             "platform": device.platform, "status": device.status,
                             "first_seen": device.first_seen.isoformat() + "Z",
                             "last_seen": device.last_seen.isoformat() + "Z"}
                            for device in rows])


@bp.patch("/admin/devices/<int:device_id>")
@api_auth
def admin_update_device(device_id):
    _admin_user()
    data = _body()
    device = db.session.get(UserDevice, device_id)
    if device is None:
        raise NotFound("Device not found.")
    action = data.get("action")
    if action == "remove":
        db.session.delete(device)
    elif action in ("approved", "blocked"):
        device.status = action
    else:
        raise ValidationError("Action must be approved, blocked, or remove.")
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/admin/maintenance")
@api_auth
def run_maintenance_api():
    _admin_user()
    from ..services.retention import run_maintenance
    result = run_maintenance()
    log_activity("admin", "maintenance", f"Maintenance run: {result}")
    db.session.commit()
    return jsonify(ok=True, result=result)


@bp.post("/admin/reindex")
@api_auth
def reindex_api():
    _admin_user()
    from ..services.search import reindex_all
    count = reindex_all()
    log_activity("admin", "reindex", f"Search index rebuilt ({count} items)")
    db.session.commit()
    return jsonify(ok=True, indexed=count)


@bp.get("/admin/status")
@api_auth
def admin_status():
    _admin_user()
    from ..security import ldap_auth, saml
    from ..security.malware import engine_name
    storage = current_app.extensions["dms_storage"]
    return jsonify(
        users=db.session.execute(select(func.count(User.id))).scalar(),
        pending_devices=db.session.execute(select(func.count(UserDevice.id)).where(
            UserDevice.status == "pending"
        )).scalar(),
        encryption={"enabled": storage.encrypted,
                    "key_id": storage.ring.active_key_id if storage.ring else None},
        malware_scanner=engine_name(),
        ldap_enabled=ldap_auth.enabled(),
        saml_enabled=saml.enabled(),
        https_required=current_app.config.get("FORCE_HTTPS"),
        secure_cookie=current_app.config.get("SESSION_COOKIE_SECURE"),
    )


@bp.get("/gmail/status")
@api_auth
def gmail_status():
    if not g.user.has("emails") and not g.user.has("admin"):
        abort(403)
    from .gmail import _status_payload
    return jsonify(status=_status_payload())


@bp.get("/gmail/connect")
@api_auth
def gmail_connect():
    _admin_user()
    from ..gmail import auth as gmail_auth
    if not gmail_auth.credentials_configured():
        return jsonify(ok=False, error="Gmail OAuth client credentials are not configured.",
                       code="not_configured"), 409
    callback = request.url_root.rstrip("/") + "/gmail/oauth2callback"
    url, state, verifier = gmail_auth.start_authorization(callback)
    session["oauth_state"], session["oauth_verifier"] = state, verifier
    session["gmail_api_connect"] = True
    return jsonify(authorization_url=url)


@bp.post("/gmail/disconnect")
@api_auth
def gmail_disconnect():
    _admin_user()
    from ..gmail import auth as gmail_auth
    from ..gmail.sync import active_account
    account = active_account()
    gmail_auth.disconnect()
    if account:
        account.is_active = False
        account.last_sync_status = "disconnected"
        log_activity("gmail", "disconnected", f"Gmail account {account.email} disconnected")
        db.session.commit()
    return jsonify(ok=True)


@bp.post("/gmail/sync")
@api_auth
def gmail_sync():
    if not g.user.has("emails") and not g.user.has("admin"):
        abort(403)
    from .. import ensure_scheduler
    from ..gmail.sync import run_sync
    scheduler = ensure_scheduler(current_app._get_current_object())
    if scheduler is not None:
        scheduler.trigger()
        return jsonify(ok=True, status="started"), 202
    result = run_sync(trigger="manual")
    status = 200 if result.status in ("ok", "partial") else (409 if result.status == "busy" else 502)
    return jsonify(ok=status == 200, result=result.as_dict()), status


@bp.post("/gmail/retry")
@api_auth
def gmail_retry():
    _admin_user()
    from .. import ensure_scheduler
    from ..gmail.sync import retry_failed
    count = retry_failed()
    scheduler = current_app.extensions.get("gmail_scheduler") or ensure_scheduler(current_app._get_current_object())
    if scheduler:
        scheduler.trigger()
    return jsonify(ok=True, queued=count), 202
