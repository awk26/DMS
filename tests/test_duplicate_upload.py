"""Uploading a file whose name already exists in the folder.

* Different content: stored straight away as the next version (no prompt); earlier versions are kept.
* Identical content (same name and same bytes as a stored version): HTTP 409 ``duplicate_document``,
  nothing stored. Cancel = the client does nothing; the document is unchanged.
  Replace (``on_duplicate=replace`` + ``allow_identical=1``): the identical old version is deleted and
  the upload takes its place as the current version, so there are never two identical versions.
"""
import io

import pytest

from .conftest import Api, make_user

# Written before "restricted by default": Contributor/Reader/Auditor keep repository-wide access here.
pytestmark = pytest.mark.usefixtures("legacy_repository")


def versions(api, doc_id):
    detail = api.get(f"/documents/{doc_id}").json
    return detail["document"]["version"], [(v["label"], v["current"]) for v in detail["versions"]]


def stored_files(app):
    return sorted(p.name for p in (app.config["STORAGE_DIR"]).rglob("*") if p.is_file())


def replace(api, name, data, folder):
    return api.upload(name, data, folder_id=folder, on_duplicate="replace", allow_identical="1")


def test_changed_content_with_the_same_name_becomes_a_new_version_without_asking(api):
    folder = api.folder("Ops")
    doc_id = api.doc("commandS.txt", b"v1", folder_id=folder)
    r = api.upload("commandS.txt", b"v1 + changes", folder_id=folder)
    assert r.status_code == 201, r.json
    assert r.json["versioned"] is True and r.json["version"] == "1.1" and r.json["document"]["id"] == doc_id
    assert versions(api, doc_id) == ("1.1", [("1.1", True), ("1.0", False)])
    assert api.get(f"/documents/{doc_id}/content?version=1").data == b"v1"     # history kept


def test_identical_upload_asks_and_cancel_changes_nothing(app, api):
    folder = api.folder("Same")
    doc_id = api.doc("commandS.txt", b"same content", folder_id=folder)
    before = stored_files(app)

    r = api.upload("CommandS.TXT", b"same content", folder_id=folder)
    assert r.status_code == 409 and r.json["code"] == "duplicate_document"
    dup = r.json["duplicate"]
    assert dup["document_id"] == doc_id and dup["identical"] is True
    assert dup["identical_version"] == "1.0" and dup["identical_is_current"] is True
    assert dup["can_replace"] is True and r.json["duplicates"][0]["filename"] == "CommandS.TXT"
    # Cancel = nothing is resent: unchanged, nothing stored.
    assert versions(api, doc_id) == ("1.0", [("1.0", True)])
    assert stored_files(app) == before


def test_replacing_an_identical_document_removes_the_old_copy(app, api):
    folder = api.folder("Same")
    doc_id = api.doc("commandS.txt", b"same content", folder_id=folder)
    old_files = stored_files(app)

    r = replace(api, "commandS.txt", b"same content", folder)
    assert r.status_code == 201, r.json
    assert r.json["document"]["id"] == doc_id and r.json["version"] == "1.0"
    # One version only: the old identical copy was replaced, not kept alongside.
    assert versions(api, doc_id) == ("1.0", [("1.0", True)])
    assert api.get(f"/documents/{doc_id}/content").data == b"same content"
    assert set(old_files).isdisjoint(stored_files(app)) and len(stored_files(app)) == len(old_files)


def test_replacing_keeps_the_other_versions(api):
    folder = api.folder("Hist")
    doc_id = api.doc("plan.txt", b"first", folder_id=folder)
    assert api.upload("plan.txt", b"second", folder_id=folder).json["version"] == "1.1"

    # Identical to the current version 1.1: 1.1 is replaced, 1.0 stays.
    assert api.upload("plan.txt", b"second", folder_id=folder).status_code == 409
    assert replace(api, "plan.txt", b"second", folder).json["version"] == "1.1"
    assert versions(api, doc_id) == ("1.1", [("1.1", True), ("1.0", False)])
    assert api.get(f"/documents/{doc_id}/content?version=1").data == b"first"

    # Identical to the older version 1.0: 1.0 is removed and the upload becomes current.
    r = api.upload("plan.txt", b"first", folder_id=folder)
    assert r.status_code == 409 and r.json["duplicate"]["identical_is_current"] is False
    assert replace(api, "plan.txt", b"first", folder).json["version"] == "1.2"
    current, history = versions(api, doc_id)
    assert current == "1.2" and sorted(label for label, _ in history) == ["1.1", "1.2"]
    assert api.get(f"/documents/{doc_id}/content").data == b"first"


def test_replace_without_allow_identical_is_still_refused(api):
    folder = api.folder("Old")
    doc_id = api.doc("report.txt", b"same", folder_id=folder)
    r = api.upload("report.txt", b"same", folder_id=folder, on_duplicate="replace")
    assert r.status_code == 400 and "identical to the current version (1.0)" in r.json["error"]
    assert versions(api, doc_id) == ("1.0", [("1.0", True)])


def test_new_names_are_uploaded_without_asking(api):
    folder = api.folder("Fresh")
    r = api.upload("new.txt", b"x", folder_id=folder)
    assert r.status_code == 201 and r.json["created"] is True


def test_same_idempotency_key_can_be_reused_after_the_user_confirms(api):
    folder = api.folder("Retry")
    doc_id = api.doc("notes.txt", b"v1", folder_id=folder)
    key = "confirm-key-0001"
    assert api.upload("notes.txt", b"v1", folder_id=folder, idempotency_key=key).status_code == 409
    r = api.upload("notes.txt", b"v1", folder_id=folder, idempotency_key=key, on_duplicate="replace",
                   allow_identical="1")
    assert r.status_code == 201 and r.json["document"]["id"] == doc_id


def test_multi_file_upload_versions_changed_files_and_lists_identical_ones(api):
    folder = api.folder("Batch")
    a = api.doc("a.txt", b"a-old", folder_id=folder)
    b = api.doc("b.txt", b"b-same", folder_id=folder)
    form = {"folder_id": str(folder), "file": [(io.BytesIO(b"a-new"), "a.txt"), (io.BytesIO(b"b-same"), "b.txt"),
                                               (io.BytesIO(b"c"), "c.txt")]}
    r = api.post("/documents", data=form, content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    assert r.json["versioned"] == [{"document_id": a, "name": "a.txt", "version": "1.1"}]
    assert sorted(d["name"] for d in r.json["documents"]) == ["a.txt", "c.txt"]
    assert [(d["filename"], d["document_id"]) for d in r.json["duplicates"]] == [("b.txt", b)]
    assert versions(api, b)[0] == "1.0"

    only_same = {"folder_id": str(folder), "file": [(io.BytesIO(b"b-same"), "b.txt"), (io.BytesIO(b"c"), "c.txt")]}
    r = api.post("/documents", data=only_same, content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["code"] == "duplicate_document"
    assert sorted(d["filename"] for d in r.json["duplicates"]) == ["b.txt", "c.txt"]


def test_prompt_says_when_the_user_cannot_replace(app, api):
    folder = api.folder("Locked")
    doc_id = api.doc("contract.txt", b"v1", folder_id=folder)
    assert api.post(f"/documents/{doc_id}/checkout").status_code == 200
    carl = Api(app, make_user("carl", ("Contributor",), email="carl@example.com"))
    r = carl.upload("contract.txt", b"v1", folder_id=folder)
    assert r.status_code == 409
    assert r.json["duplicate"]["can_replace"] is False and "checked out" in r.json["duplicate"]["reason"]
    # Changed content on a document someone else checked out is refused, as before.
    r = carl.upload("contract.txt", b"v2", folder_id=folder)
    assert r.status_code == 400 and "checked out" in r.json["error"]
    assert versions(api, doc_id)[0] == "1.0"


def test_invalid_on_duplicate_value_is_rejected(api):
    r = api.upload("x.txt", b"x", on_duplicate="overwrite")
    assert r.status_code == 400 and "on_duplicate" in r.json["error"]


def test_rename_and_upload_keeps_the_identical_file_as_a_separate_document(api):
    folder = api.folder("Copies")
    doc_id = api.doc("invoice.txt", b"same", folder_id=folder)
    assert api.upload("invoice.txt", b"same", folder_id=folder).status_code == 409
    # The user picks "Rename & upload" in the prompt: the same bytes under a new name.
    r = api.upload("invoice (client B).txt", b"same", folder_id=folder, on_duplicate="new", tags="legal")
    assert r.status_code == 201 and r.json["created"] is True, r.json
    new_id = r.json["document"]["id"]
    assert new_id != doc_id and r.json["document"]["name"] == "invoice (client B).txt"
    assert api.get(f"/documents/{new_id}/content").data == b"same"
    assert versions(api, doc_id) == ("1.0", [("1.0", True)])   # the original is untouched


def test_rename_to_a_name_that_is_already_taken_is_refused(api):
    folder = api.folder("Taken")
    api.doc("a.txt", b"one", folder_id=folder)
    other = api.doc("b.txt", b"two", folder_id=folder)
    r = api.upload("b.txt", b"one", folder_id=folder, on_duplicate="new")
    assert r.status_code == 400 and "Choose a different name" in r.json["error"]
    assert versions(api, other) == ("1.0", [("1.0", True)])   # never turned into a version of b.txt
