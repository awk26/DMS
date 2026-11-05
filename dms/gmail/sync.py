"""Gmail -> DMS synchronisation.

Every DMS user connects their own mailbox (``gmail_accounts.user_id``). Each sync run
processes all connected mailboxes, GMAIL_SYNC_WORKERS at a time, each with its own
credentials, history baseline and lease. Archived emails belong to the mailbox's user and
are visible only to them.

How "archived" is detected
--------------------------
Gmail does not notify external systems when the Archive button is clicked.
Archiving a message removes its ``INBOX`` label, which Gmail records in the
mailbox history. Each sync run asks ``users.history.list`` for changes since
the last stored ``historyId`` and:

* queues messages whose ``INBOX`` label was removed (archive candidates);
* re-checks the current labels of a candidate before importing it, so
  messages that were trashed, marked as spam or put back in the inbox are not
  imported as archived;
* updates the Gmail state of already-archived records when they are moved back
  to the inbox, trashed, marked as spam or permanently deleted.

The new ``historyId`` is committed in the same transaction as the queued
candidates, and each candidate is removed from the queue only after its EML
file and database row are committed. An interrupted run therefore never loses
a candidate and never creates duplicates (unique account + message id).

If the stored ``historyId`` is too old (Gmail returns 404), a bounded resync
scans recently archived mail and reconciles it with the archive.
"""
import base64
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from googleapiclient.errors import HttpError
from sqlalchemy import select, update
from flask import current_app

from .. import sp
from ..extensions import db
from ..models import ArchivedEmail, GmailAccount, PendingImport, utcnow
from ..services.activity import log_activity
from ..services.emails import MESSAGE_ID_RE, store_email, update_state
from .auth import GmailAuthError, build_service, check_domain, credentials_configured, mailbox_address, \
    seal_credentials

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=10)
ARCHIVED_QUERY = "-in:inbox -in:trash -in:spam -in:drafts -in:sent -in:chats"
_STATE_LABELS = {"INBOX", "TRASH", "SPAM"}


@dataclass
class SyncResult:
    status: str = "ok"          # ok | busy | not_connected | not_configured | reauth_required | error
    message: str = ""
    account: str = None
    mode: str = "incremental"   # initial | incremental | full_resync
    queued: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    state_changes: int = 0
    errors: list = field(default_factory=list)

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _execute(request, retries=3):
    """Execute a Gmail API request, retrying rate limits and transient server errors."""
    for attempt in range(retries + 1):
        try:
            return request.execute(num_retries=0)
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise
        except (socket.timeout, ConnectionError, TimeoutError):
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise


def _status(exc):
    return getattr(getattr(exc, "resp", None), "status", 0)


def _is_auth_error(exc):
    status = _status(exc)
    if status == 401:
        return True
    if status == 403:
        text = str(exc).lower()
        return "ratelimit" not in text.replace(" ", "") and "rate limit" not in text
    return False


def _short_error(exc):
    if isinstance(exc, HttpError):
        try:
            reason = exc.error_details or exc.reason
        except Exception:
            reason = ""
        return f"Gmail API error {_status(exc)}: {str(reason)[:300]}"
    return f"{exc.__class__.__name__}: {str(exc)[:300]}"


# ------------------------------------------------------------------- lease

def _acquire_lease(account_id):
    now = utcnow()
    res = db.session.execute(
        update(GmailAccount)
        .where(GmailAccount.id == account_id)
        .where((GmailAccount.lock_until.is_(None)) | (GmailAccount.lock_until < now))
        .values(lock_until=now + LEASE)
    )
    db.session.commit()
    return res.rowcount == 1


def _release_lease(account_id):
    db.session.execute(update(GmailAccount).where(GmailAccount.id == account_id).values(lock_until=None))
    db.session.commit()


# ------------------------------------------------------------------- account

def account_for_user(user_id):
    """The user's connected mailbox, or None."""
    row = sp.call_one("sp_gmail_status_for_user", user_id, flush=False, expire=False)
    if row is None or not row["is_active"]:
        return None
    return db.session.get(GmailAccount, row["account_id"])


def connect_mailbox(user, creds, ip=None):
    """Attach the mailbox behind ``creds`` to ``user`` and run its first sync. Returns a SyncResult.

    Raises GmailAuthError (domain not allowed) or ValidationError (mailbox owned by another user).
    """
    email = mailbox_address(creds)
    check_domain(email)
    account_id = sp.call_one("sp_gmail_account_connect", user.id, email, seal_credentials(creds),
                             user.label, ip)["account_id"]
    db.session.commit()
    return sync_account(account_id, trigger="connect")


def disconnect_mailbox(user, ip=None):
    """Stop archiving the user's mailbox and forget its token. Archived emails stay. Returns the count."""
    count = sp.call_one("sp_gmail_account_disconnect", user.id, user.label, ip)["disconnected"]
    db.session.commit()
    return count


def _mark_account(account_id, code, message):
    account = db.session.get(GmailAccount, account_id)
    if account:
        account.last_sync_at = utcnow()
        account.last_sync_status = code
        account.last_error = message[:1000]
        db.session.commit()


# ------------------------------------------------------------------- queue helpers

def _enqueue(account, message_ids, source):
    """Add message ids to the import queue unless already archived or queued. No commit."""
    ids = {m for m in message_ids if MESSAGE_ID_RE.match(m or "")}
    if not ids:
        return 0, set()
    have = set(db.session.execute(select(ArchivedEmail.gmail_message_id).where(
        ArchivedEmail.account_id == account.id, ArchivedEmail.gmail_message_id.in_(ids))).scalars())
    queued = set(db.session.execute(select(PendingImport.gmail_message_id).where(
        PendingImport.account_id == account.id, PendingImport.gmail_message_id.in_(ids))).scalars())
    added = 0
    for mid in sorted(ids - have - queued):
        db.session.add(PendingImport(account_id=account.id, gmail_message_id=mid, source=source))
        added += 1
    # A message archived again after being restored to the inbox: refresh its state.
    return added, ids & have


def _refresh_states(service, account, message_ids, result):
    """Re-read labels for already archived records and update their Gmail state."""
    if not message_ids:
        return
    records = db.session.execute(select(ArchivedEmail).where(
        ArchivedEmail.account_id == account.id, ArchivedEmail.gmail_message_id.in_(message_ids))).scalars().all()
    for record in records:
        try:
            msg = _execute(service.users().messages().get(
                userId="me", id=record.gmail_message_id, format="minimal"))
            record.gmail_thread_id = record.gmail_thread_id or msg.get("threadId")
            changed = update_state(record, msg.get("labelIds", []))
        except HttpError as exc:
            if _status(exc) == 404:
                changed = update_state(record, deleted=True)
            else:
                raise
        if changed:
            result.state_changes += 1
    db.session.commit()


# ------------------------------------------------------------------- history scanning

def _scan_history(service, account):
    """Return (archive_candidates, state_check_ids, deleted_ids, new_history_id)."""
    candidates, check, deleted = set(), set(), set()
    page_token, new_history_id = None, account.history_id
    while True:
        kwargs = dict(userId="me", startHistoryId=account.history_id,
                      historyTypes=["labelAdded", "labelRemoved", "messageDeleted"], maxResults=500)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _execute(service.users().history().list(**kwargs))
        for record in resp.get("history", []):
            for item in record.get("labelsRemoved", []):
                mid = item.get("message", {}).get("id")
                removed = set(item.get("labelIds", []))
                if "INBOX" in removed:
                    candidates.add(mid)
                if removed & _STATE_LABELS:
                    check.add(mid)
            for item in record.get("labelsAdded", []):
                if set(item.get("labelIds", [])) & _STATE_LABELS:
                    check.add(item.get("message", {}).get("id"))
            for item in record.get("messagesDeleted", []):
                deleted.add(item.get("message", {}).get("id"))
        new_history_id = resp.get("historyId", new_history_id)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    candidates.discard(None), check.discard(None), deleted.discard(None)
    return candidates, check, deleted, new_history_id


def _list_archived(service, query, limit):
    ids, page_token = [], None
    while len(ids) < limit:
        kwargs = dict(userId="me", q=query, maxResults=min(500, limit - len(ids)), includeSpamTrash=False)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _execute(service.users().messages().list(**kwargs))
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids[:limit]


def _full_resync(service, account, baseline_history_id, result):
    days = current_app.config["GMAIL_RESYNC_WINDOW_DAYS"]
    result.mode = "full_resync"
    ids = _list_archived(service, f"{ARCHIVED_QUERY} newer_than:{days}d", limit=5000)
    added, _ = _enqueue(account, ids, "resync")
    result.queued += added
    # Reconcile records imported in the same window.
    since = utcnow() - timedelta(days=days)
    recent = db.session.execute(select(ArchivedEmail.gmail_message_id).where(
        ArchivedEmail.account_id == account.id, ArchivedEmail.imported_at >= since,
        ArchivedEmail.gmail_state != "deleted")).scalars().all()
    account.history_id = str(baseline_history_id)
    account.last_full_sync_at = utcnow()
    log_activity("gmail", "resync", f"Gmail history expired; rescanned archived mail from the last {days} days")
    db.session.commit()
    _refresh_states(service, account, recent[:500], result)


# ------------------------------------------------------------------- importing

def _process_queue(service, account, result):
    max_attempts = current_app.config["GMAIL_MAX_IMPORT_ATTEMPTS"]
    items = db.session.execute(select(PendingImport).where(
        PendingImport.account_id == account.id, PendingImport.status == "pending",
        PendingImport.attempts < max_attempts).order_by(PendingImport.id)).scalars().all()
    for item in items:
        mid = item.gmail_message_id
        try:
            msg = _execute(service.users().messages().get(userId="me", id=mid, format="raw"))
            labels = msg.get("labelIds", [])
            if set(labels) & (_STATE_LABELS | {"DRAFT"}):
                # Back in the inbox, trashed, spam or a draft by the time we looked: not archived.
                db.session.delete(item)
                db.session.commit()
                result.skipped += 1
                continue
            raw = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
            record, created = store_email(
                account, mid, raw, thread_id=msg.get("threadId"), label_ids=labels,
                internal_date_ms=msg.get("internalDate"), gmail_snippet=msg.get("snippet"), source=item.source)
            if not created:
                update_state(record, labels)
            db.session.delete(item)
            db.session.commit()
            if created:
                result.imported += 1
        except HttpError as exc:
            db.session.rollback()
            if _status(exc) == 404:  # deleted from Gmail before we could import it
                db.session.delete(db.session.merge(item))
                db.session.commit()
                result.skipped += 1
                continue
            if _is_auth_error(exc):
                raise
            _record_failure(item, exc, max_attempts, result)
        except Exception as exc:  # noqa: BLE001 - keep the queue moving; error is recorded
            db.session.rollback()
            log.exception("Import of Gmail message %s failed", mid)
            _record_failure(item, exc, max_attempts, result)


def _record_failure(item, exc, max_attempts, result):
    item = db.session.merge(item)
    item.attempts += 1
    item.last_error = _short_error(exc)[:1000]
    if item.attempts >= max_attempts:
        item.status = "failed"
    db.session.commit()
    result.failed += 1
    result.errors.append(f"{item.gmail_message_id}: {item.last_error}")


def import_message(service, account, message_id, source="manual"):
    """Queue and import one message immediately (used by tests and retries)."""
    added, _ = _enqueue(account, [message_id], source)
    db.session.commit()
    result = SyncResult(account=account.email)
    _process_queue(service, account, result)
    return result


def retry_failed(user_id=None):
    """Re-queue failed imports of the user's mailbox (None = every mailbox). Returns the count."""
    n = sp.call_one("sp_gmail_retry_failed", user_id)["requeued"]
    db.session.commit()
    return n


# ------------------------------------------------------------------- entry points

def sync_account(account_id, service=None, trigger="scheduled"):
    """Synchronise one connected mailbox. Returns a SyncResult."""
    result = SyncResult()
    account = db.session.get(GmailAccount, account_id)
    if account is None or not account.is_active or account.user_id is None:
        result.status, result.message = "not_connected", "Gmail is not connected."
        return result
    result.account = account.email
    try:
        service = service or build_service(account)
        db.session.commit()  # keeps a token refreshed while building the service
        profile = _execute(service.users().getProfile(userId="me"))
    except GmailAuthError as exc:
        db.session.rollback()
        result.status, result.message = exc.code, str(exc)
        if exc.code != "not_connected" and exc.code != "not_configured":
            _mark_account(account_id, exc.code, str(exc))
        return result
    except HttpError as exc:
        code = "reauth_required" if _is_auth_error(exc) else "error"
        result.status, result.message = code, _short_error(exc)
        _mark_account(account_id, code, result.message)
        return result
    except Exception as exc:  # network failures etc.
        result.status, result.message = "error", _short_error(exc)
        _mark_account(account_id, "error", result.message)
        return result

    if profile["emailAddress"].lower() != account.email.lower():
        result.status = "reauth_required"
        result.message = f"The stored authorization belongs to {profile['emailAddress']}, not {account.email}."
        _mark_account(account_id, result.status, result.message)
        return result
    if not _acquire_lease(account.id):
        result.status, result.message = "busy", "A synchronization is already running."
        return result

    try:
        if not account.history_id:
            result.mode = "initial"
            account.history_id = str(profile["historyId"])
            backfill = current_app.config["GMAIL_INITIAL_BACKFILL"]
            if backfill > 0:
                ids = _list_archived(service, ARCHIVED_QUERY, limit=backfill)
                added, _ = _enqueue(account, ids, "backfill")
                result.queued += added
            log_activity("gmail", "baseline", "Gmail synchronization baseline established")
            db.session.commit()
        else:
            try:
                candidates, check, deleted, new_history_id = _scan_history(service, account)
            except HttpError as exc:
                if _status(exc) != 404:
                    raise
                _full_resync(service, account, profile["historyId"], result)
            else:
                added, rearchived = _enqueue(account, candidates, "sync")
                result.queued += added
                for record in db.session.execute(select(ArchivedEmail).where(
                        ArchivedEmail.account_id == account.id,
                        ArchivedEmail.gmail_message_id.in_(deleted))).scalars():
                    result.state_changes += int(update_state(record, deleted=True))
                account.history_id = str(new_history_id)
                db.session.commit()  # queue + new history id, atomically
                _refresh_states(service, account, (check | rearchived) - deleted, result)

        _process_queue(service, account, result)

        account = db.session.get(GmailAccount, account.id)
        account.last_sync_at = utcnow()
        if result.failed:
            account.last_sync_status = "partial"
            account.last_error = "; ".join(result.errors)[:1000]
        else:
            account.last_sync_status = "ok"
            account.last_success_at = account.last_sync_at
            account.last_error = None
        db.session.commit()
        result.message = (f"{result.imported} imported, {result.state_changes} updated, "
                          f"{result.failed} failed")
        if result.failed:
            result.status = "partial"
        log.info("Gmail sync %s (%s, %s): %s", account_id, trigger, result.mode, result.message)
    except GmailAuthError as exc:
        db.session.rollback()
        result.status, result.message = exc.code, str(exc)
        _mark_account(account_id, exc.code, str(exc))
    except HttpError as exc:
        db.session.rollback()
        code = "reauth_required" if _is_auth_error(exc) else "error"
        result.status, result.message = code, _short_error(exc)
        _mark_account(account_id, code, result.message)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.exception("Gmail sync of account %s failed", account_id)
        result.status, result.message = "error", _short_error(exc)
        _mark_account(account_id, "error", result.message)
    finally:
        try:
            _release_lease(account_id)
        except Exception:
            db.session.rollback()
    return result


def sync_all(trigger="scheduled"):
    """Synchronise every connected mailbox, GMAIL_SYNC_WORKERS at a time. Returns a summary dict."""
    if not credentials_configured():
        return {"status": "not_configured", "message": "OAuth client file (credentials.json) is missing.",
                "accounts": 0}
    ids = [row["id"] for row in sp.call("sp_gmail_accounts_due", 1000000, flush=False, expire=False)]
    db.session.commit()
    app = current_app._get_current_object()

    def one(account_id):
        # Each worker thread gets its own app context and therefore its own database session.
        with app.app_context():
            try:
                return sync_account(account_id, trigger=trigger)
            except Exception as exc:  # noqa: BLE001 - one mailbox must not stop the others
                log.exception("Gmail sync of account %s failed", account_id)
                return SyncResult(status="error", message=_short_error(exc))
            finally:
                db.session.remove()

    results = []
    if ids:
        with ThreadPoolExecutor(max_workers=min(app.config["GMAIL_SYNC_WORKERS"], len(ids)),
                                thread_name_prefix="gmail-sync") as pool:
            results = list(pool.map(one, ids))
    problems = [r for r in results if r.status not in ("ok", "busy")]
    return {
        "status": "partial" if problems else "ok",
        "message": f"{len(results)} mailbox(es) synchronised, {len(problems)} with problems",
        "accounts": len(results),
        "imported": sum(r.imported for r in results),
        "state_changes": sum(r.state_changes for r in results),
        "failed": sum(r.failed for r in results),
        "errors": [f"{r.account}: {r.status} {r.message}"[:300] for r in problems][:20],
    }
