"""Document Management System with Gmail archiving: a JSON API (``/api/v1``) for the React frontend."""
import logging
import threading
from datetime import timedelta

from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from .config import Config
from .extensions import csrf, db
from .storage import LocalStorage
from .validation import ValidationError

_scheduler_lock = threading.Lock()


def create_app(config_object=None, **overrides):
    configured_app = config_object or Config
    # API only: no server-rendered pages, templates or static files (the UI is the React app in DMS-FE).
    app = Flask(
        __name__,
        instance_path=str(Config.INSTANCE_DIR),
        instance_relative_config=False,
        static_folder=None,
        template_folder=None,
    )
    app.config.from_object(configured_app)
    app.config.update(overrides)
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(days=30))  # real limits: Admin > Settings
    same_site = app.config.get("SESSION_COOKIE_SAMESITE")
    if same_site not in ("Lax", "Strict", "None"):
        raise ValueError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
    if same_site == "None" and not app.config.get("SESSION_COOKIE_SECURE") and not app.config.get("TESTING"):
        raise ValueError("SESSION_COOKIE_SECURE must be enabled when SESSION_COOKIE_SAMESITE=None.")
    frontend_url = app.config.get("ANGULAR_FRONTEND_URL")
    if frontend_url:
        from urllib.parse import urlsplit
        parsed_frontend = urlsplit(frontend_url)
        if parsed_frontend.scheme not in ("http", "https") or not parsed_frontend.netloc \
                or parsed_frontend.username or parsed_frontend.password or parsed_frontend.fragment:
            raise ValueError("DMS_ANGULAR_FRONTEND_URL must be an absolute http(s) URL without credentials or fragment.")
    # MySQL is the only supported database: data access goes through its stored procedures (dms/sp.py).
    if not str(app.config.get("SQLALCHEMY_DATABASE_URI") or "").startswith("mysql"):
        raise RuntimeError("No MySQL database configured: set DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME "
                           "(or DATABASE_URL=mysql+pymysql://...) in the .env file.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

    if app.config.get("TRUSTED_PROXIES"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n, x_port=n)

    from pathlib import Path
    Path(app.config["INSTANCE_DIR"]).mkdir(parents=True, exist_ok=True)
    ring = None
    if app.config.get("ENCRYPTION_AT_REST"):
        from .crypto import load_key_ring
        ring = load_key_ring(app.config)
    app.extensions["dms_storage"] = LocalStorage(app.config["STORAGE_DIR"], key_ring=ring)

    db.init_app(app)
    csrf.init_app(app)

    from .routes.auth import init_oauth
    init_oauth(app)

    from . import models  # noqa: F401 - register tables
    from .validity import models as validity_models  # noqa: F401 - register the validity module's tables
    with app.app_context():
        db.create_all()
        from .migrate import upgrade
        upgrade(app)

    from .routes import api, auth, gmail
    app.register_blueprint(api.bp)
    app.register_blueprint(auth.callback_bp)
    app.register_blueprint(gmail.callback_bp)
    csrf.exempt(api.bp)
    # Document validity module: its own /api/v1 endpoints (same api_auth + CSRF check as api.bp), and the
    # hook that queues AI analysis after uploads commit (it can never fail an upload).
    from .validity import routes as validity_routes
    from .validity.jobs import install_hooks as install_validity_hooks
    app.register_blueprint(validity_routes.bp)
    csrf.exempt(validity_routes.bp)
    install_validity_hooks()

    from .cli import register_cli
    register_cli(app)
    from .validity.cli import register as register_validity_cli
    register_validity_cli(app)

    from .security import auth as auth_security
    app.before_request(auth_security.enforce_request_security)
    app.after_request(auth_security.after_request)

    _register_error_handlers(app)
    _register_security_headers(app)
    _register_api_cors(app)

    @app.before_request
    def _start_schedulers_once():
        ensure_scheduler(app)
        ensure_maintenance(app)
        ensure_validity_worker(app)

    return app


def ensure_scheduler(app):
    """Start the background Gmail sync thread (once per process)."""
    if app.config.get("TESTING") or not app.config.get("GMAIL_SYNC_ENABLED"):
        return None
    with _scheduler_lock:
        sched = app.extensions.get("gmail_scheduler")
        if sched is None:
            from .gmail.scheduler import SyncScheduler
            sched = SyncScheduler(app)
            app.extensions["gmail_scheduler"] = sched
            sched.start()
    return sched


def ensure_maintenance(app):
    """Start the background maintenance thread (recycle-bin expiry, version retention)."""
    if app.config.get("TESTING") or not app.config.get("MAINTENANCE_ENABLED"):
        return None
    with _scheduler_lock:
        sched = app.extensions.get("dms_maintenance")
        if sched is None:
            from .services.retention import MaintenanceScheduler
            sched = MaintenanceScheduler(app)
            app.extensions["dms_maintenance"] = sched
            sched.start()
    return sched


def ensure_validity_worker(app):
    """Start the validity worker thread (analysis jobs, daily expiry run, email retries), once per process.
    Off when VALIDITY_WORKER_ENABLED=false (then run `flask validity-worker` as a separate service)."""
    if app.config.get("TESTING") or not app.config.get("VALIDITY_WORKER_ENABLED")             or not app.config.get("VALIDITY_MONITORING_ENABLED"):
        return None
    with _scheduler_lock:
        worker = app.extensions.get("validity_worker")
        if worker is None:
            from .validity.scheduler import ValidityWorker
            worker = ValidityWorker(app)
            app.extensions["validity_worker"] = worker
            worker.start()
    return worker


_ERROR_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
}


def _register_error_handlers(app):
    """Every error is a JSON body: {"ok": false, "error": <message>, "code": <code>}."""
    def json_error(status, message):
        code = _ERROR_CODES.get(status, "internal_error" if status >= 500 else "request_failed")
        return jsonify(ok=False, error=message, code=code), status

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return json_error(400, "Your session token is missing or expired. Reload the page and try again.")

    @app.errorhandler(413)
    def too_large(e):
        return json_error(413, f"The upload exceeds the {app.config['MAX_REQUEST_MB']} MB limit.")

    @app.errorhandler(HTTPException)
    def http_error(e):
        return json_error(e.code, e.description if e.code != 500 else "An unexpected error occurred.")

    @app.errorhandler(ValidationError)
    def validation_error(e):
        # Routes outside the API decorator (e.g. provider callbacks) would otherwise surface these as 500s.
        db.session.rollback()
        return jsonify(ok=False, error=str(e), code="validation_error"), 400

    @app.errorhandler(Exception)
    def unhandled(e):
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        db.session.rollback()
        return json_error(500, "An unexpected error occurred. The problem has been logged.")


def _register_security_headers(app):
    """Security headers on every response; values come from config.py (DMS_* environment variables).
    setdefault keeps any stricter or route-specific header a view already set."""
    from .config import DEFAULT_CONTENT_SECURITY_POLICY
    cfg = app.config
    defaults = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": cfg.get("REFERRER_POLICY") or "same-origin",
        "X-Frame-Options": cfg.get("FRAME_OPTIONS") or "SAMEORIGIN",
        "Content-Security-Policy": cfg.get("CONTENT_SECURITY_POLICY") or DEFAULT_CONTENT_SECURITY_POLICY,
        "Permissions-Policy": cfg.get("PERMISSIONS_POLICY") or "",
    }
    defaults = {name: value for name, value in defaults.items() if value}

    @app.after_request
    def headers(resp):
        for name, value in defaults.items():
            resp.headers.setdefault(name, value)
        if request.endpoint:
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp


def _register_api_cors(app):
    allowed_origins = set(app.config.get("API_CORS_ALLOWED_ORIGINS") or ())
    if not allowed_origins:
        return
    from urllib.parse import urlsplit

    for origin in allowed_origins:
        parsed = urlsplit(origin)
        if origin == "*" or parsed.scheme not in ("http", "https") or not parsed.netloc \
                or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("API_CORS_ALLOWED_ORIGINS must contain exact http(s) origins, not paths or wildcards.")

    @app.after_request
    def api_cors_headers(resp):
        origin = request.headers.get("Origin", "").rstrip("/")
        if request.path.startswith("/api/") and origin in allowed_origins:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-CSRFToken, X-Request-ID"
            )
            resp.headers["Access-Control-Expose-Headers"] = (
                "Content-Disposition, ETag, Accept-Ranges, Content-Range"
            )
            resp.headers["Access-Control-Max-Age"] = "600"
            resp.vary.add("Origin")
        return resp
