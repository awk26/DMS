"""Sharing through the API: share levels, "Shared with me" and "Shared by me" (GET /api/v1/shared)."""
import pytest

from dms.extensions import db
from dms.services.folders import ensure_home

from .conftest import PDF, Api, make_user


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


def test_share_levels_control_what_the_recipient_can_do(alice, bob):
    doc = alice.doc("report.pdf", PDF, folder_id=home(alice))
    assert bob.get(f"/documents/{doc}").status_code == 404           # private until shared

    share(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert bob.get(f"/documents/{doc}/content").status_code == 403    # view only: no download

    share(alice, "document", doc, bob, "edit")
    assert bob.get(f"/documents/{doc}/content").data == PDF


def test_shared_folder_gives_access_to_its_contents(alice, bob):
    team = alice.folder("Team", parent=home(alice))
    doc = alice.doc("inside.pdf", PDF, folder_id=team)
    share(alice, "folder", team, bob, "edit")
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert bob.upload("bob.pdf", PDF, folder_id=team).status_code == 201
    assert [(i["type"], i["item"]["name"]) for i in bob.get("/shared").json["items"]] == [("folder", "Team")]


def test_shared_with_me_lists_document_whose_id_matches_recipients_home_folder(alice, bob):
    """Regression: a shared document was hidden when its id equalled the recipient's home *folder* id."""
    alice_home, bob_home = home(alice), home(bob)
    doc = 0
    while doc < bob_home:
        doc = alice.doc(f"file{doc}.pdf", PDF, folder_id=alice_home)
    assert doc == bob_home
    folder = alice.folder("Team", parent=alice_home)
    share(alice, "document", doc, bob, "view")
    share(alice, "folder", folder, bob, "edit")

    received = bob.get("/shared").json
    assert {(i["type"], i["id"]) for i in received["items"]} == {("document", doc), ("folder", folder)}
    assert received["counts"]["shared_with_me"] == {"folders": 1, "documents": 1, "total": 2}
    assert received["shared_by_me"] == []


def test_shared_by_me_lists_items_with_recipients_and_counts(app, alice, bob):
    carol = Api(app, make_user("carol", ("Contributor",), email="carol@example.com", display_name="Carol"))
    alice_home = home(alice)
    doc = alice.doc("plan.pdf", PDF, folder_id=alice_home)
    folder = alice.folder("Projects", parent=alice_home)
    share(alice, "document", doc, bob, "view")
    share(alice, "document", doc, carol, "edit")
    share(alice, "folder", folder, bob, "download")

    sent = alice.get("/shared").json
    assert sent["items"] == []
    assert sent["counts"]["shared_by_me"] == {"folders": 1, "documents": 1, "total": 2}
    rows = {(r["type"], r["id"]): r for r in sent["shared_by_me"]}
    assert sorted((p["name"], p["level"]) for p in rows[("document", doc)]["shared_with"]) == \
        [("Bob", "view"), ("Carol", "edit")]
    assert rows[("folder", folder)]["shared_with"] == [{"id": bob.user.id, "name": "Bob", "level": "download"}]

    # Items in the recycle bin drop out of both lists.
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert alice.get("/shared").json["counts"]["shared_by_me"]["documents"] == 0
    assert bob.get("/shared").json["counts"]["shared_with_me"] == {"folders": 1, "documents": 0, "total": 1}


def test_own_files_are_not_listed_as_shared(alice):
    alice.doc("mine.pdf", PDF, folder_id=home(alice))
    data = alice.get("/shared").json
    assert data["items"] == [] and data["shared_by_me"] == []
