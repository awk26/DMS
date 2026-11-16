"""Privacy and item-level access.

* Administrators run the system but get no automatic access to users' private content: other users'
  My Files (documents, downloads, previews, search, ZIP exports, recycle bin) and archived emails
  are hidden from them unless explicitly shared.
* A personal share on a folder/document is the person's exact access there: lowering it (e.g. from
  Full control to Can view) takes effect even where their role would grant more (Shared library).
"""
import pytest

from dms.extensions import db
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


# --------------------------------------------------------------------------- admin privacy

def test_admin_cannot_open_or_download_another_users_private_documents(api, alice):
    alice_home = home(alice)
    doc = alice.doc("salary.pdf", PDF, folder_id=alice_home)
    assert api.get(f"/documents/{doc}").status_code in (403, 404)
    assert api.get(f"/documents/{doc}/content").status_code in (403, 404)
    assert api.get(f"/documents/{doc}/preview").status_code in (403, 404)
    assert api.get(f"/folders/{alice_home}").status_code in (403, 404)
    names = [d["name"] for d in api.get("/documents", query_string={"limit": 1000}).json["documents"]]
    assert "salary.pdf" not in names
    assert api.get("/folders/root").json["folders"] == [] or all(
        f["id"] != alice_home for f in api.get("/folders").json["folders"])
    # Nor can the admin grant themselves access to it.
    r = api.post(f"/permissions/document/{doc}/share",
                 json={"principal_type": "user", "principal_id": api.user.id, "level": "full"})
    assert r.status_code in (403, 404)


def test_admin_sees_private_documents_only_when_shared_and_only_at_that_level(api, alice):
    doc = alice.doc("plan.pdf", PDF, folder_id=home(alice))
    share(alice, "document", doc, api, "view")
    assert api.get(f"/documents/{doc}").status_code == 200
    assert api.get(f"/documents/{doc}/content").status_code == 403


def test_admin_recycle_bin_does_not_list_other_users_private_items(api, alice):
    doc = alice.doc("old.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    bin_ = api.get("/recycle-bin").json
    assert all(d["id"] != doc for d in bin_.get("documents", []))


def test_admin_cannot_see_users_content_in_the_shared_library(api, alice, bob):
    folder = alice.folder("Team space")
    doc = alice.doc("team.pdf", PDF, folder_id=folder)
    # Other Contributors share it through their role; the administrator does not.
    assert bob.get(f"/documents/{doc}").status_code == 200
    for path in ("", "/content", "/preview"):
        assert api.get(f"/documents/{doc}{path}").status_code in (403, 404)
    assert api.get(f"/folders/{folder}").status_code in (403, 404)
    assert api.patch(f"/documents/{doc}", json={"name": "x.pdf"}).status_code in (403, 404)
    assert api.delete(f"/documents/{doc}").status_code in (403, 404)
    assert all(f["id"] != folder for f in api.get("/folders").json["folders"])
    names = [d["name"] for d in api.get("/documents", query_string={"limit": 1000}).json["documents"]]
    assert "team.pdf" not in names
    r = api.post(f"/permissions/document/{doc}/share",
                 json={"principal_type": "user", "principal_id": api.user.id, "level": "full"})
    assert r.status_code in (403, 404)


def test_admin_sees_shared_library_content_only_when_shared(api, alice):
    folder = alice.folder("Team space")
    doc = alice.doc("team.pdf", PDF, folder_id=folder)
    share(alice, "folder", folder, api, "download")
    assert api.get(f"/documents/{doc}/content").status_code == 200
    assert api.patch(f"/documents/{doc}", json={"name": "x.pdf"}).status_code == 403


def test_admin_keeps_running_the_repository_and_own_folders(api):
    folder = api.folder("Admin area")          # top-level folder created by the administrator
    doc = api.doc("own.pdf", PDF, folder_id=folder)
    assert api.get(f"/documents/{doc}/content").status_code == 200
    assert api.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 200


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

def test_lowering_full_control_to_view_takes_effect_in_the_shared_library(alice, bob):
    folder = alice.folder("Projects")          # Shared library: Contributors inherit edit here
    doc = alice.doc("spec.pdf", PDF, folder_id=folder)
    share(alice, "document", doc, bob, "full")
    assert bob.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 200

    share(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert "edit" not in bob.get(f"/documents/{doc}").json["document"]["permissions"]
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


def test_someone_who_can_only_share_cannot_lower_anothers_access(app, admin, alice, bob):
    carol = Api(app, make_user("carol", ("Contributor",), email="carol@example.com", display_name="Carol"))
    folder = alice.folder("Open")              # Contributors have edit+share here through their role
    doc = alice.doc("o.pdf", PDF, folder_id=folder)
    # Carol (share permission, not manage) "shares" view with Bob: Bob keeps his edit access.
    r = carol.post(f"/permissions/document/{doc}/share",
                   json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert r.status_code == 200, r.json
    assert bob.get(f"/documents/{doc}/content").status_code == 200
    assert "edit" in bob.get(f"/documents/{doc}").json["document"]["permissions"]
