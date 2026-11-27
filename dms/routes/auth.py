"""SAML 2.0 single sign-on against ADFS: login, assertion consumer, logout, single logout and SP metadata.

    GET  /auth/login           start sign-in (redirects to the identity provider)
    POST /auth/saml/acs        assertion consumer: validates the response, starts the session, redirects to the frontend
    GET  /auth/logout          end the local session and sign out at the identity provider
    *    /auth/saml/sls        single logout service (IdP response / IdP-initiated logout)
    GET  /auth/saml/metadata   service provider metadata

The SAML settings live in saml_config/settings.json (the SP URLs there must match these routes). The SP
private key comes from the PFX (DMS_SAML_PFX_FILE + DMS_SAML_PFX_PASSWORD) or from certs/sp.crt + sp.key.
"""
import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, abort, current_app, redirect, request, session, url_for

from ..extensions import csrf, db
from ..security.auth import AuthError, device_problem, login_user, logout_user, provision_external
from ..services.activity import log_activity

log = logging.getLogger(__name__)

bp = Blueprint("sso", __name__, url_prefix="/auth")

_used_assertions = {}  # assertion id -> expiry; rejects a replayed response within this process
_used_lock = threading.Lock()


def enabled():
    cfg = current_app.config
    return bool(cfg.get("SAML_ENABLED") and (Path(cfg["SAML_CONFIG_DIR"]) / "settings.json").is_file())


def sso_options():
    """Sign-in options listed by GET /api/v1/auth/options."""
    if not enabled():
        return []
    return [{"kind": "saml", "name": "saml", "label": current_app.config["SAML_LABEL"],
             "login_url": url_for("sso.login")}]


def _frontend_url(**params):
    """The frontend URL with ``params`` added to its query string ("/" when not configured)."""
    target = current_app.config.get("ANGULAR_FRONTEND_URL") or "/"
    parsed = urlsplit(target)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))


def _failure():
    return redirect(_frontend_url(auth_error="sso_failed"))


def _safe_next(value):
    """``value`` if it is a page path inside the frontend (e.g. /documents/42), else None.

    Absolute URLs, //host, backslashes and backend paths are refused, so this can never be used to send the
    browser to another site (open redirect) or straight back into /auth/login.
    """
    value = (value or "").strip()
    if (not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 1000
            or value.startswith(("/auth/", "/api/"))):
        return None
    return value


def _after_login_url(relay_state):
    """Where to send the browser after a successful sign-in: the page the user asked for (carried through
    the identity provider as RelayState by /auth/login?next=...), otherwise the frontend home."""
    path = _safe_next(relay_state)
    if path is None:
        return _frontend_url()
    parsed = urlsplit(current_app.config.get("ANGULAR_FRONTEND_URL") or "")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return path


def _consume_once(assertion_id, not_on_or_after):
    """True the first time an assertion id is seen; False if it is replayed."""
    now = time.time()
    with _used_lock:
        for key, expiry in list(_used_assertions.items()):
            if expiry < now:
                _used_assertions.pop(key, None)
        if not assertion_id or assertion_id in _used_assertions:
            return False
        _used_assertions[assertion_id] = (not_on_or_after or now + 600) + 60
    return True


def _sp_credentials(cfg):
    """The SP private key and certificate (PEM), loaded once per process."""
    cache = current_app.extensions.setdefault("dms_saml_sp", {})
    if cache:
        return cache
    base = Path(cfg["SAML_CONFIG_DIR"])
    pfx, password = cfg.get("SAML_PFX_FILE"), cfg.get("SAML_PFX_PASSWORD")
    if pfx and password:
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
        key, cert, _ = pkcs12.load_key_and_certificates(Path(pfx).read_bytes(), password.encode())
        cache["privateKey"] = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
        cache["x509cert"] = cert.public_bytes(Encoding.PEM).decode()
    else:
        cache["privateKey"] = (base / "certs" / "sp.key").read_text(encoding="utf-8")
        cache["x509cert"] = (base / "certs" / "sp.crt").read_text(encoding="utf-8")
    return cache


def _auth():
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    cfg = current_app.config
    settings = json.loads((Path(cfg["SAML_CONFIG_DIR"]) / "settings.json").read_text(encoding="utf-8"))
    settings["strict"] = bool(cfg["SAML_STRICT"])
    settings["debug"] = False
    settings["sp"].update(_sp_credentials(cfg))
    secure = request.is_secure  # behind nginx this needs X-Forwarded-Proto and DMS_TRUSTED_PROXIES=1
    req = {
        "https": "on" if secure else "off",
        "http_host": request.host,
        "server_port": "443" if secure else "80",
        "script_name": request.path,
        "get_data": request.args.copy(),
        "post_data": request.form.copy(),
    }
    return OneLogin_Saml2_Auth(req, old_settings=settings)


@bp.get("/login")
def login():
    """Start single sign-on: redirect the browser to the identity provider.

    ``?next=/some/page`` (optional) is sent as RelayState so the user lands on that page after sign-in.
    """
    if not enabled():
        abort(404)
    next_path = _safe_next(request.args.get("next"))
    try:
        auth = _auth()
        return redirect(auth.login(return_to=next_path) if next_path else auth.login())
    except Exception:
        log.exception("Could not start SAML sign-in")
        return _failure()


@bp.post("/saml/acs")
@csrf.exempt
def acs():
    """Assertion consumer service: the identity provider posts the SAML response here."""
    if not enabled():
        abort(404)
    cfg = current_app.config
    try:
        auth = _auth()
        auth.process_response()
        errors = auth.get_errors()
        if errors or not auth.is_authenticated():
            log.warning("SAML response rejected: %s (%s)", errors, auth.get_last_error_reason())
            log_activity("security", "login_failed", "SAML sign-in rejected", status="failed")
            db.session.commit()
            return _failure()
        if not _consume_once(auth.get_last_assertion_id(), auth.get_last_assertion_not_on_or_after()):
            log.warning("SAML assertion replayed or without an id")
            return _failure()

        attrs = auth.get_attributes()

        def first(name):
            values = attrs.get(name) or []
            return str(values[0]).strip() if values else ""

        name_id = (auth.get_nameid() or "").strip()
        external_id = first(cfg["SAML_ATTR_ID"]) or name_id
        if not external_id:
            log.warning("SAML response carried no federation id or NameID")
            return _failure()
        email = name_id if "@" in name_id else first(cfg["SAML_ATTR_EMAIL"])
        name = first(cfg["SAML_ATTR_NAME"]).split(" - ")[0].strip()  # ADFS sends "Full Name - Department"
        groups = [str(g) for g in (attrs.get(cfg["SAML_ATTR_GROUPS"]) or [])]

        user = provision_external("saml", external_id, email, name, groups)
        db.session.flush()
        device = login_user(user, "sso:saml")
        # Kept for the single logout request (login_user starts a fresh session, so set these after it).
        session["saml_name_id"] = name_id
        session["saml_name_id_format"] = auth.get_nameid_format()
        session["saml_session_index"] = auth.get_session_index()
    except AuthError as exc:
        db.session.rollback()
        log.warning("SAML sign-in refused: %s", exc)
        return _failure()
    except Exception:
        db.session.rollback()
        log.exception("SAML sign-in failed")
        return _failure()

    problem = device_problem(user, device)
    if problem:
        session.clear()
        db.session.commit()
        return redirect(_frontend_url(auth_error=problem))
    if not session.get("mfa_ok"):
        return redirect(_frontend_url(mfa_required="1"))
    return redirect(_after_login_url(request.form.get("RelayState")))


@bp.get("/logout")
def logout():
    """End the local session, then sign out at the identity provider (single logout)."""
    name_id = session.get("saml_name_id")
    name_id_format = session.get("saml_name_id_format")
    session_index = session.get("saml_session_index")
    logout_user()
    if enabled() and name_id:
        try:
            return redirect(_auth().logout(name_id=name_id, session_index=session_index,
                                           name_id_format=name_id_format))
        except Exception:
            log.exception("Could not build the SAML logout request")
    return redirect(_frontend_url())


@bp.route("/saml/sls", methods=["GET", "POST"])
@csrf.exempt
def sls():
    """Single logout service: the identity provider's logout response, or its own logout request."""
    if not enabled():
        abort(404)
    url = None
    try:
        auth = _auth()
        url = auth.process_slo(delete_session_cb=logout_user)
        if auth.get_errors():
            log.warning("SAML logout errors: %s (%s)", auth.get_errors(), auth.get_last_error_reason())
            url = None
    except Exception:
        log.exception("SAML single logout failed")
    logout_user()
    return redirect(url or _frontend_url())


@bp.get("/saml/metadata", strict_slashes=False)
def metadata():
    """Service provider metadata, for registering this application with the identity provider."""
    if not enabled():
        abort(404)
    settings = _auth().get_settings()
    meta = settings.get_sp_metadata()
    errors = settings.validate_metadata(meta)
    if errors:
        log.error("Invalid SAML SP metadata: %s", errors)
        abort(500)
    return Response(meta, mimetype="text/xml")
