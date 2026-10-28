"""Sign-in, sessions and access policies (FR-SEC-01, FR-SEC-03, FR-SEC-05).

* Local accounts (scrypt password hashes) with lockout after repeated failures.
* LDAP / Active Directory, OpenID Connect (Entra ID, Google, ...) and SAML 2.0 sign-in
  with automatic provisioning and group -> role mapping.
* TOTP multi-factor authentication (optional per user, or required by policy).
* Session idle timeout and maximum session length.
* IP allowlist and device policy (new-device approval, blocked platforms).
"""
import hashlib
import ipaddress
import logging
import secrets
import time
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import ApiToken, Role, User, UserDevice, utcnow
from ..services import settings
from ..services.activity import log_activity
from ..validation import ValidationError
from .permissions import PREDEFINED_ROLES, reset_resolver

log = logging.getLogger(__name__)

DEVICE_COOKIE = "dms_device"
PUBLIC_ENDPOINTS = {"static", "auth.login", "auth.logout", "auth.setup", "auth.register", "auth.mfa", "auth.oidc_login",
                    "auth.oidc_callback", "auth.saml_login", "auth.saml_acs", "auth.saml_complete", "auth.saml_metadata",
                    "auth.device_pending", "auth.blocked", "auth_callbacks.oidc_login",
                    "auth_callbacks.oidc_callback", "auth_callbacks.saml_login", "auth_callbacks.saml_acs",
                    "auth_callbacks.saml_complete", "auth_callbacks.saml_metadata", "gmail_callback.callback"}
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class AuthError(Exception):
    pass


# --------------------------------------------------------------------------- passwords

def hash_password(password):
    return generate_password_hash(password)


def check_password_policy(password):
    if len(password or "") < 12:
        raise ValidationError("Passwords must be at least 12 characters long.")
    classes = sum([any(c.islower() for c in password), any(c.isupper() for c in password),
                   any(c.isdigit() for c in password), any(not c.isalnum() for c in password)])
    if classes < 3:
        raise ValidationError("Passwords must contain at least three of: lower-case, upper-case, digits, symbols.")


def find_user(username):
    username = (username or "").strip().lower()
    if not username:
        return None
    return db.session.execute(select(User).where(
        (func.lower(User.username) == username) | (func.lower(User.email) == username))).scalars().first()


def verify_local(username, password):
    """Check a local password. Returns the user or None. Applies lockout."""
    user = find_user(username)
    if user is None or user.auth_source != "local" or not user.password_hash:
        check_password_hash(_DUMMY_HASH, password or "")  # similar timing for unknown users
        return None
    if user.locked_until and user.locked_until > utcnow():
        raise AuthError("This account is temporarily locked after repeated failed sign-ins. Try again later.")
    if check_password_hash(user.password_hash, password or ""):
        user.failed_logins = 0
        user.locked_until = None
        return user
    register_failure(user)
    return None


def register_failure(user):
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= settings.get("lockout_threshold"):
        user.locked_until = utcnow() + timedelta(minutes=settings.get("lockout_minutes"))
        user.failed_logins = 0
        log_activity("security", "account_locked", f"Account '{user.username}' locked after failed sign-ins",
                     user.id)
    db.session.commit()


# --------------------------------------------------------------------------- roles

def ensure_predefined_roles():
    """Create the predefined roles and their repository-level permissions (idempotent)."""
    from ..models import AccessEntry
    from .permissions import parse_perms
    created = False
    for name, (caps, repo_perms, desc) in PREDEFINED_ROLES.items():
        role = db.session.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
        if role is None:
            role = Role(name=name, description=desc, is_system=True, capabilities=",".join(sorted(caps)))
            db.session.add(role)
            db.session.flush()
            db.session.add(AccessEntry(target_type="repository", target_id=None, principal_type="role",
                                       principal_id=role.id, permissions=parse_perms(repo_perms)))
            created = True
    if created:
        db.session.commit()


def role_by_name(name):
    return db.session.execute(select(Role).where(func.lower(Role.name) == (name or "").strip().lower())
                              ).scalar_one_or_none()


def roles_for_groups(groups):
    mapping = settings.get("group_role_map") or {}
    if not mapping or not groups:
        return []
    wanted = {g.lower() for g in groups}
    out = []
    for group, role_name in mapping.items():
        if group.lower() in wanted:
            role = role_by_name(role_name)
            if role and role not in out:
                out.append(role)
    return out


def provision_external(source, external_id, email, name, groups):
    """Find or create the user for an external sign-in (LDAP/OIDC/SAML)."""
    email = (email or "").strip().lower() or None
    user = db.session.execute(select(User).where(User.auth_source == source, User.external_id == external_id)
                              ).scalars().first()
    if user is None and email:
        candidate = db.session.execute(select(User).where(func.lower(User.email) == email)).scalars().first()
        # Link only to accounts pre-created for this sign-in method (never take over a local account).
        if candidate is not None and candidate.auth_source == source:
            user = candidate
    mapped = roles_for_groups(groups)
    if user is None:
        if not settings.get("sso_auto_provision"):
            raise AuthError("Your account has not been set up in the document management system. "
                            "Contact an administrator.")
        username = (email or external_id).lower()[:150]
        if find_user(username) is not None:
            username = f"{username}#{source}"[:150]
        user = User(username=username, email=email, display_name=(name or "")[:200], auth_source=source,
                    external_id=external_id)
        default = role_by_name(settings.get("sso_default_role"))
        user.roles = mapped or ([default] if default else [])
        db.session.add(user)
        db.session.flush()
        log_activity("admin", "user_provisioned", f"User '{username}' created on first {source} sign-in", user.id)
    else:
        user.external_id = external_id
        if email:
            user.email = email
        if name:
            user.display_name = name[:200]
        if mapped:
            user.roles = mapped
    if not user.is_active:
        raise AuthError("Your account is disabled.")
    return user


# --------------------------------------------------------------------------- devices

def platform_of(user_agent):
    ua = (user_agent or "").lower()
    if "android" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        return "ios"
    if "windows" in ua:
        return "windows"
    if "mac os" in ua or "macintosh" in ua:
        return "macos"
    if "linux" in ua or "x11" in ua:
        return "linux"
    return "other"


def _device_id():
    token = request.cookies.get(DEVICE_COOKIE) or ""
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
        g.set_device_cookie = token
    return token


def register_device(user):
    """Record the device for this sign-in. Returns the UserDevice (status says if it may be used)."""
    token = _device_id()
    digest = hashlib.sha256(token.encode()).hexdigest()
    device = db.session.execute(select(UserDevice).where(UserDevice.user_id == user.id,
                                                         UserDevice.device_hash == digest)).scalar_one_or_none()
    ua = request.headers.get("User-Agent", "")[:300]
    platform = platform_of(ua)
    if device is None:
        needs_approval = settings.get("device_policy") == "approval" and not user.is_admin
        device = UserDevice(user_id=user.id, device_hash=digest, platform=platform, user_agent=ua,
                            status="pending" if needs_approval else "approved", last_ip=client_ip())
        db.session.add(device)
        db.session.flush()
        if needs_approval:
            log_activity("security", "device_pending",
                         f"New {platform} device for '{user.username}' is waiting for approval", user.id)
    device.last_seen = utcnow()
    device.last_ip = client_ip()
    device.platform = platform
    device.user_agent = ua
    return device


def device_problem(user, device):
    if device is None:
        return "device"
    if device.platform in (settings.get("blocked_platforms") or []):
        return "platform"
    if device.status == "blocked":
        return "blocked"
    if device.status != "approved":
        return "pending"
    return None


# --------------------------------------------------------------------------- sessions

def client_ip():
    return request.remote_addr or ""


def ip_allowed(ip=None):
    allow = settings.get("ip_allowlist") or []
    if not allow:
        return True
    try:
        addr = ipaddress.ip_address(ip or client_ip())
    except ValueError:
        return False
    if addr.is_loopback:
        return True  # never lock the server console out
    return any(addr in ipaddress.ip_network(net, strict=False) for net in allow)


def login_user(user, method):
    """Start an authenticated session after the first factor succeeded."""
    session.clear()
    session.permanent = True
    now = int(time.time())
    session["uid"] = user.id
    session["auth_at"] = now
    session["seen"] = now
    session["method"] = method
    session["mfa_ok"] = not user.mfa_enabled
    device = register_device(user)
    session["device_id"] = device.id
    user.last_login_at = utcnow()
    log_activity("security", "login", f"'{user.username}' signed in ({method})", user.id)
    db.session.commit()
    g.user = user
    reset_resolver()
    from ..services.folders import ensure_home
    ensure_home(user)
    return device


def logout_user():
    user = getattr(g, "user", None)
    if user is not None:
        log_activity("security", "logout", f"'{user.username}' signed out", user.id)
        db.session.commit()
    session.clear()
    g.user = None


def _load_session_user():
    uid = session.get("uid")
    if not uid:
        return None, None
    user = db.session.get(User, uid)
    if user is None or not user.is_active:
        session.clear()
        return None, "Your account is not active."
    now = int(time.time())
    idle = settings.get("session_idle_minutes") * 60
    max_age = settings.get("session_max_hours") * 3600
    if now - session.get("seen", 0) > idle:
        session.clear()
        return None, "Your session expired after a period of inactivity. Please sign in again."
    if now - session.get("auth_at", 0) > max_age:
        session.clear()
        return None, "Your session reached its maximum length. Please sign in again."
    session["seen"] = now
    return user, None


def _wants_json():
    from .. import wants_json
    return wants_json() or request.path.startswith("/api/")


def _deny(status, message, redirect_to=None):
    if _wants_json():
        return jsonify(ok=False, error=message), status
    if redirect_to:
        if message:
            from flask import flash
            flash(message, "error")
        return redirect(redirect_to)
    abort(status, description=message)


def enforce_request_security():
    """before_request hook: transport, IP policy, authentication, MFA, device policy."""
    g.user = None
    reset_resolver()
    settings.clear_cache()
    cfg = current_app.config
    if cfg.get("FORCE_HTTPS") and not request.is_secure and not cfg.get("TESTING"):
        if request.method in ("GET", "HEAD"):
            return redirect(request.url.replace("http://", "https://", 1), code=301)
        return _deny(403, "HTTPS is required.")
    if not ip_allowed():
        log.warning("Request from %s blocked by IP allowlist", client_ip())
        return _deny(403, "Access from your network address is not permitted.")
    endpoint = request.endpoint or ""
    if endpoint.startswith("api."):
        return None  # the API authenticates with bearer tokens (see routes/api.py)
    if not endpoint and not cfg.get("LEGACY_UI_ENABLED"):
        return None
    if endpoint in PUBLIC_ENDPOINTS:
        uid = session.get("uid")
        if uid:
            g.user = db.session.get(User, uid)
        return None

    if db.session.execute(select(func.count(User.id))).scalar() == 0:
        return redirect(url_for("auth.setup"))

    user, reason = _load_session_user()
    if user is None:
        nxt = request.full_path if request.method == "GET" else None
        return _deny(401, reason or "Please sign in.", url_for("auth.login", next=nxt) if nxt else url_for("auth.login"))
    if not session.get("mfa_ok"):
        return _deny(401, "", url_for("auth.mfa"))

    device = db.session.get(UserDevice, session.get("device_id") or 0)
    problem = device_problem(user, device)
    if problem:
        session.clear()
        if problem == "pending":
            return _deny(403, "This device is waiting for administrator approval.", url_for("auth.device_pending"))
        return _deny(403, "Access from this device is not permitted.", url_for("auth.blocked"))

    g.user = user
    if settings.get("mfa_required") and user.auth_source in ("local", "ldap") and not user.mfa_enabled \
            and endpoint not in ("account.mfa_setup", "account.mfa_enable"):
        return _deny(403, "Your organisation requires multi-factor authentication. Set it up to continue.",
                     url_for("account.mfa_setup"))
    return None


def after_request(resp):
    token = getattr(g, "set_device_cookie", None)
    if token:
        resp.set_cookie(DEVICE_COOKIE, token, max_age=5 * 365 * 24 * 3600, httponly=True, samesite="Lax",
                        secure=current_app.config.get("SESSION_COOKIE_SECURE", False))
    if current_app.config.get("FORCE_HTTPS") and request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security",
                                f"max-age={current_app.config['HSTS_SECONDS']}; includeSubDomains")
    return resp


# --------------------------------------------------------------------------- API tokens

def new_api_token(user, name, days=None):
    raw = "dms_" + secrets.token_urlsafe(32)
    token = ApiToken(user_id=user.id, name=name[:100] or "API token", prefix=raw[:12],
                     token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                     expires_at=(utcnow() + timedelta(days=days)) if days else None)
    db.session.add(token)
    log_activity("security", "token_created", f"API token '{token.name}' created for '{user.username}'", user.id)
    db.session.commit()
    return raw, token


def user_for_token(raw):
    if not raw or not raw.startswith("dms_"):
        return None
    token = db.session.execute(select(ApiToken).where(
        ApiToken.token_hash == hashlib.sha256(raw.encode()).hexdigest())).scalar_one_or_none()
    if token is None or token.revoked_at or (token.expires_at and token.expires_at < utcnow()):
        return None
    if not token.user.is_active:
        return None
    token.last_used_at = utcnow()
    return token.user


# --------------------------------------------------------------------------- decorators

def current_user():
    return getattr(g, "user", None)


def require_capability(capability):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None or not user.has(capability):
                abort(403, description="You do not have permission to open this page.")
            return view(*args, **kwargs)
        return wrapper
    return deco
