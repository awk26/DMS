"""Concurrent uploads and edits against MySQL: database transactions + locking + optimistic locking.

Each test starts two real requests at the same moment in separate threads (each request has its own
app context, database session and MySQL connection). A short delay is injected inside the critical
section so the requests are guaranteed to overlap; without the protections these tests produce
duplicate documents, lost versions or 500 errors.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from dms.extensions import db
from dms.models import Document, DocumentVersion
from dms.services import documents as doc_svc
from dms.services import search

from .conftest import Api, make_user

DELAY = 0.6  # seconds spent inside the critical section by every request


def run_together(*calls):
    """Run the callables at the same moment in separate threads; return their results in order."""
    barrier = threading.Barrier(len(calls))

    def start(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(start, call) for call in calls]
        return [f.result(timeout=60) for f in futures]


def slow(monkeypatch, module, name):
    """Make module.name sleep before running, widening the window in which requests overlap."""
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        time.sleep(DELAY)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)


def fresh(model, **filters):
    db.session.rollback()  # read what the other connections committed
    return db.session.query(model).filter_by(**filters).all()


@pytest.fixture
def users(app, admin):
    """Two independent API clients (separate tokens and test clients) and a shared folder."""
    alice = Api(app, admin)
    bob = Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))
    folder = alice.folder("Team")
    return alice, bob, folder


# ----------------------------------------------------------------------------- uploads by name

def test_same_new_name_uploaded_at_once_creates_one_document_with_two_versions(users, monkeypatch):
    alice, bob, folder = users
    slow(monkeypatch, doc_svc, "_scan")  # between "does the name exist?" and creating the document
    r1, r2 = run_together(lambda: alice.upload("report.txt", b"alice", folder_id=folder),
                          lambda: bob.upload("report.txt", b"bob", folder_id=folder))
    assert {r1.status_code, r2.status_code} == {201}, (r1.json, r2.json)
    assert sorted([r1.json["created"], r2.json["created"]]) == [False, True]
    docs = fresh(Document, folder_id=folder, name="report.txt")
    assert len(docs) == 1                                         # never two documents with one name
    labels = sorted(v.label for v in fresh(DocumentVersion, document_id=docs[0].id))
    assert labels == ["1.0", "1.1"]                               # the later upload became a version


def test_identical_new_file_uploaded_twice_at_once_is_stored_once(users, monkeypatch):
    alice, bob, folder = users
    slow(monkeypatch, doc_svc, "_scan")
    r1, r2 = run_together(lambda: alice.upload("same.txt", b"same", folder_id=folder),
                          lambda: bob.upload("same.txt", b"same", folder_id=folder))
    assert sorted([r1.status_code, r2.status_code]) == [201, 400], (r1.json, r2.json)
    loser = r1 if r1.status_code == 400 else r2
    assert "identical to the current version (1.0)" in loser.json["error"]
    docs = fresh(Document, folder_id=folder, name="same.txt")
    assert len(docs) == 1 and len(fresh(DocumentVersion, document_id=docs[0].id)) == 1


# ----------------------------------------------------------------------------- versions of one document

def test_two_new_versions_at_once_are_numbered_one_after_the_other(users, monkeypatch):
    alice, bob, folder = users
    doc_id = alice.doc("notes.txt", b"v1", folder_id=folder)
    slow(monkeypatch, search, "index_document")  # inside the version transaction, before commit
    r1, r2 = run_together(lambda: alice.version(doc_id, "notes.txt", b"alice's change"),
                          lambda: bob.version(doc_id, "notes.txt", b"bob's change"))
    assert (r1.status_code, r2.status_code) == (201, 201), (r1.json, r2.json)
    assert sorted([r1.json["version"], r2.json["version"]]) == ["1.1", "1.2"]
    versions = fresh(DocumentVersion, document_id=doc_id)
    assert sorted(v.label for v in versions) == ["1.0", "1.1", "1.2"]  # nothing lost, no duplicate number
    doc = fresh(Document, id=doc_id)[0]
    assert doc.current_version.label == "1.2"


def test_same_change_uploaded_twice_at_once_is_saved_once(users, monkeypatch):
    alice, bob, folder = users
    doc_id = alice.doc("notes.txt", b"v1", folder_id=folder)
    slow(monkeypatch, search, "index_document")
    r1, r2 = run_together(lambda: alice.version(doc_id, "notes.txt", b"v2"),
                          lambda: bob.version(doc_id, "notes.txt", b"v2"))
    assert sorted([r1.status_code, r2.status_code]) == [201, 400], (r1.json, r2.json)
    assert sorted(v.label for v in fresh(DocumentVersion, document_id=doc_id)) == ["1.0", "1.1"]


def test_optimistic_locking_on_versions_rejects_the_second_writer(users, monkeypatch):
    """Both started from version 1.0 (base_version=1): the first wins, the second gets 409, not a silent 1.2."""
    alice, bob, folder = users
    doc_id = alice.doc("plan.txt", b"v1", folder_id=folder)
    slow(monkeypatch, search, "index_document")
    r1, r2 = run_together(lambda: alice.version(doc_id, "plan.txt", b"alice", base_version=1),
                          lambda: bob.version(doc_id, "plan.txt", b"bob", base_version=1))
    assert sorted([r1.status_code, r2.status_code]) == [201, 409], (r1.json, r2.json)
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json["code"] == "conflict" and loser.json["current_version"] == "1.1"
    assert "was changed while you were working on it" in loser.json["error"]
    assert sorted(v.label for v in fresh(DocumentVersion, document_id=doc_id)) == ["1.0", "1.1"]


def test_stale_base_version_is_rejected_and_current_one_accepted(users):
    alice, bob, folder = users
    doc_id = alice.doc("plan.txt", b"v1", folder_id=folder)
    assert bob.version(doc_id, "plan.txt", b"bob", base_version=1).json["version"] == "1.1"
    stale = alice.version(doc_id, "plan.txt", b"alice", base_version=1)
    assert stale.status_code == 409 and stale.json["current_version_number"] == 2
    assert alice.version(doc_id, "plan.txt", b"alice", base_version=2).json["version"] == "1.2"
    # Restore honours base_version too.
    assert alice.post(f"/documents/{doc_id}/versions/1/restore", json={"base_version": 2}).status_code == 409
    assert alice.post(f"/documents/{doc_id}/versions/1/restore", json={"base_version": 3}).status_code == 201


def test_upload_waits_for_check_out_made_at_the_same_time(users, monkeypatch):
    """Bob checks the document out while Alice's upload is in flight: either Alice's version is
    saved before the check-out, or she is refused. Never a version on a document Bob holds."""
    alice, bob, folder = users
    doc_id = alice.doc("contract.txt", b"v1", folder_id=folder)
    slow(monkeypatch, doc_svc, "_scan")  # Alice's upload passes the early check-out test, then waits
    r_upload, r_checkout = run_together(lambda: alice.version(doc_id, "contract.txt", b"v2"),
                                        lambda: bob.post(f"/documents/{doc_id}/checkout"))
    assert r_checkout.status_code == 200, r_checkout.json
    assert r_upload.status_code == 400 and "checked out" in r_upload.json["error"], r_upload.json
    assert sorted(v.label for v in fresh(DocumentVersion, document_id=doc_id)) == ["1.0"]


# ----------------------------------------------------------------------------- edits (row_version)

def test_concurrent_check_outs_only_one_wins(users, monkeypatch):
    alice, bob, folder = users
    doc_id = alice.doc("draft.txt", b"v1", folder_id=folder)
    original = doc_svc.log_activity

    def slow_log(category, action, *args, **kwargs):
        if action == "checked_out":
            time.sleep(DELAY)  # both requests have read "not checked out" before either commits
        return original(category, action, *args, **kwargs)

    monkeypatch.setattr(doc_svc, "log_activity", slow_log)
    r1, r2 = run_together(lambda: alice.post(f"/documents/{doc_id}/checkout"),
                          lambda: bob.post(f"/documents/{doc_id}/checkout"))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.json, r2.json)
    holder = fresh(Document, id=doc_id)[0].checked_out_by.username
    winner = alice if r1.status_code == 200 else bob
    assert holder == winner.user.username


def test_concurrent_renames_second_gets_conflict_not_silent_overwrite(users, monkeypatch):
    alice, bob, folder = users
    doc_id = alice.doc("old.txt", b"v1", folder_id=folder)
    slow(monkeypatch, search, "index_document_meta")
    r1, r2 = run_together(lambda: alice.patch(f"/documents/{doc_id}", json={"name": "alice.txt"}),
                          lambda: bob.patch(f"/documents/{doc_id}", json={"name": "bob.txt"}))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.json, r2.json)
    winner = r1 if r1.status_code == 200 else r2
    assert fresh(Document, id=doc_id)[0].name == winner.json["document"]["name"]


def test_row_version_sent_by_client_detects_changes_made_since_loading(users):
    alice, bob, folder = users
    doc_id = alice.doc("spec.txt", b"v1", folder_id=folder)
    loaded = alice.get(f"/documents/{doc_id}").json["document"]["row_version"]
    assert bob.patch(f"/documents/{doc_id}", json={"tags": "urgent"}).status_code == 200
    r = alice.patch(f"/documents/{doc_id}", json={"name": "spec-v2.txt", "row_version": loaded})
    assert r.status_code == 409 and r.json["row_version"] > loaded
    assert fresh(Document, id=doc_id)[0].name == "spec.txt"
    ok = alice.patch(f"/documents/{doc_id}", json={"name": "spec-v2.txt", "row_version": r.json["row_version"]})
    assert ok.status_code == 200 and ok.json["document"]["name"] == "spec-v2.txt"
