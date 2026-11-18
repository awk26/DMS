"""Administrator access and item-level access (DMS Permissions Matrix v2).

* Administrators may act on every folder and document (rule 1.3), and each use of access they would
  not have as an ordinary user is written to the audit log ("admin override").
* Archived emails stay private: administrators cannot read another user's email content without a
  share (section 5).
* A personal share on a folder/document is the person's exact access there: lowering it (e.g. from
  Edit to View) takes effect even where their role would grant more (Shared library).
* Only the owner (or an administrator) can share; a non-owner cannot change anybody's access.
"""
import pytest

from dms.extensions import db
from dms.models import ActivityLog
from dms.services.folders import ensure_home

from .conftest import PDF, Api, make_user


# Written before "restricted by default": Contributor/Reader/Auditor keep repository-wide access here.
pytestmark = pytest.mark.usefixtures("legacy_repository")


@pytest.fixture
def alice(app, admin):
    return Api(app, make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


def home(client):
    folder = ensure_home(client.user)
    db.session.commit()
    return folder.id


def share(owner, kind, item_id, person, level):
    r = owner.post(f"/permissions/{kind}/{item_id}/share",
                   json={"principal_type": "user", "principal_id": person.user.id, "level": level})
    assert r.status_code == 200, r.json
    return r


def overrides(user_id):
    db.session.rollback()  # overrides are committed in their own transaction
    return db.session.query(ActivityLog).filter_by(user_id=user_id, action="override").all()


# --------------------------------------------------------------------------- administrator access

def test_admin_can_open_and_download_another_users_private_documents_and_it_is_audited(api, alice):
    alice_home = home(alice)
    doc = alice.doc("salary.pdf", PDF, folder_id=alice_home)
    assert api.get(f"/documents/{doc}").status_code == 200
    assert api.get(f"/documents/{doc}/content").status_code == 200
    assert api.get(f"/folders/{alice_home}").status_code == 200
    names = [d["name"] for d in api.get("/documents", query_string={"limit": 1000}).json["documents"]]
    assert "salary.pdf" in names
    rows = overrides(api.user.id)
    assert {r.details["permission"] for r in rows} >= {"item.view", "item.download"}
    assert all(r.status == "success" and r.category == "admin" for r in rows)
    assert any(r.details.get("owner") == "alice" for r in rows)


def test_admin_using_own_access_is_not_an_override(api):
    folder = api.folder("Admin area")
    doc = api.doc("own.pdf", PDF, folder_id=folder)
    assert api.get(f"/documents/{doc}/content").status_code == 200
    assert api.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 200
    assert overrides(api.user.id) == []


def test_admin_can_rename_and_delete_users_content_and_change_any_share(api, alice, bob):
    folder = alice.folder("Team space")
    doc = alice.doc("team.pdf", PDF, folder_id=folder)
    assert api.patch(f"/documents/{doc}", json={"name": "renamed.pdf"}).status_code == 200
    share(api, "document", doc, bob, "view")                     # Admin may change any item's shares
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert api.delete(f"/documents/{doc}").status_code == 200
    assert any(r.details["permission"] == "item.delete" for r in overrides(api.user.id))


def test_admin_recycle_bin_lists_every_users_deleted_items(api, alice):
    doc = alice.doc("old.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    bin_ = api.get("/recycle-bin").json
    row = next(d for d in bin_["documents"] if d["id"] == doc)
    assert row["can_purge"] is True and bin_["can_purge"] is True


def test_admin_cannot_open_another_users_archived_email(app, api, alice):
    from dms.models import ArchivedEmail, GmailAccount
    account = GmailAccount(user_id=alice.user.id, email="alice@example.com")
    db.session.add(account)
    db.session.flush()
    email = ArchivedEmail(account_id=account.id, gmail_message_id="m1", subject="Private",
                          sender="x@example.com", storage_key="none", size=1, sha256="0" * 64)
    db.session.add(email)
    db.session.commit()
    for path in ("", "/body", "/eml"):
        assert api.get(f"/emails/{email.id}{path}").status_code == 404
    assert api.get("/emails").json["total"] == 0


# --------------------------------------------------------------------------- item-level access

def test_lowering_edit_to_view_takes_effect_in_the_shared_library(alice, bob):
    folder = alice.folder("Projects")          # Shared library: Contributors inherit edit here
    doc = alice.doc("spec.pdf", PDF, folder_id=folder)
    share(alice, "document", doc, bob, "edit")
    assert bob.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 200

    share(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert "edit" not in bob.get(f"/documents/{doc}").json["document"]["permissions"]
    assert bob.patch(f"/documents/{doc}", json={"tags": "y"}).status_code == 403
    assert bob.patch(f"/documents/{doc}", json={"name": "hacked.pdf"}).status_code == 403
    assert bob.get(f"/documents/{doc}/content").status_code == 403
    assert bob.delete(f"/documents/{doc}").status_code == 403


def test_view_only_folder_share_blocks_upload_and_download(alice, bob):
    folder = alice.folder("Board")             # Shared library folder
    doc = alice.doc("minutes.pdf", PDF, folder_id=folder)
    share(alice, "folder", folder, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert bob.get(f"/documents/{doc}/content").status_code == 403
    assert bob.upload("bob.pdf", PDF, folder_id=folder).status_code == 403
    assert bob.get(f"/folders/{folder}/download").status_code in (403, 404) or \
        b"minutes" not in bob.get(f"/folders/{folder}/download").data


def test_view_only_preview_cannot_be_opened_as_a_page(alice, bob):
    doc = alice.doc("view.pdf", PDF, folder_id=home(alice))
    share(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}/preview", headers={"Sec-Fetch-Dest": "iframe"}).status_code == 200
    assert bob.get(f"/documents/{doc}/preview", headers={"Sec-Fetch-Dest": "document"}).status_code == 403
    share(alice, "document", doc, bob, "download")
    assert bob.get(f"/documents/{doc}/preview", headers={"Sec-Fetch-Dest": "document"}).status_code == 200


def test_document_level_share_overrides_folder_share(alice, bob):
    folder = alice.folder("Mixed", parent=home(alice))
    open_doc = alice.doc("open.pdf", PDF, folder_id=folder)
    secret = alice.doc("secret.pdf", PDF, folder_id=folder)
    share(alice, "folder", folder, bob, "edit")
    share(alice, "document", secret, bob, "view")
    assert bob.get(f"/documents/{open_doc}/content").status_code == 200
    assert bob.get(f"/documents/{secret}/content").status_code == 403


def test_a_non_owner_cannot_share_or_change_anothers_access(app, admin, alice, bob):
    carol = Api(app, make_user("carol", ("Contributor",), email="carol@example.com", display_name="Carol"))
    folder = alice.folder("Open")              # Contributors have edit (+ legacy share) here through their role
    doc = alice.doc("o.pdf", PDF, folder_id=folder)
    r = carol.post(f"/permissions/document/{doc}/share",
                   json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert r.status_code == 403, r.json
    assert bob.get(f"/documents/{doc}/content").status_code == 200
    assert "edit" in bob.get(f"/documents/{doc}").json["document"]["permissions"]
