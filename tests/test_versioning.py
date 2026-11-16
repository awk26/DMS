"""Uploading a file whose name already exists in the folder: the user is asked first (HTTP 409,
nothing stored); once they confirm (on_duplicate=replace) it adds a new version (1.0 -> 1.1 -> 1.2 ...)."""
import io

import pytest

from .conftest import Api, make_user

# Written before "restricted by default": Contributor/Reader/Auditor keep repository-wide access here.
pytestmark = pytest.mark.usefixtures("legacy_repository")


def versions(api, doc_id):
    detail = api.get(f"/documents/{doc_id}").json
    return detail["document"]["version"], [(v["label"], v["current"]) for v in detail["versions"]]


def test_same_name_upload_with_changes_creates_next_version_and_keeps_all(api):
    folder = api.folder("Ops")
    first = api.upload("commandS.txt", b"line 1\n", folder_id=folder)
    assert first.status_code == 201 and first.json["created"] is True and first.json["versioned"] is False
    doc_id = first.json["document"]["id"]

    second = api.upload("commandS.txt", b"line 1\nline 2\n", folder_id=folder, on_duplicate="replace")
    assert second.status_code == 201
    assert second.json["created"] is False and second.json["versioned"] is True and second.json["version"] == "1.1"
    assert second.json["document"]["id"] == doc_id

    third = api.upload("commandS.txt", b"line 1\nline 2\nline 3\n", folder_id=folder, on_duplicate="replace")
    assert third.json["version"] == "1.2" and third.json["document"]["id"] == doc_id

    current, history = versions(api, doc_id)
    assert current == "1.2"
    assert sorted(label for label, _ in history) == ["1.0", "1.1", "1.2"]
    assert [label for label, is_current in history if is_current] == ["1.2"]
    # Every version is preserved with its own content.
    assert api.get(f"/documents/{doc_id}/content?version=1").data == b"line 1\n"
    assert api.get(f"/documents/{doc_id}/content?version=2").data == b"line 1\nline 2\n"
    assert api.get(f"/documents/{doc_id}/content").data == b"line 1\nline 2\nline 3\n"
    # Still one document in the folder.
    assert [d["name"] for d in api.get("/documents").json["documents"]] == ["commandS.txt"]


def test_identical_content_is_not_saved_again(api):
    folder = api.folder("Ops")
    doc_id = api.doc("report.txt", b"same", folder_id=folder)
    r = api.upload("report.txt", b"same", folder_id=folder, on_duplicate="replace")
    assert r.status_code == 400
    assert "identical to the current version (1.0)" in r.json["error"]
    assert versions(api, doc_id) == ("1.0", [("1.0", True)])


def test_content_identical_to_an_older_version_is_not_saved_again(app, api):
    folder = api.folder("Ops")
    doc_id = api.doc("commandS.txt", b"first", folder_id=folder)
    assert api.upload("commandS.txt", b"second", folder_id=folder, on_duplicate="replace").json["version"] == "1.1"
    stored_before = sorted(p.name for p in (app.config["STORAGE_DIR"]).rglob("*") if p.is_file())

    r = api.upload("commandS.txt", b"first", folder_id=folder, on_duplicate="replace")          # same as v1.0
    assert r.status_code == 400
    assert "identical to version 1.0" in r.json["error"] and "Restore" in r.json["error"]
    assert versions(api, doc_id)[0] == "1.1"
    # Nothing new left behind in storage.
    assert sorted(p.name for p in (app.config["STORAGE_DIR"]).rglob("*") if p.is_file()) == stored_before

    # The same refusal applies to "Upload New Version" on the document.
    r = api.version(doc_id, "commandS.txt", b"second")                  # same as current v1.1
    assert r.status_code == 400 and "identical to the current version (1.1)" in r.json["error"]

    # Restore is the way to bring old content back; it still works.
    restored = api.post(f"/documents/{doc_id}/versions/1/restore", json={})
    assert restored.status_code == 201, restored.json
    assert api.get(f"/documents/{doc_id}/content").data == b"first"
    assert versions(api, doc_id)[0] == "1.2"


def test_name_match_is_case_insensitive_and_per_folder(api):
    a, b = api.folder("A"), api.folder("B")
    doc_a = api.doc("Plan.txt", b"v1", folder_id=a)
    r = api.upload("plan.TXT", b"v2", folder_id=a, on_duplicate="replace")
    assert r.json["versioned"] is True and r.json["document"]["id"] == doc_a
    other = api.upload("Plan.txt", b"elsewhere", folder_id=b)            # another folder: a new document
    assert other.json["created"] is True and other.json["document"]["id"] != doc_a


def test_multi_file_upload_versions_existing_names(api):
    folder = api.folder("Batch")
    existing = api.doc("a.txt", b"old", folder_id=folder)
    form = {"folder_id": str(folder), "on_duplicate": "replace",
            "file": [(io.BytesIO(b"new"), "a.txt"), (io.BytesIO(b"b"), "b.txt")]}
    r = api.post("/documents", data=form, content_type="multipart/form-data")
    assert r.status_code == 201 and r.json["errors"] == []
    assert r.json["versioned"] == [{"document_id": existing, "name": "a.txt", "version": "1.1"}]
    assert sorted(d["name"] for d in r.json["documents"]) == ["a.txt", "b.txt"]


def test_uploader_needs_edit_permission_on_the_existing_document(app, api):
    folder = api.folder("Shared")
    doc_id = api.doc("policy.txt", b"v1", folder_id=folder)
    # A contributor can upload into the folder but this document's inheritance is broken: they cannot edit it.
    assert api.patch(f"/permissions/document/{doc_id}/inheritance",
                     json={"inherit": "0", "copy": "0"}).status_code == 200
    carl = Api(app, make_user("carl", ("Contributor",), email="carl@example.com"))
    r = carl.upload("policy.txt", b"v2", folder_id=folder, on_duplicate="replace")
    assert r.status_code == 400 and "do not have permission to add versions" in r.json["error"]
    assert versions(api, doc_id)[0] == "1.0"


def test_document_checked_out_by_someone_else_is_not_versioned(app, api):
    folder = api.folder("Locked")
    doc_id = api.doc("contract.txt", b"v1", folder_id=folder)
    assert api.post(f"/documents/{doc_id}/checkout").status_code == 200
    carl = Api(app, make_user("carl", ("Contributor",), email="carl@example.com"))
    r = carl.upload("contract.txt", b"v2", folder_id=folder, on_duplicate="replace")
    assert r.status_code == 400 and "checked out" in r.json["error"]
    assert versions(api, doc_id)[0] == "1.0"


def test_retried_request_does_not_add_a_second_version(api):
    folder = api.folder("Retry")
    api.doc("notes.txt", b"v1", folder_id=folder)
    key = "retry-key-0001"
    first = api.upload("notes.txt", b"v2", folder_id=folder, idempotency_key=key, on_duplicate="replace")
    again = api.upload("notes.txt", b"v2", folder_id=folder, idempotency_key=key, on_duplicate="replace")
    assert first.json["version"] == "1.1"
    assert again.status_code == 200 and again.json["versioned"] is False
    assert versions(api, first.json["document"]["id"])[0] == "1.1"
