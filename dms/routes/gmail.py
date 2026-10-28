import logging
import threading

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from .. import ensure_scheduler
from ..extensions import db
from ..gmail import auth
from ..gmail.sync import active_account, retry_failed, run_sync
from ..models import ArchivedEmail, PendingImport
from ..services.activity import log_activity
from ._helpers import fail, ok

log = logging.getLogger(__name__)
bp = Blueprint("gmail", __name__, url_prefix="/gmail")
callback_bp = Blueprint("gmail_callback", __name__, url_prefix="/gmail")


@bp.before_request
def _restrict():
    """Connecting or disconnecting Gmail is for administrators; email-archive users may trigger a sync."""
    from flask import abort, g
    user = getattr(g, "user", None)
    needed = "emails" if request.endpoint in ("gmail.sync_now", "gmail.status") else "admin"
    if user is None or not user.has(needed):
        abort(403, description="You do not have permission to manage the Gmail connection.")


def _status_payload():
    account = active_account()
    sched = current_app.extensions.get("gmail_scheduler")
    pending = db.session.execute(select(func.count(PendingImport.id)).where(PendingImport.status == "pending")).scalar()
    failed = db.session.execute(select(func.count(PendingImport.id)).where(PendingImport.status == "failed")).scalar()
    return {
        "configured": auth.credentials_configured(),
        "token_present": auth.token_present(),
        "account": account.email if account else None,
        "last_sync_at": account.last_sync_at.isoformat() + "Z" if account and account.last_sync_at else None,
        "last_success_at": account.last_success_at.isoformat() + "Z" if account and account.last_success_at else None,
        "status": account.last_sync_status if account else ("not_connected" if not auth.token_present() else "never"),
        "error": account.last_error if account else None,
        "scheduler_running": bool(sched and sched.alive),
        "sync_in_progress": bool(sched and sched.running_now),
        "interval_seconds": current_app.config["GMAIL_SYNC_INTERVAL_SECONDS"],
        "pending": pending, "failed": failed,
        "archived_total": db.session.execute(select(func.count(ArchivedEmail.id))).scalar(),
    }


@bp.get("/")
def settings():
    failed_items = db.session.execute(select(PendingImport).where(PendingImport.status == "failed")).scalars().all()
    return render_template("gmail/settings.html", status=_status_payload(), failed_items=failed_items,
                           redirect_uri=url_for("gmail_callback.callback", _external=True))


@bp.get("/status")
def status():
    return jsonify(_status_payload())


@bp.get("/connect")
def connect():
    if not auth.credentials_configured():
        return fail("credentials.json is missing. See the setup guide.", url_for("gmail.settings"))
    redirect_uri = url_for("gmail_callback.callback", _external=True)
    url, state, verifier = auth.start_authorization(redirect_uri)
    session["oauth_state"], session["oauth_verifier"] = state, verifier
    return redirect(url)


@callback_bp.get("/oauth2callback")
def callback():
    state = session.pop("oauth_state", None)
    verifier = session.pop("oauth_verifier", None)
    api_connect = session.pop("gmail_api_connect", False)
    frontend = current_app.config.get("ANGULAR_FRONTEND_URL") if api_connect else None
    fallback = frontend or (url_for("gmail.settings") if current_app.config.get("LEGACY_UI_ENABLED") else "/")
    if request.args.get("error"):
        return fail(f"Google authorization was not completed ({request.args['error']}).", fallback)
    if not state or state != request.args.get("state"):
        return fail("Authorization session expired or did not match. Please try connecting again.", fallback)
    try:
        auth.finish_authorization(url_for("gmail_callback.callback", _external=True), state, verifier, request.url)
    except Exception as exc:  # noqa: BLE001 - surface a safe message, log details
        log.warning("OAuth token exchange failed: %s", exc.__class__.__name__)
        return fail("Google authorization failed. Please try again.", fallback)
    result = run_sync(trigger="connect")  # establishes the account and history baseline
    if result.status not in ("ok", "partial", "busy"):
        return fail(f"Connected, but the first synchronization failed: {result.message}", fallback)
    ensure_scheduler(current_app._get_current_object())
    return ok(f"Gmail account {result.account} connected. Newly archived emails will be imported automatically.",
              fallback)


@bp.post("/disconnect")
def disconnect():
    account = active_account()
    auth.disconnect()
    if account:
        account.is_active = False
        account.last_sync_status = "disconnected"
        log_activity("gmail", "disconnected", f"Gmail account {account.email} disconnected")
        db.session.commit()
    return ok("Gmail disconnected. Archived emails remain in the DMS.", url_for("gmail.settings"))


@bp.post("/sync")
def sync_now():
    sched = ensure_scheduler(current_app._get_current_object())
    if sched is not None:
        sched.trigger()
        return ok("Synchronization started.", request.referrer or url_for("gmail.settings"), 202)
    result = run_sync(trigger="manual")
    if result.status in ("ok", "partial"):
        return ok(f"Synchronization finished: {result.message}.", request.referrer or url_for("gmail.settings"),
                  result=result.as_dict())
    return fail(result.message or "Synchronization failed.", request.referrer or url_for("gmail.settings"),
                409 if result.status == "busy" else 502)


@bp.post("/retry")
def retry():
    n = retry_failed()
    sched = current_app.extensions.get("gmail_scheduler")
    if sched:
        sched.trigger()
    return ok(f"{n} failed import(s) queued for retry.", request.referrer or url_for("gmail.settings"))
