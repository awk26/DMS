"""Gmail synchronisation tests against a MOCKED Gmail API (see conftest.FakeGmail).

These verify the sync logic end to end (history scanning, import, EML storage,
dedup, reconciliation, retries). They do not contact Google; see
test_gmail_live.py for the opt-in live check.
"""
import pytest

from dms.extensions import db
from dms.gmail import sync
from dms.models import ArchivedEmail, GmailAccount, PendingImport
from dms.services.emails import parse_message
from dms.storage import get_storage

from .conftest import PDF, FakeGmail, build_email, http_error


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)


@pytest.fixture
def mailbox(app, admin):
    """The admin user's connected mailbox (the address FakeGmail reports)."""
    account = GmailAccount(email="tester@example.com", user_id=admin.id, is_active=True)
    db.session.add(account)
    db.session.commit()
    return account.id


@pytest.fixture
def gmail(mailbox):
    return FakeGmail()


def run(service=None):
    """Synchronise the (single) connected mailbox."""
    account_id = db.session.query(GmailAccount.id).scalar()
    return sync.sync_account(account_id, service=service)


def connect(gmail):
    """First run establishes the baseline."""
    result = run(gmail)
    assert result.status == "ok" and result.mode == "initial"
    return result


def test_initial_connection_sets_baseline_without_importing(gmail):
    gmail.add("m1", build_email(), labels=("UNREAD",))  # already archived before connecting
    connect(gmail)
    account = db.session.query(GmailAccount).one()
    assert account.email == "tester@example.com" and account.history_id == str(gmail.history_id)
    assert db.session.query(ArchivedEmail).count() == 0


def test_initial_backfill_is_optional(app, gmail):
    app.config["GMAIL_INITIAL_BACKFILL"] = 10
    gmail.add("m1", build_email(), labels=("UNREAD",))
    gmail.add("m2", build_email(), labels=("INBOX",))
    result = run(gmail)
    assert result.imported == 1
    assert db.session.query(ArchivedEmail).one().source == "backfill"


def test_archived_message_is_imported_as_original_eml(gmail, api):
    connect(gmail)
    raw = build_email()
    gmail.add("18c0ffee", raw)
    assert run(gmail).imported == 0   # new inbox mail is not archived
    gmail.archive("18c0ffee")
    result = run(gmail)
    assert result.status == "ok" and result.imported == 1

    record = db.session.query(ArchivedEmail).one()
    assert record.gmail_message_id == "18c0ffee" and record.gmail_thread_id == "18c0ffee"
    assert record.account.email == "tester@example.com"
    assert record.subject == "Quarterly report" and "alice@example.com" in record.sender
    assert "bob@example.com" in record.recipients and "carol@example.com" in record.cc
    assert record.message_id_header == "<abc123@example.com>"
    assert record.gmail_state == "archived" and record.size == len(raw)

    # The stored file is byte-for-byte the raw message from Gmail.
    stored = get_storage().read_bytes(record.storage_key)
    assert stored == raw
    # ...and attachments are still inside it.
    names = [p.get_filename() for p in parse_message(stored).walk() if p.get_filename()]
    assert "report.pdf" in names
    assert record.attachment_count == 1
    assert any(a.is_inline and a.content_type == "image/png" for a in record.attachments)

    # Download of the original EML returns identical bytes.
    r = api.get(f"/emails/{record.id}/eml")
    assert r.status_code == 200 and r.data == raw and r.mimetype == "message/rfc822"
    # Individual attachment download.
    pdf = next(a for a in record.attachments if a.filename == "report.pdf")
    r = api.get(f"/emails/{record.id}/attachments/{pdf.part_index}")
    assert r.status_code == 200 and r.data == PDF
    assert [e["subject"] for e in api.get("/emails").json["emails"]] == ["Quarterly report"]
    assert api.get("/dashboard").json["stats"]["emails"] == 1


def test_repeated_sync_does_not_duplicate(gmail):
    connect(gmail)
    gmail.add("m1", build_email())
    gmail.archive("m1")
    run(gmail)
    # Same message archived again after a round-trip through the inbox, plus extra runs.
    gmail.move_to_inbox("m1")
    gmail.archive("m1")
    for _ in range(3):
        r = run(gmail)
        assert r.status == "ok" and r.imported == 0
    assert db.session.query(ArchivedEmail).count() == 1
    assert db.session.query(ArchivedEmail).one().gmail_state == "archived"


def test_trash_and_spam_are_not_imported(gmail):
    connect(gmail)
    gmail.add("t1", build_email())
    gmail.trash("t1")  # removes INBOX but adds TRASH
    gmail.add("s1", build_email())
    gmail._labels("s1", add=("SPAM",), remove=("INBOX",))
    result = run(gmail)
    assert result.imported == 0 and result.skipped == 2
    assert db.session.query(ArchivedEmail).count() == 0
    assert db.session.query(PendingImport).count() == 0


def test_archived_then_back_to_inbox_before_sync_is_skipped(gmail):
    connect(gmail)
    gmail.add("m1", build_email())
    gmail.archive("m1")
    gmail.move_to_inbox("m1")
    assert run(gmail).imported == 0
    assert db.session.query(ArchivedEmail).count() == 0


def test_reconciles_state_changes_of_archived_emails(gmail):
    connect(gmail)
    for mid in ("a1", "a2", "a3"):
        gmail.add(mid, build_email(subject=mid))
        gmail.archive(mid)
    assert run(gmail).imported == 3
    gmail.move_to_inbox("a1")
    gmail.trash("a2")
    gmail.delete("a3")
    result = run(gmail)
    assert result.state_changes == 3
    states = {e.gmail_message_id: e.gmail_state for e in db.session.query(ArchivedEmail)}
    assert states == {"a1": "inbox", "a2": "trashed", "a3": "deleted"}
    # The archived copies are retained.
    assert all(get_storage().exists(e.storage_key) for e in db.session.query(ArchivedEmail))
    # Archiving a1 again flips it back without a duplicate.
    gmail.archive("a1")
    run(gmail)
    assert db.session.query(ArchivedEmail).filter_by(gmail_message_id="a1").one().gmail_state == "archived"
    assert db.session.query(ArchivedEmail).count() == 3


def test_failed_import_is_retried_safely(gmail):
    connect(gmail)
    gmail.add("f1", build_email())
    gmail.archive("f1")
    gmail.fail_get["f1"] = [RuntimeError("connection reset"), RuntimeError("connection reset")]
    r1 = run(gmail)
    assert r1.status == "partial" and r1.failed == 1
    item = db.session.query(PendingImport).one()
    assert item.attempts == 1 and item.status == "pending"
    assert db.session.query(GmailAccount).one().last_sync_status == "partial"
    run(gmail)                  # fails again
    r3 = run(gmail)             # succeeds
    assert r3.imported == 1 and r3.status == "ok"
    assert db.session.query(PendingImport).count() == 0
    assert db.session.query(ArchivedEmail).count() == 1


def test_permanent_failure_and_manual_retry(app, gmail, api):
    app.config["GMAIL_MAX_IMPORT_ATTEMPTS"] = 2
    connect(gmail)
    gmail.add("f1", build_email())
    gmail.archive("f1")
    gmail.fail_get["f1"] = http_error(400, "bad request")
    run(gmail)
    run(gmail)
    assert db.session.query(PendingImport).one().status == "failed"
    run(gmail)  # not retried automatically any more
    assert db.session.query(PendingImport).one().attempts == 2
    assert api.get("/gmail/status").json["status"]["failed"] == 1
    del gmail.fail_get["f1"]
    assert api.post("/gmail/retry").status_code == 202
    assert db.session.query(PendingImport).one().status == "pending"
    assert run(gmail).imported == 1


def test_transient_server_errors_are_retried_in_call(gmail):
    connect(gmail)
    gmail.add("r1", build_email())
    gmail.archive("r1")
    gmail.fail_get["r1"] = [http_error(503, "backend error"), http_error(429, "rate limit")]
    assert run(gmail).imported == 1


def test_interrupted_import_leaves_no_partial_record(gmail, monkeypatch):
    connect(gmail)
    gmail.add("i1", build_email())
    gmail.archive("i1")
    real_store = sync.store_email

    def flaky_store(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sync, "store_email", flaky_store)
    assert run(gmail).failed == 1
    assert db.session.query(ArchivedEmail).count() == 0
    assert db.session.query(PendingImport).one().attempts == 1  # candidate kept for retry
    monkeypatch.setattr(sync, "store_email", real_store)
    assert run(gmail).imported == 1


def test_db_failure_after_file_write_cleans_up_and_retries(gmail, monkeypatch):
    connect(gmail)
    gmail.add("d1", build_email())
    gmail.archive("d1")
    from dms.services import emails as email_svc
    original = email_svc.log_activity

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(email_svc, "log_activity", boom)
    assert run(gmail).failed == 1
    account = db.session.query(GmailAccount).one()
    key = email_svc.email_storage_key(account.id, "d1")
    assert not get_storage().exists(key)  # file removed because the record was not committed
    monkeypatch.setattr(email_svc, "log_activity", original)
    assert run(gmail).imported == 1
    assert get_storage().exists(key)


def test_history_expiry_triggers_resync(gmail):
    connect(gmail)
    gmail.add("old1", build_email())
    gmail.archive("old1")
    gmail.add("old2", build_email())   # still in the inbox
    gmail.expired_below = gmail.history_id + 1  # stored id is now too old
    result = run(gmail)
    assert result.mode == "full_resync" and result.imported == 1
    assert db.session.query(ArchivedEmail).one().gmail_message_id == "old1"
    account = db.session.query(GmailAccount).one()
    assert account.last_full_sync_at is not None
    # Next run is incremental again from the new baseline.
    gmail.expired_below = None
    assert run(gmail).mode == "incremental"


def test_history_pagination(gmail):
    connect(gmail)
    for i in range(7):
        gmail.add(f"p{i}", build_email(subject=f"P{i}"))
        gmail.archive(f"p{i}")
    assert run(gmail).imported == 7


def test_concurrent_run_is_blocked_by_lease(gmail):
    connect(gmail)
    account = db.session.query(GmailAccount).one()
    assert sync._acquire_lease(account.id)
    assert run(gmail).status == "busy"
    sync._release_lease(account.id)
    assert run(gmail).status == "ok"


def test_auth_failures_are_reported(gmail, api):
    connect(gmail)

    class Revoked(FakeGmail):
        def getProfile(self, userId):
            from .conftest import FakeRequest

            def run():
                raise http_error(401, "Invalid Credentials")
            return FakeRequest(run)

    result = run(Revoked())
    assert result.status == "reauth_required"
    assert db.session.query(GmailAccount).one().last_sync_status == "reauth_required"
    assert api.get("/gmail/status").json["status"]["status"] == "reauth_required"


def test_not_connected_without_mailbox(api):
    assert sync.sync_account(12345).status == "not_connected"
    status = api.get("/gmail/status").json["status"]
    assert status["account"] is None and status["connected"] is False
