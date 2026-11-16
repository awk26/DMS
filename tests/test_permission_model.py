"""Shared library permission model (step 2): restricted by default, the upload permission, grants that
expire, and roles used as groups / departments."""
from datetime import timedelta

import pytest

from dms.extensions import db
from dms.models import AccessEntry, Document, Folder, Role, User, utcnow
from dms.services.folders import ensure_home

from .conftest import Api, make_user, open_repository


@pytest.fixture
def finance(app):
    role = Role(name="Finance", description="Finance department")
    db.session.add(role)
    db.session.commit()
    return role


@pytest.fixture
def alice(app, admin, finance):
    return Api(app, make_user("alice", ("Contributor", "Finance"), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


def grant_role(api, kind, item_id, role, level, **extra):
    r = api.post(f"/permissions/{kind}/{item_id}/share",
                 json={"principal_type": "role", "principal_id": role.id, "level": level, **extra})
    assert r.status_code == 200, r.json
    return r


def share(owner, kind, item_id, person, level, **extra):
    return owner.post(f"/permissions/{kind}/{item_id}/share",
                      json={"principal_type": "user", "principal_id": person.user.id, "level": level, **extra})


def department(api, finance, level="upload"):
    folder = api.folder("Finance")
    grant_role(api, "folder", folder, finance, level)
    return folder


# --------------------------------------------------------------------------- restricted by default

def test_predefined_roles_have_no_repository_access(app):
    for name in ("Contributor", "Reader", "Auditor"):
        role = db.session.query(Role).filter_by(name=name).one()
        assert db.session.query(AccessEntry).filter_by(
            target_type="repository", principal_type="role", principal_id=role.id).first() is None


def test_contributor_sees_nothing_and_cannot_create_top_level_folders(api, bob):
    api.folder("Board")
    root = bob.get("/folders/root").json
    assert root["folders"] == [] and root["permissions"] == []
    assert bob.post("/folders", json={"name": "Mine", "parent_id": None}).status_code == 403


def test_self_signup_gets_no_role(app, anon, admin):
    csrf = anon.get("/api/v1/auth/csrf").json["csrf_token"]
    r = anon.post("/api/v1/auth/register", headers={"X-CSRFToken": csrf},
                  json={"username": "newbie", "email": "newbie@example.com", "display_name": "New Bie",
                        "password": "Correct-Horse-9", "password2": "Correct-Horse-9"})
    assert r.status_code == 201, r.json
    assert r.json["user"]["roles"] == []
    assert db.session.query(User).filter_by(username="newbie").one().roles == []


def test_department_role_grant_reaches_only_members(api, alice, bob, finance):
    folder = department(api, finance)
    assert alice.get(f"/folders/{folder}").status_code == 200
    assert bob.get(f"/folders/{folder}").status_code == 404
    assert [f["id"] for f in alice.get("/folders/root").json["folders"]] == [folder]


# --------------------------------------------------------------------------- upload permission

def test_upload_only_member_adds_files_but_cannot_change_others(api, alice, finance):
    folder = department(api, finance)
    theirs = api.doc("budget.txt", b"admin's", folder_id=folder)
    perms = alice.get(f"/permissions/folder/{folder}").json["permissions"]
    assert "upload" in perms and "edit" not in perms

    mine = alice.doc("notes.txt", b"alice's", folder_id=folder)
    alice.folder("Alice sub", folder)
    # What she added is hers ...
    assert alice.patch(f"/documents/{mine}", json={"name": "notes-v2.txt"}).status_code == 200
    assert alice.delete(f"/documents/{mine}").status_code == 200
    # ... what others put there is not.
    assert alice.patch(f"/documents/{theirs}", json={"name": "hacked.txt"}).status_code == 403
    assert alice.version(theirs, "budget.txt", b"overwrite").status_code == 403
    assert alice.patch(f"/folders/{folder}", json={"name": "Renamed"}).status_code == 403


def test_upload_only_member_cannot_replace_a_document_by_uploading_the_same_name(api, alice, finance):
    folder = department(api, finance)
    theirs = api.doc("budget.txt", b"original", folder_id=folder)
    r = alice.upload("budget.txt", b"replacement", folder_id=folder)
    assert r.status_code == 400
    assert len(api.get(f"/documents/{theirs}").json["versions"]) == 1


def test_edit_implies_upload(api, alice, finance):
    folder = department(api, finance, level="edit")
    assert "upload" in alice.get(f"/permissions/folder/{folder}").json["permissions"]


# --------------------------------------------------------------------------- expiring grants

def _expire(target_type, target_id, principal_id):
    entry = db.session.query(AccessEntry).filter_by(target_type=target_type, target_id=target_id,
                                                    principal_type="user", principal_id=principal_id).one()
    entry.expires_at = utcnow() - timedelta(minutes=1)
    db.session.commit()


def test_share_with_end_date_stops_counting_once_it_has_passed(api, bob):
    folder = api.folder("Board")
    end = (utcnow() + timedelta(days=7)).date().isoformat()
    assert share(api, "folder", folder, bob, "view", expires_at=end).status_code == 200
    assert bob.get(f"/folders/{folder}").status_code == 200
    acl = api.get(f"/permissions/folder/{folder}").json["entries"]
    row = next(e for e in acl if e["principal_id"] == bob.user.id and e["principal_type"] == "user")
    assert row["expires_at"].startswith(end) and row["expired"] is False
    assert [i["id"] for i in bob.get("/shared").json["items"]] == [folder]

    _expire("folder", folder, bob.user.id)
    assert bob.get(f"/folders/{folder}").status_code == 404
    assert bob.get("/shared").json["items"] == []
    row = next(e for e in api.get(f"/permissions/folder/{folder}").json["entries"]
               if e["principal_id"] == bob.user.id and e["principal_type"] == "user")
    assert row["expired"] is True


def test_expired_personal_grant_falls_back_to_other_access(api, alice, finance):
    folder = department(api, finance, level="view")
    assert share(api, "folder", folder, alice, "edit",
                 expires_at=(utcnow() + timedelta(days=1)).isoformat()).status_code == 200
    assert "edit" in alice.get(f"/permissions/folder/{folder}").json["permissions"]
    _expire("folder", folder, alice.user.id)
    perms = alice.get(f"/permissions/folder/{folder}").json["permissions"]
    assert "view" in perms and "edit" not in perms


def test_end_date_must_be_in_the_future(api, bob):
    folder = api.folder("Board")
    assert share(api, "folder", folder, bob, "view", expires_at="2001-01-01").status_code == 400
    assert share(api, "folder", folder, bob, "view", expires_at="not a date").status_code == 400


def test_sharer_without_manage_cannot_put_an_end_on_existing_access(api, alice, bob, app):
    folder = api.folder("Board")
    # Alice may share but not manage.
    r = api.post(f"/permissions/folder/{folder}/grant",
                 json={"principal_type": "user", "principal_id": alice.user.id,
                       "permissions": ["view", "download", "edit", "share"]})
    assert r.status_code == 200, r.json
    assert share(api, "folder", folder, bob, "view").status_code == 200
    end = (utcnow() + timedelta(days=2)).date().isoformat()
    assert share(alice, "folder", folder, bob, "download", expires_at=end).status_code == 200
    entry = db.session.query(AccessEntry).filter_by(target_type="folder", target_id=folder,
                                                    principal_type="user", principal_id=bob.user.id).one()
    db.session.refresh(entry)
    assert entry.expires_at is None and "download" in entry.permission_set


# --------------------------------------------------------------------------- roles as groups

def test_admin_manages_role_members(api, bob, finance):
    r = api.post(f"/admin/roles/{finance.id}/members", json={"user_ids": [bob.user.id]})
    assert r.status_code == 200, r.json
    members = api.get(f"/admin/roles/{finance.id}/members").json["members"]
    assert [m["id"] for m in members] == [bob.user.id]
    folder = department(api, finance, level="view")
    assert bob.get(f"/folders/{folder}").status_code == 200
    assert api.delete(f"/admin/roles/{finance.id}/members/{bob.user.id}").status_code == 200
    assert bob.get(f"/folders/{folder}").status_code == 404


def test_role_members_endpoints_are_admin_only(bob, finance):
    assert bob.get(f"/admin/roles/{finance.id}/members").status_code == 403
    assert bob.post(f"/admin/roles/{finance.id}/members", json={"user_ids": [bob.user.id]}).status_code == 403


def test_last_admin_cannot_be_removed_from_admin_role(api, admin):
    role = db.session.query(Role).filter_by(name="Admin").one()
    assert api.delete(f"/admin/roles/{role.id}/members/{admin.id}").status_code == 400


def test_directory_lists_groups_and_share_levels(bob, finance):
    data = bob.get("/directory").json
    assert any(r["name"] == "Finance" and r["description"] == "Finance department" for r in data["roles"])
    assert "upload" in [lvl["key"] for lvl in data["share_levels"]]


# --------------------------------------------------------------------------- upgrade of existing data

def test_upgrade_moves_role_repository_access_onto_top_level_content(app, api, bob):
    from dms.migrate import _restricted_repository
    from dms.services import settings

    open_repository()  # an existing installation: roles still have repository-wide access
    projects = api.folder("Projects")
    hidden = api.folder("Hidden")
    db.session.get(Folder, hidden).inherit_permissions = False
    root_doc = api.doc("policy.txt", b"policy")
    home = ensure_home(bob.user)
    db.session.commit()
    settings.set_value("restricted_repository_migrated", False)
    db.session.commit()

    _restricted_repository()

    contributor = db.session.query(Role).filter_by(name="Contributor").one()
    entries = db.session.query(AccessEntry).filter_by(principal_type="role", principal_id=contributor.id).all()
    assert {(e.target_type, e.target_id) for e in entries} == {("folder", projects), ("document", root_doc)}
    assert all(e.permission_set == {"view", "download", "edit", "share"} for e in entries)
    assert home.parent_id not in {e.target_id for e in entries}
    # Bob keeps exactly what he could open before, and nothing new.
    assert bob.get(f"/folders/{projects}").status_code == 200
    assert bob.get(f"/documents/{root_doc}").status_code == 200
    assert bob.get(f"/folders/{hidden}").status_code == 404
    assert bob.post("/folders", json={"name": "Top", "parent_id": None}).status_code == 403
    # Runs once.
    assert settings.get("restricted_repository_migrated") is True
