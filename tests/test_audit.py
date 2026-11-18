"""Audit log: who did what to which document/folder and when, what changed, tamper protection,
and a personal log: each user reads only their own entries (GET /api/v1/activity)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from dms.extensions import db
from dms.models import ActivityLog
from dms.services.activity import AuditLogImmutable

from .conftest import PDF, Api, make_user


# Written before "restricted by default": Contributor/Reader/Auditor keep repository-wide access here.
pytestmark = pytest.mark.usefixtures("legacy_repository")


@pytest.fixture
def auditor(app, admin):
    return Api(app, make_user("audrey", ("Auditor",), display_name="Audrey"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), display_name="Bob"))


def events(client, **filters):
    r = client.get("/activity", query_string={"per_page": 100, **filters})
    assert r.status_code == 200, r.json
    return r.json["events"]


def one(client, **filters):
    found = events(client, **filters)
    assert len(found) == 1, found
    return found[0]


def test_document_lifecycle_is_audited_with_who_what_which_when(api, auditor):
    folder = api.folder("Contracts")
    other = api.folder("Archive")
    doc = api.doc("deal.pdf", PDF, folder_id=folder)

    up = one(api, action="uploaded", entity_id=doc)
    assert up["entity_type"] == "document" and up["entity_name"] == "deal.pdf"
    assert up["user_name"] == "Admin User" and up["user_id"] == api.user.id and up["username"] == "admin"
    assert up["created_at"].endswith("Z") and up["details"]["folder"] == "Contracts"

    assert api.patch(f"/documents/{doc}", json={"name": "final deal"}).status_code == 200
    renamed = one(api, action="renamed", entity_id=doc)
    assert renamed["details"]["changes"]["name"] == {"old": "deal.pdf", "new": "final deal.pdf"}

    assert api.patch(f"/documents/{doc}", json={"folder_id": other}).status_code == 200
    moved = one(api, action="moved", entity_id=doc)
    assert moved["details"]["changes"]["folder"] == {"old": "Contracts", "new": "Archive"}

    assert api.patch(f"/documents/{doc}", json={"tags": "legal, 2026"}).status_code == 200
    meta = one(api, action="metadata_updated", entity_id=doc)
    assert meta["details"]["changes"]["tags"] == {"old": [], "new": ["2026", "legal"]}

    assert api.delete(f"/documents/{doc}").status_code == 200
    trashed = one(api, action="trashed", entity_id=doc)
    assert trashed["entity_name"] == "final deal.pdf"


def test_folder_changes_are_audited(api, auditor):
    parent = api.folder("Team")
    folder = api.folder("Drafts", parent)
    assert one(api, category="folder", action="created", entity_id=folder)["entity_type"] == "folder"

    assert api.patch(f"/folders/{folder}", json={"name": "Final"}).status_code == 200
    assert one(api, action="renamed", entity_id=folder)["details"]["changes"]["name"] == \
        {"old": "Drafts", "new": "Final"}

    assert api.patch(f"/folders/{folder}", json={"parent_id": None}).status_code == 200
    moved = one(api, category="folder", action="moved", entity_id=folder)
    assert moved["details"]["changes"]["location"] == {"old": "Team", "new": "Root"}

    assert api.delete(f"/folders/{folder}").status_code == 200
    assert one(api, category="folder", action="deleted", entity_id=folder)["entity_name"] == "Final"


def test_sharing_and_permission_changes_record_old_and_new_access(api, auditor, bob):
    doc = api.doc("plan.pdf", PDF)
    r = api.post(f"/permissions/document/{doc}/share",
                 json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert r.status_code == 200, r.json
    shared = one(api, action="shared", entity_type="document", entity_id=doc)
    assert shared["details"]["principal"] == "Bob" and shared["details"]["level"] == "view"
    assert shared["details"]["changes"]["permissions"] == {"old": [], "new": ["view"]}

    api.post(f"/permissions/document/{doc}/share",
             json={"principal_type": "user", "principal_id": bob.user.id, "level": "edit"})
    latest = events(api, action="shared", entity_id=doc)[0]
    assert latest["details"]["changes"]["permissions"] == {"old": ["view"], "new": ["view", "upload", "download", "edit"]}

    assert api.patch(f"/permissions/document/{doc}/inheritance", json={"inherit": False}).status_code == 200
    inh = one(api, action="inheritance_changed", entity_id=doc)
    assert inh["entity_type"] == "document"
    assert inh["details"]["changes"]["inherit_permissions"] == {"old": True, "new": False}


def test_audit_log_shows_contributors_their_own_activity_and_admin_everything(api, auditor, bob):
    api.folder("Admin folder")
    bob.folder("Bob folder")
    # Contributors read only the entries for their own actions.
    mine = events(bob)
    assert mine and all(e["user_id"] == bob.user.id for e in mine)
    assert any(e["entity_name"] == "Bob folder" for e in mine)
    assert not any(e["entity_name"] == "Admin folder" for e in mine)
    # Permissions Matrix v2 (admin.audit): administrators see the whole audit log.
    assert any(e["entity_name"] == "Bob folder" for e in events(api))
    assert events(auditor) == [] or all(e["user_id"] == auditor.user.id for e in events(auditor))
    # Filtering by another user is ignored.
    assert all(e["user_id"] == bob.user.id for e in events(bob, user_id=api.user.id, user="admin"))
    assert auditor.get("/activity", query_string={"date_from": "not-a-date"}).status_code == 400


def test_audit_entries_cannot_be_modified_or_deleted(api):
    api.folder("Protected")
    entry = db.session.query(ActivityLog).first()
    assert entry is not None

    entry.message = "tampered"
    with pytest.raises(AuditLogImmutable):
        db.session.flush()
    db.session.rollback()

    db.session.delete(db.session.query(ActivityLog).first())
    with pytest.raises(AuditLogImmutable):
        db.session.flush()
    db.session.rollback()

    # Database triggers stop changes that bypass the ORM too.
    with pytest.raises(DatabaseError, match="cannot be modified"):
        db.session.execute(text("UPDATE activity_log SET message = 'tampered'"))
    db.session.rollback()
    with pytest.raises(DatabaseError, match="cannot be deleted"):
        db.session.execute(text("DELETE FROM activity_log"))
    db.session.rollback()
    assert db.session.query(ActivityLog).filter(ActivityLog.message == "tampered").count() == 0
    assert db.session.query(ActivityLog).count() >= 1


def test_startup_installs_missing_audit_procedure_and_triggers(app):
    from dms.migrate import _audit_objects
    with db.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER IF EXISTS trg_activity_log_no_delete"))
        conn.execute(text("DROP PROCEDURE IF EXISTS sp_audit_log_add"))
    _audit_objects()
    with db.engine.connect() as conn:
        triggers = set(conn.execute(text(
            "SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE()")).scalars())
        routines = set(conn.execute(text(
            "SELECT routine_name FROM information_schema.routines WHERE routine_schema = DATABASE()")).scalars())
    assert {"trg_activity_log_no_update", "trg_activity_log_no_delete"} <= triggers
    assert "sp_audit_log_add" in routines
