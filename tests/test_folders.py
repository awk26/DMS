from dms.extensions import db
from dms.models import Document, Folder, FolderActivity

from .conftest import PDF, post_json, upload


def mk(client, name, parent=None):
    data = {"name": name}
    if parent:
        data["parent_id"] = parent
    r = post_json(client, "/folders/", data)
    assert r.status_code == 201, r.json
    return r.json["folder_id"]


def test_create_nested_and_browse(client):
    a = mk(client, "Finance")
    b = mk(client, "2025", a)
    doc_id = upload(client, "invoice.pdf", PDF, folder_id=b).json["document_id"]
    assert db.session.get(Document, doc_id).folder_id == b
    page = client.get(f"/folders/{b}").data
    assert b"invoice.pdf" in page and b"Finance" in page  # breadcrumb
    assert db.session.get(Folder, b).path == "Finance / 2025"
    assert b"Finance" in client.get("/folders/").data


def test_duplicate_and_invalid_names(client):
    mk(client, "HR")
    assert post_json(client, "/folders/", {"name": "hr"}).status_code == 400
    assert post_json(client, "/folders/", {"name": "   "}).status_code == 400
    assert post_json(client, "/folders/", {"name": "x", "parent_id": 999}).status_code == 404


def test_rename_and_move_with_history(client):
    a, b = mk(client, "Projects"), mk(client, "Archive")
    c = mk(client, "Apollo", a)
    assert post_json(client, f"/folders/{c}/rename", {"name": "Apollo-2"}).status_code == 200
    assert post_json(client, f"/folders/{c}/move", {"parent_id": b}).status_code == 200
    assert db.session.get(Folder, c).path == "Archive / Apollo-2"
    actions = [e.action for e in db.session.query(FolderActivity).filter_by(folder_id=c)]
    assert {"created", "renamed", "moved"} <= set(actions)
    old_parent = [e.action for e in db.session.query(FolderActivity).filter_by(folder_id=a)]
    assert "subfolder_removed" in old_parent
    page = client.get(f"/folders/{c}/history").data
    assert b"Renamed from" in page and b"Moved from" in page


def test_circular_moves_are_rejected(client):
    a = mk(client, "A")
    b = mk(client, "B", a)
    c = mk(client, "C", b)
    assert post_json(client, f"/folders/{a}/move", {"parent_id": c}).status_code == 400
    assert post_json(client, f"/folders/{a}/move", {"parent_id": a}).status_code == 400
    assert db.session.get(Folder, a).parent_id is None
    # moving to root is fine
    assert post_json(client, f"/folders/{c}/move", {"parent_id": ""}).status_code == 200
    assert db.session.get(Folder, c).parent_id is None


def test_delete_folder_goes_to_recycle_bin_with_contents(client):
    a = mk(client, "Legal")
    b = mk(client, "Sub", a)
    doc_id = upload(client, "nda.pdf", PDF, folder_id=b).json["document_id"]
    assert post_json(client, f"/folders/{a}/delete").status_code == 200   # whole subtree
    assert db.session.get(Folder, a).deleted_at is not None
    assert db.session.get(Folder, b).deleted_at is not None
    assert db.session.get(Document, doc_id).deleted_at is not None
    assert client.get(f"/folders/{a}").status_code == 404
    page = client.get("/recycle-bin").data
    assert b"Legal" in page and b"nda.pdf" not in page   # contents are restored with the folder
    assert post_json(client, f"/folders/{a}/restore").status_code == 200
    assert db.session.get(Folder, b).deleted_at is None
    doc = db.session.get(Document, doc_id)
    assert doc.deleted_at is None and doc.folder_id == b


def test_document_changes_appear_in_folder_history(client):
    f = mk(client, "Ops")
    doc_id = upload(client, "runbook.txt", b"v1", folder_id=f).json["document_id"]
    upload(client, "runbook.txt", b"v2", url=f"/documents/{doc_id}/versions")
    post_json(client, f"/documents/{doc_id}/rename", {"name": "playbook"})
    post_json(client, f"/documents/{doc_id}/move", {"folder_id": ""})
    actions = [e.action for e in db.session.query(FolderActivity).filter_by(folder_id=f)]
    assert {"document_added", "document_updated", "document_renamed", "document_removed"} <= set(actions)
