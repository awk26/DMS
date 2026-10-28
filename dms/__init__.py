"""Document Management System with Gmail archiving."""
import json
import logging
import threading
from datetime import timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException

from .config import BASE_DIR, Config
from .extensions import csrf, db
from .storage import LocalStorage

load_dotenv(BASE_DIR / ".env")

_scheduler_lock = threading.Lock()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


def wants_json():
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]


def create_app(config_object=None, **overrides):
    configured_app = config_object or Config
    legacy_ui_enabled = overrides.get(
        "LEGACY_UI_ENABLED", getattr(configured_app, "LEGACY_UI_ENABLED", False)
    )
    app = Flask(
        __name__,
        instance_path=str(Config.INSTANCE_DIR),
        instance_relative_config=False,
        static_folder="static" if legacy_ui_enabled else None,
        template_folder="templates" if legacy_ui_enabled else None,
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
    with app.app_context():
        db.create_all()
        from .migrate import upgrade
        upgrade(app)

    from .routes import api, auth, gmail
    app.register_blueprint(api.bp)
    app.register_blueprint(auth.callback_bp)
    app.register_blueprint(gmail.callback_bp)
    if app.config.get("LEGACY_UI_ENABLED"):
        from .routes import access, account, admin, documents, emails, folders, main, search
        for bp in (main.bp, documents.bp, folders.bp, emails.bp, gmail.bp, auth.bp, account.bp, admin.bp,
                   search.bp, access.bp):
            app.register_blueprint(bp)
    csrf.exempt(api.bp)

    from .cli import register_cli
    register_cli(app)

    from .security import auth as auth_security
    app.before_request(auth_security.enforce_request_security)
    app.after_request(auth_security.after_request)

    if app.config.get("LEGACY_UI_ENABLED"):
        _register_template_helpers(app)
    _register_error_handlers(app)
    _register_security_headers(app)
    _register_api_cors(app)

    @app.before_request
    def _start_schedulers_once():
        ensure_scheduler(app)
        ensure_maintenance(app)

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


def _register_template_helpers(app):
    from .validation import TYPE_LABELS

    @app.template_filter("filesize")
    def filesize(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024

    @app.template_filter("dt")
    def format_dt(value, fmt="%d %b %Y, %H:%M"):
        if not value:
            return "—"
        return value.replace(tzinfo=timezone.utc).astimezone().strftime(fmt)

    @app.template_filter("importable")
    def importable(filename):
        from .validation import extension_of, is_allowed_extension
        return is_allowed_extension(extension_of(filename))

    @app.template_filter("typelabel")
    def typelabel(ext):
        return TYPE_LABELS.get((ext or "").lower(), (ext or "File").upper())

    @app.context_processor
    def inject_globals():
        from .gmail.sync import active_account
        from .security.permissions import resolver
        user = getattr(g, "user", None)
        try:
            account = active_account() if user is not None and user.has("emails") else None
        except Exception:
            account = None

        def folder_options_json(perm="edit"):
            from .services.folders import folder_tree
            if user is None:
                return "[]"
            res = resolver()
            visible = None if res.is_admin else res.visible_folder_ids()
            data = [{"id": f.id, "parent": f.parent_id, "name": f.name, "depth": d, "path": f.path,
                     "can": perm in res.folder(f)}
                    for f, d in folder_tree(visible)]
            return json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

        def can(perm, obj=None):
            return user is not None and resolver().can(perm, obj)

        def has_cap(cap):
            return user is not None and user.has(cap)

        def is_fav(kind, target_id):
            if user is None:
                return False
            if "dms_favs" not in g:
                from .models import Favourite
                from sqlalchemy import select
                g.dms_favs = set(db.session.execute(select(Favourite.target_type, Favourite.target_id)
                                                    .where(Favourite.user_id == user.id)).all())
            return (kind, target_id) in g.dms_favs

        def folder_label(folder):
            return "My Files" if user is not None and folder is not None and folder.id == user.home_folder_id                 else folder.name

        def crumbs(folder):
            """[(folder, label, can_view)] from the top down to ``folder``; inside My Files it starts there."""
            if folder is None:
                return []
            chain = folder.ancestors() + [folder]
            home = user.home_folder_id if user is not None else None
            ids = [f.id for f in chain]
            if home in ids:
                chain = chain[ids.index(home):]
            res = resolver()
            return [(f, folder_label(f), "view" in res.folder(f)) for f in chain]

        return {"app_name": app.config["APP_NAME"], "gmail_account": account, "current_user": user,
                "max_upload_mb": _max_upload_mb(), "folder_options_json": folder_options_json,
                "can": can, "has_cap": has_cap, "is_fav": is_fav, "folder_label": folder_label, "crumbs": crumbs,
                "home_folder_id": user.home_folder_id if user is not None else None,
                "root_can_edit": user is not None and resolver().can("edit", None)}

    def _max_upload_mb():
        from .services import settings
        try:
            return settings.get("max_upload_mb")
        except Exception:
            return app.config["MAX_UPLOAD_MB"]


def _register_error_handlers(app):
    def render_error(status, title, message):
        if not app.config.get("LEGACY_UI_ENABLED") or wants_json() or request.path.startswith("/api/"):
            code = {
                400: "bad_request",
                401: "authentication_required",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                413: "payload_too_large",
            }.get(status, "internal_error" if status >= 500 else "request_failed")
            return jsonify(ok=False, error=message, code=code), status
        return render_template("error.html", status=status, title=title, message=message), status

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_error(400, "Session expired", "Your session token is missing or expired. "
                                                    "Reload the page and try again.")

    @app.errorhandler(413)
    def too_large(e):
        return render_error(413, "File too large",
                            f"The upload exceeds the {app.config['MAX_REQUEST_MB']} MB limit.")

    @app.errorhandler(HTTPException)
    def http_error(e):
        return render_error(e.code, e.name, e.description if e.code != 500 else "An unexpected error occurred.")

    @app.errorhandler(Exception)
    def unhandled(e):
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        db.session.rollback()
        return render_error(500, "Something went wrong",
                            "An unexpected error occurred. The problem has been logged.")


def _register_security_headers(app):
    default_csp = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
                   "script-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
                   "form-action 'self' https://accounts.google.com; frame-ancestors 'self'")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Content-Security-Policy", default_csp)
        if request.endpoint and not request.endpoint.startswith("static"):
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
