"""Sign-in pages: local/LDAP password, MFA, OpenID Connect, SAML 2.0 and first-run setup."""
import ipaddress
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, session,
                   url_for)
from sqlalchemy import func, select

from ..config import oidc_provider_configs
from ..extensions import csrf, db
from ..models import User, UserDevice, utcnow
from ..security import ldap_auth, mfa, saml
from ..security.auth import (AuthError, check_password_policy, device_problem, find_user, hash_password,
                             login_user, logout_user, provision_external, register_failure, role_by_name,
                             verify_local)
from ..services import settings
from ..services.activity import log_activity
from ..validation import ValidationError
from ._helpers import safe_next

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__, url_prefix="/auth")
callback_bp = Blueprint("auth_callbacks", __name__, url_prefix="/auth")
_oauth = None


def init_oauth(app):
    """Register the configured OpenID Connect providers with Authlib."""
    global _oauth
    from authlib.integrations.flask_client import OAuth
    _oauth = OAuth(app)
    providers = oidc_provider_configs(app.config.get("OIDC_PROVIDERS") or [])
    for name, cfg in providers.items():
        _oauth.register(name=name, client_id=cfg["client_id"], client_secret=cfg["client_secret"],
                        server_metadata_url=cfg["server_metadata_url"],
                        client_kwargs={"scope": cfg["scope"], "code_challenge_method": "S256"})
    app.extensions["dms_oidc"] = providers


def sso_options():
    opts = [{"kind": "oidc", "name": n, "label": c["label"],
             "login_url": url_for("auth_callbacks.oidc_login", name=n)}
            for n, c in current_app.extensions.get("dms_oidc", {}).items()]
    if saml.enabled():
        opts.append({"kind": "saml", "name": "saml", "label": current_app.config["SAML_LABEL"],
                     "login_url": url_for("auth_callbacks.saml_login")})
    return opts


def _after_first_factor(user, method, next_url=None):
    device = login_user(user, method)
    problem = device_problem(user, device)
    if problem:
        session.clear()
        db.session.commit()
        if current_app.config.get("LEGACY_UI_ENABLED"):
            return redirect(url_for("auth.device_pending" if problem == "pending" else "auth.blocked"))
        return redirect(_frontend_url(auth_error=problem) or "/")
    nxt = next_url or current_app.config.get("ANGULAR_FRONTEND_URL") or (
        url_for("main.dashboard") if current_app.config.get("LEGACY_UI_ENABLED") else "/"
    )
    if not session.get("mfa_ok"):
        session["mfa_next"] = nxt
        frontend = _frontend_url(mfa_required="1")
        if frontend:
            return redirect(frontend)
        if current_app.config.get("LEGACY_UI_ENABLED"):
            return redirect(url_for("auth.mfa"))
        return redirect("/?mfa_required=1")
    return redirect(nxt)


def _frontend_url(**params):
    target = current_app.config.get("ANGULAR_FRONTEND_URL")
    if not target:
        return None
    parsed = urlsplit(target)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _sso_failure_redirect():
    return _frontend_url(auth_error="sso_failed") or (
        url_for("auth.login") if current_app.config.get("LEGACY_UI_ENABLED") else "/"
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if db.session.execute(select(func.count(User.id))).scalar() == 0:
        return redirect(url_for("auth.setup"))
    if g.get("user") is not None and session.get("mfa_ok"):
        return redirect(safe_next(url_for("main.dashboard")))
    error = None
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        try:
            user = verify_local(username, password)
            method = "password"
            if user is None and ldap_auth.enabled():
                existing = find_user(username)
                if existing is not None and existing.locked_until and existing.locked_until > utcnow():
                    raise AuthError("This account is temporarily locked after repeated failed sign-ins. "
                                    "Try again later.")
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
                error = "The user name or password is incorrect."
            elif not user.is_active:
                error = "Your account is disabled."
            else:
                return _after_first_factor(user, method)
        except (AuthError, ldap_auth.LdapUnavailable) as exc:
            db.session.rollback()
            error = str(exc)
    return render_template("auth/login.html", error=error, username=username, sso=sso_options(),
                           ldap_label=current_app.config["LDAP_LABEL"] if ldap_auth.enabled() else None,
                           next=request.args.get("next", ""), signup=settings.get("self_signup")), \
        (401 if error else 200)


_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,59}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Self sign-up: creates a local account with the default sign-up role and signs it in."""
    if db.session.execute(select(func.count(User.id))).scalar() == 0:
        return redirect(url_for("auth.setup"))
    if not settings.get("self_signup"):
        flash("New accounts are created by an administrator. Contact your administrator for access.", "error")
        return redirect(url_for("auth.login"))
    error = None
    form = request.form
    if request.method == "POST":
        try:
            username = (form.get("username") or "").strip().lower()
            email = (form.get("email") or "").strip().lower()
            display_name = (form.get("display_name") or "").strip()[:200]
            if not display_name:
                raise ValidationError("Enter your full name.")
            if not _EMAIL_RE.match(email) or len(email) > 320:
                raise ValidationError("Enter a valid email address.")
            if not _USERNAME_RE.match(username):
                raise ValidationError("User names are 3–60 characters: lower-case letters, digits, dots, "
                                      "hyphens or underscores.")
            if find_user(username) is not None or find_user(email) is not None:
                raise ValidationError("An account with this user name or email already exists. Sign in instead.")
            if form.get("password") != form.get("password2"):
                raise ValidationError("The passwords do not match.")
            check_password_policy(form.get("password"))
            role = role_by_name(settings.get("signup_default_role"))
            if role is None or role.capability_set & {"admin", "records"}:
                role = role_by_name("Contributor")
            user = User(username=username, email=email, display_name=display_name,
                        password_hash=hash_password(form.get("password")), roles=[role] if role else [])
            db.session.add(user)
            db.session.flush()
            log_activity("security", "signup", f"'{username}' created an account ({role.name if role else 'no role'})",
                         user.id)
            db.session.commit()
            return _after_first_factor(user, "password")
        except ValidationError as exc:
            db.session.rollback()
            error = str(exc)
    return render_template("auth/register.html", error=error, form=form), (400 if error else 200)


@bp.route("/mfa", methods=["GET", "POST"], endpoint="mfa")
def mfa_challenge():
    uid = session.get("uid")
    user = db.session.get(User, uid) if uid else None
    if user is None:
        return redirect(url_for("auth.login"))
    if session.get("mfa_ok"):
        return redirect(url_for("main.dashboard"))
    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        secret = mfa.unseal(user.mfa_secret)
        attempts = session.get("mfa_attempts", 0) + 1
        session["mfa_attempts"] = attempts
        if attempts > 5:
            session.clear()
            flash("Too many incorrect codes. Sign in again.", "error")
            return redirect(url_for("auth.login"))
        if mfa.verify_totp(secret, code) or mfa.use_recovery_code(user, code):
            session["mfa_ok"] = True
            session.pop("mfa_attempts", None)
            log_activity("security", "mfa_passed", f"'{user.username}' completed multi-factor authentication", user.id)
            db.session.commit()
            return redirect(session.pop("mfa_next", None) or url_for("main.dashboard"))
        log_activity("security", "mfa_failed", f"Incorrect MFA code for '{user.username}'", user.id)
        db.session.commit()
        error = "That code is not correct. Enter the current 6-digit code or a recovery code."
    return render_template("auth/mfa.html", error=error, user=user), (401 if error else 200)


@bp.post("/logout")
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.get("/device-pending")
def device_pending():
    return render_template("auth/message.html", title="Device waiting for approval",
                           message="This device has not been approved yet. An administrator has been notified; "
                                   "try again once it has been approved.")


@bp.get("/blocked")
def blocked():
    return render_template("auth/message.html", title="Access not permitted",
                           message="Sign-in from this device or platform is not permitted by your organisation's "
                                   "policy."), 403


# --------------------------------------------------------------------------- first-run setup

def _setup_allowed():
    if current_app.config.get("ALLOW_REMOTE_SETUP") or current_app.config.get("TESTING"):
        return True
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Create the first administrator. Only available while no user exists."""
    if db.session.execute(select(func.count(User.id))).scalar() > 0:
        return redirect(url_for("auth.login"))
    if not _setup_allowed():
        abort(403, description="Initial setup must be completed from the server itself "
                               "(or set DMS_ALLOW_REMOTE_SETUP=true temporarily).")
    error = None
    form = request.form
    if request.method == "POST":
        try:
            username = (form.get("username") or "").strip().lower()
            if not username or len(username) > 150:
                raise ValidationError("Choose a user name.")
            if form.get("password") != form.get("password2"):
                raise ValidationError("The passwords do not match.")
            check_password_policy(form.get("password"))
            admin = role_by_name("Admin")
            user = User(username=username, email=(form.get("email") or "").strip().lower() or None,
                        display_name=(form.get("display_name") or "").strip()[:200],
                        password_hash=hash_password(form.get("password")), roles=[admin])
            db.session.add(user)
            db.session.flush()
            log_activity("admin", "setup", f"Initial administrator '{username}' created", user.id)
            db.session.commit()
            flash("Administrator account created. Sign in to continue.", "success")
            return redirect(url_for("auth.login"))
        except ValidationError as exc:
            db.session.rollback()
            error = str(exc)
    return render_template("auth/setup.html", error=error, form=form)


# --------------------------------------------------------------------------- OpenID Connect

@callback_bp.get("/oidc/<name>")
def oidc_login(name):
    if _oauth is None or name not in current_app.extensions.get("dms_oidc", {}):
        abort(404)
    session["sso_next"] = current_app.config.get("ANGULAR_FRONTEND_URL") or (
        safe_next(url_for("main.dashboard")) if current_app.config.get("LEGACY_UI_ENABLED") else "/"
    )
    client = _oauth.create_client(name)
    return client.authorize_redirect(url_for("auth_callbacks.oidc_callback", name=name, _external=True))


@callback_bp.get("/oidc/<name>/callback")
def oidc_callback(name):
    providers = current_app.extensions.get("dms_oidc", {})
    if _oauth is None or name not in providers:
        abort(404)
    nxt = session.get("sso_next")
    try:
        token = _oauth.create_client(name).authorize_access_token()
    except Exception as exc:
        log.warning("OIDC callback failed for %s: %s", name, exc)
        flash("Single sign-on failed. Try again.", "error")
        return redirect(_sso_failure_redirect())
    claims = token.get("userinfo") or {}
    subject = claims.get("sub")
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn") or ""
    if name == "google" and claims.get("email") and not claims.get("email_verified", False):
        email = ""
    if not subject:
        flash("The identity provider did not return a user identifier.", "error")
        return redirect(_sso_failure_redirect())
    groups = claims.get(providers[name]["groups_claim"]) or []
    if isinstance(groups, str):
        groups = [groups]
    try:
        user = provision_external("oidc", f"{name}:{claims.get('iss', '')}:{subject}", email,
                                  claims.get("name") or "", groups)
        db.session.flush()
    except AuthError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(_sso_failure_redirect())
    resp = _after_first_factor(user, f"sso:{name}", next_url=nxt)
    if nxt and session.get("mfa_ok"):
        return redirect(nxt)
    return resp


# --------------------------------------------------------------------------- SAML 2.0

def _saml_serializer(purpose):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=f"dms-saml-{purpose}")


_used_saml_tokens = {}  # token -> expiry; best-effort single use within this process


def _consume_once(token, ttl):
    import time
    now = time.time()
    for t, exp in list(_used_saml_tokens.items()):
        if exp < now:
            _used_saml_tokens.pop(t, None)
    if token in _used_saml_tokens:
        return False
    _used_saml_tokens[token] = now + ttl
    return True


@callback_bp.get("/saml/login")
def saml_login():
    if not saml.enabled():
        abort(404)
    # The IdP posts back cross-site, where the (SameSite=Lax) session cookie is not sent, so the
    # request id and target page travel in a signed RelayState instead of the session.
    request_id = saml.new_request_id()
    next_url = current_app.config.get("ANGULAR_FRONTEND_URL") or (
        safe_next(url_for("main.dashboard")) if current_app.config.get("LEGACY_UI_ENABLED") else "/"
    )
    relay = _saml_serializer("relay").dumps({"rid": request_id, "next": next_url})
    url, _ = saml.build_login_redirect(relay, request_id=request_id)
    return redirect(url)


@callback_bp.post("/saml/acs")
@csrf.exempt
def saml_acs():
    """Assertion consumer: validates the response, then finishes sign-in on a same-site GET."""
    if not saml.enabled():
        abort(404)
    from itsdangerous import BadSignature
    relay_raw = request.form.get("RelayState", "")
    try:
        relay = _saml_serializer("relay").loads(relay_raw, max_age=600)
    except BadSignature:
        relay = {}
    try:
        if not relay.get("rid") or not _consume_once(relay_raw, 600):
            raise saml.SamlError("This sign-in was not started here or has already been used. Try again.")
        info = saml.parse_response(request.form.get("SAMLResponse"), relay["rid"])
    except saml.SamlError as exc:
        log_activity("security", "login_failed", f"SAML sign-in rejected: {exc}")
        db.session.commit()
        flash(str(exc), "error")
        return redirect(_sso_failure_redirect())
    token = _saml_serializer("complete").dumps({**info, "next": relay.get("next")})
    return redirect(url_for("auth_callbacks.saml_complete", t=token))


@callback_bp.get("/saml/complete")
def saml_complete():
    from itsdangerous import BadSignature
    token = request.args.get("t", "")
    try:
        info = _saml_serializer("complete").loads(token, max_age=120)
    except BadSignature:
        info = None
    if not info or not _consume_once(token, 120):
        flash("The sign-in link has expired. Try again.", "error")
        return redirect(_sso_failure_redirect())
    try:
        user = provision_external("saml", info["name_id"], info["email"], info["name"], info["groups"])
        db.session.flush()
    except AuthError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(_sso_failure_redirect())
    nxt = info.get("next")
    resp = _after_first_factor(user, "sso:saml", next_url=nxt)
    if nxt and session.get("mfa_ok") and nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return resp


@callback_bp.get("/saml/metadata")
def saml_metadata():
    if not saml.enabled():
        abort(404)
    return Response(saml.sp_metadata(), mimetype="application/samlmetadata+xml")


def device_status_label(device: UserDevice):
    return {"approved": "Approved", "pending": "Waiting for approval", "blocked": "Blocked"}.get(device.status,
                                                                                              device.status)
