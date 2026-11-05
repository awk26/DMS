"""Gmail OAuth callback and helpers used by the Gmail API endpoints (routes/api.py).

Every user connects and manages their own mailbox. The connect / status / sync / disconnect
endpoints live under ``/api/v1/gmail``; only Google's OAuth redirect lands here.
"""
import logging
from urllib.parse import urlencode

from flask import Blueprint, current_app, g, redirect, request, session, url_for

from .. import ensure_scheduler, sp
from ..extensions import db
from ..gmail import auth
from ..gmail.sync import account_for_user, connect_mailbox, sync_account
from ..validation import ValidationError

log = logging.getLogger(__name__)
callback_bp = Blueprint("gmail_callback", __name__, url_prefix="/gmail")


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _status_payload(user):
    """Gmail connection status of ``user``'s own mailbox."""
    row = sp.call_one("sp_gmail_status_for_user", user.id, flush=False, expire=False) or {}
    connected = bool(row.get("is_active"))
    sched = current_app.extensions.get("gmail_scheduler")
    return {
        "configured": auth.credentials_configured(),
        "connected": connected,
        "token_present": connected,  # kept for older clients
        "account": row.get("email"),
        "connected_at": _iso(row.get("connected_at")),
        "last_sync_at": _iso(row.get("last_sync_at")),
        "last_success_at": _iso(row.get("last_success_at")),
        "status": row.get("last_sync_status") if row else "not_connected",
        "error": row.get("last_error"),
        "scheduler_running": bool(sched and sched.alive),
        "sync_in_progress": bool(sched and sched.running_now),
        "interval_seconds": current_app.config["GMAIL_SYNC_INTERVAL_SECONDS"],
        "allowed_domains": current_app.config.get("GMAIL_ALLOWED_DOMAINS") or [],
        "pending": int(row.get("pending") or 0) if connected else 0,
        "failed": int(row.get("failed") or 0) if connected else 0,
        "archived_total": int(row.get("archived_total") or 0),
    }


def begin_connect(user):
    """Start Google authorization for ``user``. Returns the Google URL to send the browser to."""
    callback = url_for("gmail_callback.callback", _external=True)
    url, state, verifier = auth.start_authorization(callback)
    session["oauth_state"], session["oauth_verifier"] = state, verifier
    session["gmail_connect_uid"] = user.id
    return url


def sync_own(user):
    """Run "Sync now" for the user's own mailbox. Returns (http_status, payload)."""
    account = account_for_user(user.id)
    if account is None:
        return 409, {"ok": False, "error": "Connect your Gmail account first.", "code": "not_connected"}
    result = sync_account(account.id, trigger="manual")
    if result.status in ("ok", "partial"):
        return 200, {"ok": True, "result": result.as_dict()}
    return (409 if result.status == "busy" else 502), {"ok": False, "error": result.message,
                                                       "result": result.as_dict()}


@callback_bp.get("/oauth2callback")
def callback():
    state = session.pop("oauth_state", None)
    verifier = session.pop("oauth_verifier", None)
    connect_uid = session.pop("gmail_connect_uid", None)
    frontend = current_app.config.get("ANGULAR_FRONTEND_URL") or "/"

    def _back(outcome, message):
        # The React app reads ?gmail=connected|error&message=...
        sep = "&" if "?" in frontend else "?"
        return redirect(f"{frontend}{sep}{urlencode({'gmail': outcome, 'message': message})}", code=303)

    if request.args.get("error"):
        return _back("error", f"Google authorization was not completed ({request.args['error']}).")
    if not state or state != request.args.get("state"):
        return _back("error", "Authorization session expired or did not match. Please try connecting again.")
    # The mailbox is attached to the DMS user who started the connection, who must still be signed in.
    user = getattr(g, "user", None)
    if user is None or not user.is_active or user.id != connect_uid:
        return _back("error", "Please sign in to the DMS and connect Gmail again.")
    try:
        creds = auth.finish_authorization(url_for("gmail_callback.callback", _external=True), state, verifier,
                                          request.url)
    except Exception as exc:  # noqa: BLE001 - surface a safe message, log details
        log.warning("OAuth token exchange failed: %s", exc.__class__.__name__)
        return _back("error", "Google authorization failed. Please try again.")
    try:
        result = connect_mailbox(user, creds, request.remote_addr)
    except (ValidationError, auth.GmailAuthError) as exc:
        db.session.rollback()
        return _back("error", str(exc))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.exception("Connecting Gmail for user %s failed", user.id)
        return _back("error", f"Could not connect Gmail ({exc.__class__.__name__}).")
    ensure_scheduler(current_app._get_current_object())
    if result.status not in ("ok", "partial", "busy"):
        return _back("error", f"Connected, but the first synchronization failed: {result.message}")
    return _back("connected",
                 f"Gmail account {result.account} connected. Emails you archive in Gmail will be stored in the DMS.")
