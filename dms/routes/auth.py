"""Single sign-on provider endpoints (OpenID Connect and SAML 2.0) and helpers shared with the API.

Password, MFA, sign-up and first-run setup are handled by the JSON API (routes/api.py). These
endpoints exist because identity providers redirect the browser here; once sign-in finishes, the
browser is sent back to the React frontend (``ANGULAR_FRONTEND_URL``), which reads
``?auth_error=...`` / ``?mfa_required=1`` from the query string.
"""
import ipaddress
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, abort, current_app, redirect, request, session, url_for

from ..config import oidc_provider_configs
from ..extensions import csrf, db
from ..security import saml
from ..security.auth import AuthError, device_problem, login_user, provision_external
from ..services.activity import log_activity

log = logging.getLogger(__name__)
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


def _setup_allowed():
    """First-run setup is only allowed from the server itself unless explicitly enabled."""
    if current_app.config.get("ALLOW_REMOTE_SETUP") or current_app.config.get("TESTING"):
        return True
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _frontend_url(**params):
    """The React frontend URL with ``params`` added to its query string ("/" when not configured)."""
    target = current_app.config.get("ANGULAR_FRONTEND_URL") or "/"
    parsed = urlsplit(target)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))


def _after_first_factor(user, method, next_url=None):
    device = login_user(user, method)
    problem = device_problem(user, device)
    if problem:
        session.clear()
        db.session.commit()
        return redirect(_frontend_url(auth_error=problem))
    nxt = next_url or _frontend_url()
    if not session.get("mfa_ok"):
        session["mfa_next"] = nxt
        return redirect(_frontend_url(mfa_required="1"))
    return redirect(nxt)


def _sso_failure():
    return redirect(_frontend_url(auth_error="sso_failed"))


# --------------------------------------------------------------------------- OpenID Connect

@callback_bp.get("/oidc/<name>")
def oidc_login(name):
    if _oauth is None or name not in current_app.extensions.get("dms_oidc", {}):
        abort(404)
    session["sso_next"] = _frontend_url()
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
        return _sso_failure()
    claims = token.get("userinfo") or {}
    subject = claims.get("sub")
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn") or ""
    if name == "google" and claims.get("email") and not claims.get("email_verified", False):
        email = ""
    if not subject:
        log.warning("OIDC provider %s returned no subject", name)
        return _sso_failure()
    groups = claims.get(providers[name]["groups_claim"]) or []
    if isinstance(groups, str):
        groups = [groups]
    try:
        user = provision_external("oidc", f"{name}:{claims.get('iss', '')}:{subject}", email,
                                  claims.get("name") or "", groups)
        db.session.flush()
    except AuthError as exc:
        db.session.rollback()
        log.warning("OIDC sign-in via %s refused: %s", name, exc)
        return _sso_failure()
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
    relay = _saml_serializer("relay").dumps({"rid": request_id, "next": _frontend_url()})
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
        log_activity("security", "login_failed", f"SAML sign-in rejected: {exc}", status="failed")
        db.session.commit()
        return _sso_failure()
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
        return _sso_failure()
    try:
        user = provision_external("saml", info["name_id"], info["email"], info["name"], info["groups"])
        db.session.flush()
    except AuthError as exc:
        db.session.rollback()
        log.warning("SAML sign-in refused: %s", exc)
        return _sso_failure()
    nxt = info.get("next")
    resp = _after_first_factor(user, "sso:saml", next_url=nxt)
    if nxt and session.get("mfa_ok"):
        return redirect(nxt)
    return resp


@callback_bp.get("/saml/metadata")
def saml_metadata():
    if not saml.enabled():
        abort(404)
    return Response(saml.sp_metadata(), mimetype="application/samlmetadata+xml")
