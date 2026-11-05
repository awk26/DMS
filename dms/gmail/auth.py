"""Gmail OAuth handling. Every user connects their own mailbox; its token is stored encrypted in
``gmail_accounts.token_enc`` and never sent to the browser."""
import json
import logging
import os
import threading
from pathlib import Path

from flask import current_app
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build

from .. import sp
from ..security.mfa import seal, unseal

log = logging.getLogger(__name__)
_token_lock = threading.Lock()


class GmailAuthError(Exception):
    """Gmail cannot be used until the user (re)connects the account."""

    def __init__(self, message, code="reauth_required"):
        super().__init__(message)
        self.code = code


def _cfg():
    c = current_app.config
    return Path(c["GMAIL_CREDENTIALS_FILE"]), list(c["GMAIL_SCOPES"])


def credentials_configured():
    return _cfg()[0].is_file()


def seal_credentials(creds):
    """Encrypt credentials for gmail_accounts.token_enc."""
    return seal(creds.to_json())


def check_domain(email):
    """Raise GmailAuthError if the mailbox is outside GMAIL_ALLOWED_DOMAINS (when that is set)."""
    allowed = current_app.config.get("GMAIL_ALLOWED_DOMAINS") or []
    domain = (email or "").rpartition("@")[2].lower()
    if allowed and domain not in allowed:
        raise GmailAuthError("Only Gmail accounts of " + ", ".join("@" + d for d in allowed)
                             + " can be connected.", "domain_not_allowed")


def load_credentials(account):
    """Load the account's stored credentials, refreshing them if expired. Raises GmailAuthError."""
    creds_path, scopes = _cfg()
    if not creds_path.is_file():
        raise GmailAuthError("OAuth client file (credentials.json) is missing.", "not_configured")
    if not account.token_enc:
        raise GmailAuthError("Gmail is not connected.", "not_connected")
    with _token_lock:
        try:
            info = json.loads(unseal(account.token_enc) or "")
            granted = set(info.get("scopes") or [])
            if granted and not set(scopes) <= granted:
                raise GmailAuthError("Stored Gmail authorization lacks required permissions. Please reconnect.")
            creds = Credentials.from_authorized_user_info(info, scopes)
        except GmailAuthError:
            raise
        except Exception as exc:  # undecryptable or malformed token
            raise GmailAuthError(f"Stored Gmail token is invalid ({exc.__class__.__name__}). Please reconnect.")
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                log.warning("Gmail token refresh rejected for account %s (expired or revoked)", account.id)
                raise GmailAuthError("Gmail access has expired or been revoked. Please reconnect.")
            except TransportError as exc:
                raise GmailAuthError(f"Could not reach Google to refresh access: {exc.__class__.__name__}",
                                     "network")
            sp.call("sp_gmail_account_save_token", account.id, seal_credentials(creds), flush=False, expire=False)
            return creds
        raise GmailAuthError("Stored Gmail token cannot be refreshed. Please reconnect.")


def build_service(account=None, creds=None):
    creds = creds or load_credentials(account)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def mailbox_address(creds):
    """The Gmail address the credentials belong to."""
    profile = build_service(creds=creds).users().getProfile(userId="me").execute(num_retries=2)
    return profile["emailAddress"].lower()


# ---------------------------------------------------------------- browser (redirect) flow

def _flow(redirect_uri, state=None):
    creds_path, scopes = _cfg()
    if redirect_uri.startswith("http://") and current_app.config["OAUTH_ALLOW_HTTP_LOCALHOST"]:
        host = redirect_uri.split("//", 1)[1].split("/", 1)[0].split(":")[0]
        if host in ("localhost", "127.0.0.1"):
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    return Flow.from_client_secrets_file(str(creds_path), scopes=scopes, state=state, redirect_uri=redirect_uri)


def start_authorization(redirect_uri, login_hint=None):
    """Returns (authorization_url, state, code_verifier)."""
    flow = _flow(redirect_uri)
    extra = {"login_hint": login_hint} if login_hint else {}
    url, state = flow.authorization_url(access_type="offline", prompt="select_account consent", include_granted_scopes="true",
                                        **extra)
    return url, state, flow.code_verifier


def finish_authorization(redirect_uri, state, code_verifier, authorization_response):
    flow = _flow(redirect_uri, state=state)
    flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials


# ---------------------------------------------------------------- console flow (CLI)

def console_authorize():
    """Original local-server flow (opens a browser on this machine)."""
    creds_path, scopes = _cfg()
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
    return flow.run_local_server(port=0)
