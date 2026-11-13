"""Shared library security regressions (BRD FR-FLD-05, FR-VER-03/04/05, FR-SRC-03, FR-SEC-02).

Each test pins one fix: private items never land in the Shared library on restore, version-level
restrictions hold for restore/copy/ZIP, and hidden folder or document names never leak through search,
breadcrumbs, paths, folder history, shortcut paths, delete errors or access lists.
"""
import io
import zipfile

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


def share(owner, kind, item_id, person, level, number=None):
    path = f"/permissions/{kind}/{item_id}" + (f"/{number}" if number else "") + "/share"
    r = owner.post(path, json={"principal_type": "user", "principal_id": person.user.id, "level": level})
    assert r.status_code == 200, r.json
    return r


def stop_inheritance(owner, kind, item_id):
    r = owner.patch(f"/permissions/{kind}/{item_id}/inheritance", json={"inherit": False, "copy": False})
    assert r.status_code == 200, r.json


def with_restricted_v2(alice, bob):
    """A Shared-library document whose version 2 (current) is view-only for Bob."""
    folder = alice.folder("Projects")
    doc = alice.doc("plan.txt", b"v1 public", folder_id=folder)
    assert alice.version(doc, "plan.txt", b"v2 secret").status_code in (200, 201)
    share(alice, "version", doc, bob, "view", number=2)
    return folder, doc


# --------------------------------------------------------------------------- recycle bin restore

def test_restoring_a_private_file_whose_folder_is_still_deleted_keeps_it_private(alice, bob):
    sub = alice.folder("Sub", home(alice))
    doc = alice.doc("secret.pdf", PDF, folder_id=sub)
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert alice.delete(f"/folders/{sub}").status_code == 200
    assert alice.post(f"/documents/{doc}/restore").status_code == 200
    assert alice.get(f"/documents/{doc}").json["document"]["folder_id"] == home(alice)
    assert bob.get(f"/documents/{doc}").status_code in (403, 404)


def test_restored_folder_goes_to_nearest_live_ancestor(alice, bob):
    outer = alice.folder("Outer", home(alice))
    inner = alice.folder("Inner", outer)
    deepest = alice.folder("Deepest", inner)
    assert alice.delete(f"/folders/{deepest}").status_code == 200
    assert alice.delete(f"/folders/{inner}").status_code == 200
    assert alice.post(f"/folders/{deepest}/restore").status_code == 200
    assert alice.get(f"/folders/{deepest}").json["folder"]["parent_id"] == outer
    assert bob.get(f"/folders/{deepest}").status_code in (403, 404)


# --------------------------------------------------------------------------- retention

def test_admin_cannot_change_retention_of_a_folder_they_cannot_manage(api, alice):
    r = api.patch(f"/folders/{home(alice)}/retention", json={"keep_last": "1"})
    assert r.status_code in (403, 404)
    team = alice.folder("Team")
    assert api.patch(f"/folders/{team}/retention", json={"keep_last": "1"}).status_code in (403, 404)


def test_admin_can_set_retention_on_their_own_folder(api):
    folder = api.folder("Admin area")
    assert api.patch(f"/folders/{folder}/retention", json={"keep_last": "3"}).json["keep_last"] == 3


# --------------------------------------------------------------------------- version-level limits

def test_view_only_version_cannot_be_restored_into_a_downloadable_one(alice, bob):
    _, doc = with_restricted_v2(alice, bob)
    assert alice.version(doc, "plan.txt", b"v3 public").status_code in (200, 201)
    share(alice, "document", doc, bob, "edit")
    share(alice, "version", doc, bob, "view", number=2)
    assert bob.post(f"/documents/{doc}/versions/2/restore", json={}).status_code == 403
    assert bob.post(f"/documents/{doc}/versions/1/restore", json={}).status_code in (200, 201)


def test_copy_respects_a_restricted_current_version(alice, bob):
    folder, doc = with_restricted_v2(alice, bob)
    target = bob.folder("Bob copies")
    assert bob.post(f"/documents/{doc}/copy", json={"folder_id": target}).status_code == 403


def test_zip_export_leaves_out_a_restricted_current_version(alice, bob):
    folder, doc = with_restricted_v2(alice, bob)
    alice.doc("open.txt", b"open", folder_id=folder)
    r = bob.get(f"/folders/{folder}/download")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert any(n.endswith("open.txt") for n in names)
    assert not any(n.endswith("plan.txt") for n in names)


def test_document_json_hides_size_and_hash_of_a_hidden_current_version(alice, bob):
    folder = alice.folder("Specs")
    doc = alice.doc("spec.txt", b"v1", folder_id=folder)
    assert alice.version(doc, "spec.txt", b"v2 hidden").status_code in (200, 201)
    r = alice.patch(f"/permissions/version/{doc}/2/inheritance", json={"inherit": False, "copy": False})
    assert r.status_code == 200, r.json
    data = bob.get(f"/documents/{doc}").json["document"]
    assert data["sha256"] is None and data["size"] == 0 and data["mime_type"] is None
    assert data["version"] is not None  # the label itself is not secret
    assert alice.get(f"/documents/{doc}").json["document"]["sha256"] is not None


# --------------------------------------------------------------------------- search

def _search(client, **params):
    r = client.get("/search", query_string=params)
    assert r.status_code == 200, r.json
    return r.json


def test_search_does_not_match_hidden_parent_folder_names(alice, bob):
    hidden = alice.folder("Zebracodename")
    stop_inheritance(alice, "folder", hidden)
    doc = alice.doc("brief.pdf", PDF, folder_id=hidden)
    share(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200
    ids = [r["id"] for r in _search(bob, q="Zebracodename")["results"]]
    assert doc not in ids
    assert doc in [r["id"] for r in _search(alice, q="Zebracodename")["results"]]
    found = _search(bob, q="brief")
    assert doc in [r["id"] for r in found["results"]]
    assert "Zebracodename" not in str(found["facets"])
    assert "Zebracodename" not in str(found["results"])


def test_search_does_not_match_the_note_of_a_restricted_version(alice, bob):
    folder = alice.folder("Notes")
    doc = alice.doc("memo.txt", b"v1", folder_id=folder)
    assert alice.version(doc, "memo.txt", b"v2", note="quokkaproject merger").status_code in (200, 201)
    # Version 2 stops inheriting: only Alice (who made the change) keeps access to it.
    r = alice.patch(f"/permissions/version/{doc}/2/inheritance", json={"inherit": False, "copy": False})
    assert r.status_code == 200, r.json
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert doc in [r["id"] for r in _search(alice, q="quokkaproject")["results"]]
    assert doc not in [r["id"] for r in _search(bob, q="quokkaproject")["results"]]
    hit = [r for r in _search(bob, q="memo")["results"] if r["id"] == doc]
    assert hit and hit[0]["size"] == 0


# --------------------------------------------------------------------------- names of hidden items

def test_paths_and_breadcrumbs_do_not_name_hidden_folders(alice, bob):
    hidden = alice.folder("Secretparent")
    stop_inheritance(alice, "folder", hidden)
    child = alice.folder("Visible child", hidden)
    share(alice, "folder", child, bob, "view")
    data = bob.get(f"/folders/{child}").json
    assert "Secretparent" not in data["folder"]["path"]
    assert all(c["name"] != "Secretparent" for c in data["breadcrumbs"])


def test_folder_history_hides_events_about_hidden_documents(alice, bob):
    folder = alice.folder("Shared work")
    secret = alice.doc("hidden-name.pdf", PDF, folder_id=folder)
    stop_inheritance(alice, "document", secret)
    alice.doc("public.pdf", PDF, folder_id=folder)
    events = bob.get(f"/folders/{folder}/history").json["events"]
    text = str(events)
    assert "public.pdf" in text and "hidden-name.pdf" not in text
    assert "hidden-name.pdf" in str(alice.get(f"/folders/{folder}/history").json["events"])


def test_delete_error_does_not_name_hidden_documents(alice, bob):
    folder = bob.folder("Bob team")
    secret = bob.doc("unseen-report.pdf", PDF, folder_id=folder)
    stop_inheritance(bob, "document", secret)
    share(bob, "folder", folder, alice, "full")
    r = alice.delete(f"/folders/{folder}")
    assert r.status_code == 400
    assert "unseen-report" not in str(r.json)


def test_document_detail_lists_only_shortcuts_in_visible_folders(alice, bob):
    folder = alice.folder("Main")
    doc = alice.doc("linked.pdf", PDF, folder_id=folder)
    private = alice.folder("Alice private", home(alice))
    assert alice.post(f"/documents/{doc}/links", json={"folder_id": private}).status_code in (200, 201)
    assert any(link["folder_id"] == private for link in alice.get(f"/documents/{doc}").json["links"])
    assert all(link["folder_id"] != private for link in bob.get(f"/documents/{doc}").json["links"])


# --------------------------------------------------------------------------- move and access lists

def test_moving_a_folder_needs_delete_on_everything_inside(alice, bob):
    folder = bob.folder("Bob dept")
    locked = bob.doc("keep.pdf", PDF, folder_id=folder)
    share(bob, "folder", folder, alice, "full")
    share(bob, "document", locked, alice, "edit")      # Alice may edit but not delete this one
    r = alice.patch(f"/folders/{folder}", json={"parent_id": home(alice)})
    assert r.status_code == 400
    assert bob.get(f"/documents/{locked}").status_code == 200


def test_access_list_is_only_shown_to_people_who_can_share_or_manage(app, alice):
    reader = Api(app, make_user("rita", ("Reader",), email="rita@example.com", display_name="Rita"))
    folder = alice.folder("Policies")
    data = reader.get(f"/permissions/folder/{folder}").json
    assert data["entries"] == [] and "view" in data["permissions"]
    assert alice.get(f"/permissions/folder/{folder}").json["entries"]
