"""Application configuration, driven by environment variables (.env supported).

Settings that administrators change at runtime (file-type lists, retention, session and
device policies, ...) live in the database instead; see ``services/settings.py``.
"""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Must run before the Config class body below reads os.environ.
load_dotenv(BASE_DIR / ".env")


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _path(name, default):
    value = os.environ.get(name)
    path = Path(value) if value else default
    if path is None:
        return None
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _str(name, default=""):
    return os.environ.get(name, default).strip()


# Default Content-Security-Policy for the app and API: everything from this origin only, no inline
# scripts, no plugins, no framing by other sites. data:/blob: images are used by previews and email
# bodies; Google is allowed as a form target for the Gmail OAuth hand-off.
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self' https://accounts.google.com; frame-ancestors 'self'"
)
# Browser features the app never uses are switched off.
DEFAULT_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def database_uri():
    """The MySQL connection URL.

    Preferred: separate settings DB_HOST, DB_PORT (3306), DB_USER, DB_PASSWORD, DB_NAME. The password
    is used as-is (no URL encoding needed for '@', ':' or '/'). When DB_HOST is not set, DATABASE_URL
    (a full mysql+pymysql:// URL) is used, as before. Every connection uses utf8mb4 and UTC.
    """
    from sqlalchemy.engine import URL
    host = _str("DB_HOST")
    if not host:
        return os.environ.get("DATABASE_URL", "")
    return URL.create(
        "mysql+pymysql",
        username=_str("DB_USER") or None,
        password=os.environ.get("DB_PASSWORD") or None,  # not stripped: spaces may be part of it
        host=host,
        port=_int("DB_PORT", 3306),
        database=_str("DB_NAME") or None,
        query={"charset": "utf8mb4", "init_command": "SET time_zone='+00:00'"},
    ).render_as_string(hide_password=False)


class Config:
    APP_NAME = os.environ.get("DMS_APP_NAME", "Archive DMS")

    # A random key per process is safe but logs everyone out on restart;
    # set SECRET_KEY in .env for stable sessions.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    INSTANCE_DIR = _path("DMS_INSTANCE_DIR", BASE_DIR / "instance")
    STORAGE_DIR = _path("DMS_STORAGE_DIR", BASE_DIR / "storage")

    # Required: DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME, or DATABASE_URL (see database_uri()).
    SQLALCHEMY_DATABASE_URI = database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Connection pool: DB_POOL_SIZE connections kept open, up to DB_MAX_OVERFLOW extra under load.
    # pre_ping replaces connections MySQL closed while idle; recycle stays below wait_timeout.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": _int("DB_POOL_SIZE", 5),
        "max_overflow": _int("DB_MAX_OVERFLOW", 5),
        "pool_pre_ping": True,
        "pool_recycle": _int("DB_POOL_RECYCLE_SECONDS", 3600),
    }

    # Uploads. DMS_MAX_UPLOAD_MB is the default per-file limit (administrators can change it and set
    # per-type limits in Admin > Settings); DMS_MAX_REQUEST_MB is the hard ceiling for any request.
    MAX_UPLOAD_MB = _int("DMS_MAX_UPLOAD_MB", 50)
    MAX_REQUEST_MB = max(_int("DMS_MAX_REQUEST_MB", 1024), MAX_UPLOAD_MB)
    MAX_CONTENT_LENGTH = MAX_REQUEST_MB * 1024 * 1024

    # Sessions / CSRF
    SESSION_COOKIE_NAME = "dms_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = _str("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    WTF_CSRF_TIME_LIMIT = None
    API_CORS_ALLOWED_ORIGINS = tuple(
        origin.strip().rstrip("/")
        for origin in _str("DMS_CORS_ALLOWED_ORIGINS").split(",")
        if origin.strip()
    )
    ANGULAR_FRONTEND_URL = _str("DMS_ANGULAR_FRONTEND_URL")
    # Allow the first-administrator setup page from non-local addresses.
    ALLOW_REMOTE_SETUP = _bool("DMS_ALLOW_REMOTE_SETUP", False)

    # Transport security (FR-SEC-04). TLS 1.2+ is enforced when the built-in server serves HTTPS.
    FORCE_HTTPS = _bool("DMS_FORCE_HTTPS", False)
    HSTS_SECONDS = _int("DMS_HSTS_SECONDS", 31536000)
    SSL_CERT_FILE = _path("DMS_SSL_CERT_FILE", None)
    SSL_KEY_FILE = _path("DMS_SSL_KEY_FILE", None)
    # Number of reverse proxies in front of the app whose X-Forwarded-* headers are trusted.
    TRUSTED_PROXIES = _int("DMS_TRUSTED_PROXIES", 0)

    # Security response headers, added to every Flask response (VAPT: CSP, clickjacking, MIME sniffing,
    # server version). Routes that need a different policy (document previews, rendered files, email
    # bodies) set their own Content-Security-Policy, which takes precedence. Keep these values in line
    # with the frontend's web server config (DMS-FE/deploy/nginx-dms.conf), which serves the React app.
    CONTENT_SECURITY_POLICY = _str("DMS_CONTENT_SECURITY_POLICY", DEFAULT_CONTENT_SECURITY_POLICY)
    FRAME_OPTIONS = _str("DMS_FRAME_OPTIONS", "SAMEORIGIN")
    REFERRER_POLICY = _str("DMS_REFERRER_POLICY", "same-origin")
    PERMISSIONS_POLICY = _str("DMS_PERMISSIONS_POLICY", DEFAULT_PERMISSIONS_POLICY)
    # "Server" header sent by `python app.py` instead of "Werkzeug/3.x Python/3.x", so no version is disclosed.
    SERVER_HEADER = _str("DMS_SERVER_HEADER", "DMS")

    # Encryption at rest (AES-256-GCM). Keys: DMS_MASTER_KEYS / DMS_MASTER_KEY_FILE / DMS_KEY_PROVIDER.
    ENCRYPTION_AT_REST = _bool("DMS_ENCRYPTION_AT_REST", True)
    DMS_MASTER_KEY_FILE = _path("DMS_MASTER_KEY_FILE", None)

    # Malware scanning (FR-DOC-05): auto | clamav | defender | builtin
    MALWARE_SCANNER = _str("DMS_MALWARE_SCANNER", "auto").lower()
    CLAMAV_HOST = _str("CLAMAV_HOST")
    CLAMAV_PORT = _int("CLAMAV_PORT", 3310)
    CLAMAV_SOCKET = _str("CLAMAV_SOCKET")
    DEFENDER_PATH = _str("DEFENDER_PATH")
    # Reject uploads when the configured scanner cannot be reached (recommended).
    MALWARE_FAIL_CLOSED = _bool("DMS_MALWARE_FAIL_CLOSED", True)

    # Office previews: LibreOffice renders Office files to PDF when available.
    LIBREOFFICE_PATH = _str("LIBREOFFICE_PATH")

    # Search: files larger than this are indexed by name/metadata only.
    SEARCH_EXTRACT_MAX_MB = _int("DMS_SEARCH_EXTRACT_MAX_MB", 50)
    # OCR of images and scanned PDF pages for search. Used only when Tesseract is installed
    # (TESSERACT_PATH, or `tesseract` on the PATH). Languages as in Tesseract, e.g. "eng+hin".
    OCR_ENABLED = _bool("DMS_OCR_ENABLED", True)
    TESSERACT_PATH = _str("TESSERACT_PATH")
    OCR_LANGUAGES = _str("DMS_OCR_LANGUAGES", "eng")
    OCR_MAX_PAGES = max(1, _int("DMS_OCR_MAX_PAGES", 20))
    OCR_TIMEOUT_SECONDS = max(5, _int("DMS_OCR_TIMEOUT_SECONDS", 60))

    # Background maintenance (recycle-bin expiry, version retention)
    MAINTENANCE_ENABLED = _bool("DMS_MAINTENANCE_ENABLED", True)
    MAINTENANCE_INTERVAL_SECONDS = max(60, _int("DMS_MAINTENANCE_INTERVAL_SECONDS", 3600))

    # LDAP / Active Directory (FR-SEC-03)
    LDAP_URL = _str("LDAP_URL")                      # ldaps://dc01.corp.example.com
    LDAP_START_TLS = _bool("LDAP_START_TLS", False)
    LDAP_CA_FILE = _str("LDAP_CA_FILE")
    LDAP_BIND_DN = _str("LDAP_BIND_DN")              # service account used to find the user
    LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "")
    LDAP_BASE_DN = _str("LDAP_BASE_DN")
    LDAP_USER_FILTER = _str("LDAP_USER_FILTER", "(|(sAMAccountName={username})(userPrincipalName={username}))")
    LDAP_USER_DN_TEMPLATE = _str("LDAP_USER_DN_TEMPLATE")  # direct bind, e.g. {username}@corp.example.com
    LDAP_ATTR_EMAIL = _str("LDAP_ATTR_EMAIL", "mail")
    LDAP_ATTR_NAME = _str("LDAP_ATTR_NAME", "displayName")
    LDAP_ATTR_GROUPS = _str("LDAP_ATTR_GROUPS", "memberOf")
    LDAP_LABEL = _str("LDAP_LABEL", "Active Directory")

    # OpenID Connect providers (Azure AD / Entra ID, Google, any OIDC IdP); see oidc_provider_configs()
    OIDC_PROVIDERS = [p.strip().lower() for p in _str("OIDC_PROVIDERS").split(",") if p.strip()]

    # SAML 2.0
    SAML_ENABLED = _bool("SAML_ENABLED", False)
    SAML_IDP_ENTITY_ID = _str("SAML_IDP_ENTITY_ID")
    SAML_IDP_SSO_URL = _str("SAML_IDP_SSO_URL")
    SAML_IDP_CERT_FILE = _path("SAML_IDP_CERT_FILE", None)
    SAML_SP_ENTITY_ID = _str("SAML_SP_ENTITY_ID")    # default: <base url>/auth/saml/metadata
    SAML_ATTR_EMAIL = _str("SAML_ATTR_EMAIL", "email")
    SAML_ATTR_NAME = _str("SAML_ATTR_NAME", "displayName")
    SAML_ATTR_GROUPS = _str("SAML_ATTR_GROUPS", "groups")
    SAML_LABEL = _str("SAML_LABEL", "SAML single sign-on")

    # Gmail. Every user connects their own mailbox; the OAuth tokens are stored encrypted in
    # gmail_accounts.token_enc, and each user sees only the emails archived from their mailbox.
    GMAIL_CREDENTIALS_FILE = _path("GMAIL_CREDENTIALS_FILE", BASE_DIR / "credentials.json")
    GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    # Comma-separated Google domains users may connect (e.g. jmbaxi.com); empty allows any Gmail account.
    GMAIL_ALLOWED_DOMAINS = [d.strip().lower().lstrip("@") for d in _str("GMAIL_ALLOWED_DOMAINS").split(",")
                             if d.strip()]
    GMAIL_SYNC_ENABLED = _bool("GMAIL_SYNC_ENABLED", True)
    GMAIL_SYNC_INTERVAL_SECONDS = max(30, _int("GMAIL_SYNC_INTERVAL_SECONDS", 120))
    # Mailboxes synchronised in parallel by each sync run.
    GMAIL_SYNC_WORKERS = max(1, _int("GMAIL_SYNC_WORKERS", 4))
    # On first connection, also import up to N recently archived messages.
    GMAIL_INITIAL_BACKFILL = _int("GMAIL_INITIAL_BACKFILL", 0)
    # When Gmail history has expired, rescan archived mail from the last N days.
    GMAIL_RESYNC_WINDOW_DAYS = _int("GMAIL_RESYNC_WINDOW_DAYS", 30)
    GMAIL_MAX_IMPORT_ATTEMPTS = _int("GMAIL_MAX_IMPORT_ATTEMPTS", 5)
    # Allow the OAuth redirect over plain http (only sensible for localhost).
    OAUTH_ALLOW_HTTP_LOCALHOST = _bool("OAUTH_ALLOW_HTTP_LOCALHOST", True)
    # Images that archived emails link to (e.g. newsletter logos on mcusercontent.com) are downloaded
    # once by the server and stored with the email, so the preview shows them without loading anything
    # from the internet. The original .eml is never changed.
    EMAIL_REMOTE_IMAGES = _bool("DMS_EMAIL_REMOTE_IMAGES", True)
    EMAIL_IMAGE_MAX_MB = max(1, _int("DMS_EMAIL_IMAGE_MAX_MB", 5))
    EMAIL_IMAGE_MAX_COUNT = max(1, _int("DMS_EMAIL_IMAGE_MAX_COUNT", 60))
    EMAIL_IMAGE_TIMEOUT_SECONDS = max(2, _int("DMS_EMAIL_IMAGE_TIMEOUT_SECONDS", 10))

    TESTING = False


def oidc_provider_configs(names=None):
    """OIDC providers from the environment: OIDC_<NAME>_CLIENT_ID / _CLIENT_SECRET / _METADATA_URL / _LABEL.

    ``entra`` and ``google`` need only a client id/secret (Entra also reads ENTRA_TENANT_ID).
    """
    out = {}
    for name in (Config.OIDC_PROVIDERS if names is None else names):
        prefix = f"OIDC_{name.upper()}_"
        metadata = _str(prefix + "METADATA_URL")
        label = _str(prefix + "LABEL")
        if name in ("entra", "azure", "azuread") and not metadata:
            tenant = _str("ENTRA_TENANT_ID", "organizations")
            metadata = f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
            label = label or "Microsoft Entra ID"
        if name == "google" and not metadata:
            metadata = "https://accounts.google.com/.well-known/openid-configuration"
            label = label or "Google"
        client_id = _str(prefix + "CLIENT_ID")
        if not (client_id and metadata):
            continue
        out[name] = {
            "client_id": client_id,
            "client_secret": os.environ.get(prefix + "CLIENT_SECRET", ""),
            "server_metadata_url": metadata,
            "label": label or name.title(),
            "scope": _str(prefix + "SCOPES", "openid email profile"),
            "groups_claim": _str(prefix + "GROUPS_CLAIM", "groups"),
        }
    return out


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False
    GMAIL_SYNC_ENABLED = False
    GMAIL_REVOKE_TOKENS = False  # never call Google when a test unlinks a mailbox
    EMAIL_REMOTE_IMAGES = False  # tests never reach the internet; test_email_images enables it with a fake fetcher
    MAINTENANCE_ENABLED = False
    MALWARE_SCANNER = "builtin"
    FORCE_HTTPS = False
    LDAP_URL = ""
    SAML_ENABLED = False
    OIDC_PROVIDERS = []
