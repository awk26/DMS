"""Audit status (step 3): every entry has an outcome (success | denied | failed); refused attempts,
document views and downloads are recorded. Each user still reads only their own entries."""
import pytest

from dms.extensions import db
from dms.models import ActivityLog

from .conftest import PDF, Api, make_user


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


def events(client, **filters):
    r = client.get("/activity", query_string={"per_page": 100, **filters})
    assert r.status_code == 200, r.json
    return r.json["events"]


def share(owner, kind, item_id, person, level):
    r = owner.post(f"/permissions/{kind}/{item_id}/share",
                   json={"principal_type": "user", "principal_id": person.user.id, "level": level})
    assert r.status_code == 200, r.json


def test_successful_actions_have_status_success(api):
    api.folder("Board")
    created = events(api, action="created")
    assert created and all(e["status"] == "success" for e in created)


def test_denied_attempt_is_logged_for_the_user_who_tried(api, bob):
    folder = api.folder("Board")
    doc = api.doc("minutes.pdf", PDF, folder_id=folder)
    share(api, "document", doc, bob, "view")
    assert bob.delete(f"/documents/{doc}").status_code == 403

    denied = events(bob, status="denied")
    assert len(denied) == 1
    entry = denied[0]
    assert entry["action"] == "access_denied" and entry["entity_type"] == "document"
    assert entry["entity_id"] == doc and entry["entity_name"] == "Document 'minutes.pdf'"
    assert entry["details"]["permission"] == "item.delete" and entry["user_id"] == bob.user.id


def test_denied_attempt_on_a_hidden_item_does_not_store_its_name(api, bob):
    folder = api.folder("Secret board")
    assert bob.get(f"/folders/{folder}").status_code == 404
    entry = events(bob, status="denied")[0]
    assert entry["entity_name"] is None and "Secret" not in entry["message"]
    assert entry["entity_type"] == "folder" and entry["entity_id"] == folder


def test_denied_entry_survives_the_failed_request(api, bob):
    folder = api.folder("Board")
    assert bob.post("/folders", json={"name": "Sneaky", "parent_id": folder}).status_code == 404
    db.session.rollback()  # end the test's own snapshot so the separately committed entry is visible
    rows = db.session.query(ActivityLog).filter_by(user_id=bob.user.id, status="denied").all()
    assert len(rows) == 1 and rows[0].details["permission"] == "folder.create_child"


def test_opening_and_downloading_a_document_are_logged(api):
    doc = api.doc("report.pdf", PDF)
    assert api.get(f"/documents/{doc}").status_code == 200
    assert api.get(f"/documents/{doc}/content").status_code == 200
    viewed = events(api, action="viewed", entity_id=doc)
    downloaded = events(api, action="downloaded", entity_id=doc)
    assert len(viewed) == 1 and viewed[0]["status"] == "success"
    assert len(downloaded) == 1 and downloaded[0]["details"]["version"] == "1.0"


def test_failed_sign_in_has_status_failed(app, anon, admin):
    csrf = anon.get("/api/v1/auth/csrf").json["csrf_token"]
    r = anon.post("/api/v1/auth/login", headers={"X-CSRFToken": csrf},
                  json={"username": admin.username, "password": "wrong-password"})
    assert r.status_code in (400, 401, 403)
    db.session.rollback()
    row = db.session.query(ActivityLog).filter_by(action="login_failed").one()
    assert row.status == "failed"


def test_status_filter_only_returns_own_entries_for_contributors(api, bob):
    folder = api.folder("Board")
    assert bob.get(f"/folders/{folder}").status_code == 404
    assert all(e["user_id"] == bob.user.id for e in events(bob))
    # Administrators see every user's entries, including refusals (admin.audit).
    assert any(e["user_id"] == bob.user.id for e in events(api, status="denied"))
