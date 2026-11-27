"""Connecting a Gmail address that another DMS user connected before (sp_gmail_account_connect).

* While another user has the address ACTIVE, connecting it is refused (unchanged).
* After they disconnect, a different user may connect it. They get a row of their own with a fresh
  sync, and the previous owner's archived emails stay with the previous owner.
* Reconnecting your own address keeps your row and its sync position (unchanged).
"""
import pytest
from sqlalchemy import select, text

from dms import sp
from dms.validation import ValidationError
from dms.extensions import db
from dms.models import GmailAccount
from dms.services.emails import store_email

from .conftest import Api, build_email, make_user

ADDRESS = "shared.mailbox@gmail.example"


@pytest.fixture
def alice(app, admin):
    return Api(app, make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


def connect(user, address=ADDRESS, token="sealed-token"):
    row = sp.call_one("sp_gmail_account_connect", user.id, address, token, user.username, None)
    db.session.commit()
    return row["account_id"]


def disconnect(user):
    sp.call_one("sp_gmail_account_disconnect", user.id, user.username, None)
    db.session.commit()


def rows(address=ADDRESS):
    db.session.expire_all()
    return db.session.execute(select(GmailAccount).where(GmailAccount.email == address)
                              .order_by(GmailAccount.id)).scalars().all()


def test_active_mailbox_of_another_user_still_blocks(alice, bob):
    connect(alice.user)
    with pytest.raises(ValidationError, match="already connected by another DMS user"):
        connect(bob.user)
    db.session.rollback()
    [row] = rows()
    assert row.user_id == alice.user.id and row.is_active is True


def test_disconnected_mailbox_can_be_connected_by_another_user(alice, bob):
    alice_account = connect(alice.user)
    acc = db.session.get(GmailAccount, alice_account)
    acc.history_id = "12345"
    db.session.commit()
    record, _ = store_email(acc, "m1", build_email(subject="Alice only"))
    disconnect(alice.user)

    bob_account = connect(bob.user)

    assert bob_account != alice_account
    alice_row, bob_row = rows()
    # Alice's row and emails are untouched; Bob's row starts a fresh sync.
    assert alice_row.id == alice_account and alice_row.user_id == alice.user.id and alice_row.is_active is False
    assert alice_row.history_id == "12345"
    assert bob_row.id == bob_account and bob_row.user_id == bob.user.id and bob_row.is_active is True
    assert bob_row.history_id is None and bob_row.last_sync_status == "never"
    # Privacy: the previous owner's archived email is not visible to the new owner.
    assert alice.get(f"/emails/{record.id}").status_code == 200
    assert bob.get(f"/emails/{record.id}").status_code == 404
    assert bob.get("/emails").json["emails"] == []


def test_new_owner_blocks_the_previous_owner_while_active(alice, bob):
    connect(alice.user)
    disconnect(alice.user)
    connect(bob.user)
    with pytest.raises(ValidationError, match="already connected by another DMS user"):
        connect(alice.user)
    db.session.rollback()


def test_previous_owner_gets_their_own_row_back(alice, bob):
    alice_account = connect(alice.user)
    disconnect(alice.user)
    connect(bob.user)
    disconnect(bob.user)

    assert connect(alice.user) == alice_account
    alice_row, bob_row = rows()
    assert alice_row.is_active is True and bob_row.is_active is False
    assert len(rows()) == 2                                  # no duplicate rows


def test_reconnecting_own_mailbox_keeps_the_row_and_sync_position(alice):
    first = connect(alice.user)
    acc = db.session.get(GmailAccount, first)
    acc.history_id = "999"
    db.session.commit()
    disconnect(alice.user)

    assert connect(alice.user) == first
    [row] = rows()
    assert row.history_id == "999" and row.last_sync_status == "connected" and row.is_active is True


def test_ownerless_legacy_row_is_still_claimed(alice):
    legacy = GmailAccount(email=ADDRESS, user_id=None, is_active=False)
    db.session.add(legacy)
    db.session.commit()

    assert connect(alice.user) == legacy.id
    [row] = rows()
    assert row.user_id == alice.user.id and row.is_active is True


def test_only_the_per_owner_unique_key_remains(app):
    keys = db.session.execute(text(
        "SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols "
        "FROM information_schema.statistics WHERE table_schema = DATABASE() "
        "AND table_name = 'gmail_accounts' AND non_unique = 0 GROUP BY index_name")).all()
    by_cols = {cols: name for name, cols in keys}
    assert "email" not in by_cols                          # the old global UNIQUE(email) is gone
    assert by_cols.get("email,user_id") == "uq_gmail_accounts_email_user"
