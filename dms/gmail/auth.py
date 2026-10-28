"""Gmail OAuth handling. Tokens stay on the server (token file); never sent to the browser."""
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

log = logging.getLogger(__name__)
_token_lock = threading.Lock()


class GmailAuthError(Exception):
    """Gmail cannot be used until the user (re)connects the account."""

    def __init__(self, message, code="reauth_required"):
        super().__init__(message)
        self.code = code


def _cfg():
    c = current_app.config
    return Path(c["GMAIL_CREDENTIALS_FILE"]), Path(c["GMAIL_TOKEN_FILE"]), list(c["GMAIL_SCOPES"])


def credentials_configured():
    return _cfg()[0].is_file()


def token_present():
    return _cfg()[1].is_file()


def save_token(creds):
    """Atomically replace the token file."""
    _, token_path, _ = _cfg()
    tmp = token_path.with_suffix(".json.tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    os.replace(tmp, token_path)


def load_credentials():
    """Load stored credentials, refreshing them if expired. Raises GmailAuthError."""
    creds_path, token_path, scopes = _cfg()
    if not creds_path.is_file():
        raise GmailAuthError("OAuth client file (credentials.json) is missing.", "not_configured")
    if not token_path.is_file():
        raise GmailAuthError("Gmail is not connected.", "not_connected")
    with _token_lock:
        try:
            info = json.loads(token_path.read_text(encoding="utf-8"))
            granted = set(info.get("scopes") or [])
            if granted and not set(scopes) <= granted:
                raise GmailAuthError("Stored Gmail authorization lacks required permissions. Please reconnect.")
            creds = Credentials.from_authorized_user_info(info, scopes)
        except (ValueError, KeyError) as exc:
            raise GmailAuthError(f"Stored Gmail token is invalid ({exc.__class__.__name__}). Please reconnect.")
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                log.warning("Gmail token refresh rejected (expired or revoked)")
                raise GmailAuthError("Gmail access has expired or been revoked. Please reconnect.")
            except TransportError as exc:
                raise GmailAuthError(f"Could not reach Google to refresh access: {exc.__class__.__name__}",
                                     "network")
            save_token(creds)
            return creds
        raise GmailAuthError("Stored Gmail token cannot be refreshed. Please reconnect.")


def build_service(creds=None):
    creds = creds or load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------- browser (redirect) flow

def _flow(redirect_uri, state=None):
    creds_path, _, scopes = _cfg()
    if redirect_uri.startswith("http://") and current_app.config["OAUTH_ALLOW_HTTP_LOCALHOST"]:
        host = redirect_uri.split("//", 1)[1].split("/", 1)[0].split(":")[0]
        if host in ("localhost", "127.0.0.1"):
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    return Flow.from_client_secrets_file(str(creds_path), scopes=scopes, state=state, redirect_uri=redirect_uri)


def start_authorization(redirect_uri):
    """Returns (authorization_url, state, code_verifier)."""
    flow = _flow(redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return url, state, flow.code_verifier


def finish_authorization(redirect_uri, state, code_verifier, authorization_response):
    flow = _flow(redirect_uri, state=state)
    flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    save_token(creds)
    return creds


# ---------------------------------------------------------------- console flow (CLI)

def console_authorize():
    """Original local-server flow (opens a browser on this machine)."""
    creds_path, _, scopes = _cfg()
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
    creds = flow.run_local_server(port=0)
    save_token(creds)
    return creds


def disconnect():
    _, token_path, _ = _cfg()
    with _token_lock:
        token_path.unlink(missing_ok=True)
