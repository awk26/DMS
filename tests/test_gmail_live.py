"""LIVE Gmail integration check (read-only). Skipped unless RUN_LIVE_GMAIL=1.

Uses the real credentials.json / token.json of this installation. It never
modifies the mailbox (the app only has the gmail.readonly scope). It verifies:
authentication, history access, and that a real message fetched with
format="raw" is stored byte-for-byte as an .eml file.

    set RUN_LIVE_GMAIL=1 && venv\\Scripts\\python -m pytest tests/test_gmail_live.py -v -s
"""
import base64
import os
import shutil

import pytest

from dms.config import BASE_DIR

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_GMAIL") != "1",
                                reason="live Gmail test; set RUN_LIVE_GMAIL=1 to run")


def test_live_gmail_raw_message_round_trip(app, tmp_path):
    token = BASE_DIR / "token.json"
    if not token.exists():
        pytest.skip("token.json not present")
    # Work on a copy of the token so the real one is only touched via refresh semantics.
    shutil.copy(token, tmp_path / "token.json")
    shutil.copy(BASE_DIR / "credentials.json", tmp_path / "credentials.json")

    from dms.extensions import db
    from dms.gmail.auth import build_service
    from dms.gmail.sync import _execute, run_sync
    from dms.models import GmailAccount
    from dms.services.emails import parse_message, store_email
    from dms.storage import get_storage

    service = build_service()
    profile = _execute(service.users().getProfile(userId="me"))
    print("Connected account:", profile["emailAddress"])
    assert profile["historyId"]

    result = run_sync(service=service, trigger="live-test")
    assert result.status in ("ok", "partial"), result.message
    account = db.session.query(GmailAccount).one()
    history = _execute(service.users().history().list(userId="me", startHistoryId=account.history_id))
    assert "historyId" in history

    listing = _execute(service.users().messages().list(userId="me", maxResults=1))
    if not listing.get("messages"):
        pytest.skip("mailbox is empty")
    mid = listing["messages"][0]["id"]
    msg = _execute(service.users().messages().get(userId="me", id=mid, format="raw"))
    raw = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
    record, _ = store_email(account, mid, raw, thread_id=msg.get("threadId"), label_ids=msg.get("labelIds"),
                            internal_date_ms=msg.get("internalDate"), source="manual")
    stored = get_storage().read_bytes(record.storage_key)
    assert stored == raw
    parsed = parse_message(stored)
    assert parsed.get("From") is not None
    print("Stored", record.size, "bytes for message", mid, "attachments:", record.attachment_count)
