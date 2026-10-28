"""Document library: upload workflow, version history, folder moves, folder ZIP export,
attachment import and failure handling."""
import io
import zipfile
from pathlib import Path

import pytest

from dms.extensions import db
from dms.gmail import sync
from dms.models import ArchivedEmail, Document, DocumentVersion, FolderActivity
from dms.services import documents as doc_svc
from dms.storage import get_storage

from .conftest import PDF, PNG, FakeGmail, build_email, post_json, upload


def mkfolder(client, name, parent=None):
    data = {"name": name}
    if parent:
        data["parent_id"] = parent
    return post_json(client, "/folders/", data).json["folder_id"]


def zip_of(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.data[:300]
    assert r.mimetype == "application/zip"
    return r, zipfile.ZipFile(io.BytesIO(r.data))


def stored_document_files(app):
    root = Path(app.config["STORAGE_DIR"]) / "documents"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# ------------------------------------------------------------------- upload workflow

def test_upload_lands_in_chosen_folder_and_is_listed(client):
    folder = mkfolder(client, "HR")
    r = upload(client, "Employee_Handbook.pdf", PDF, folder_id=folder)
    assert r.status_code == 201 and r.json["ok"]
    doc_id = r.json["document_id"]
    assert r.json["redirect"] == f"/folders/{folder}?highlight={doc_id}"
    page = client.get(r.json["redirect"]).data.decode()
    assert "Employee_Handbook.pdf" in page and "is-highlight" in page and ">v1.0<" in page
    assert client.get(f"/documents/{doc_id}/download").data == PDF


def test_upload_to_root(client):
    r = upload(client, "Root.pdf", PDF)
    assert r.json["redirect"] == f"/folders/?highlight={r.json['document_id']}"
    assert db.session.get(Document, r.json["document_id"]).folder_id is None


def test_same_filename_is_a_new_document_not_a_version(client):
    a = upload(client, "Policy.pdf", PDF).json["document_id"]
    b = upload(client, "Policy.pdf", PDF + b"other").json["document_id"]
    assert a != b
    assert db.session.get(Document, b).name == "Policy (1).pdf"
    assert len(db.session.get(Document, a).versions) == 1
    assert len(db.session.get(Document, b).versions) == 1


# ------------------------------------------------------------------- version history

def test_handbook_version_history_scenario(client):
    doc_id = upload(client, "Employee_Handbook.pdf", PDF + b"v1").json["document_id"]
    url = f"/documents/{doc_id}/versions"
    assert upload(client, "Employee_Handbook.pdf", PDF + b"v2", url=url).json["version"] == 2
    assert upload(client, "Employee_Handbook.pdf", PDF + b"v3", url=url).json["version"] == 3

    # One logical document in the listing, showing v3.
    listing = client.get("/documents/").data.decode()
    assert listing.count(">Employee_Handbook.pdf<") == 1 and ">v1.2<" in listing

    page = client.get(f"/documents/{doc_id}").data.decode()
    assert "Latest version" in page and "Previous version" in page and "Original upload" in page
    for n in (1, 2, 3):
        assert client.get(f"/documents/{doc_id}/versions/{n}/download").data == PDF + f"v{n}".encode()

    # Restore v1: becomes v4; v2 and v3 are still there.
    r = post_json(client, f"/documents/{doc_id}/versions/1/restore")
    assert r.status_code == 201 and r.json["version"] == 4
    doc = db.session.get(Document, doc_id)
    assert [v.version_number for v in doc.versions] == [4, 3, 2, 1]
    assert client.get(f"/documents/{doc_id}/download").data == PDF + b"v1"
    assert client.get(f"/documents/{doc_id}/versions/3/download").data == PDF + b"v3"
    assert "Restored from version 1.0" in client.get(f"/documents/{doc_id}").data.decode()


def test_listing_offers_version_actions(client):
    doc_id = upload(client, "Plan.txt", b"a").json["document_id"]
    page = client.get("/documents/").data.decode()
    assert f'href="/documents/{doc_id}#versions"' in page                 # Version history
    assert f'data-action="/documents/{doc_id}/versions"' in page           # Upload new version


def test_move_keeps_identity_and_history(client):
    a, b = mkfolder(client, "A"), mkfolder(client, "B")
    doc_id = upload(client, "Spec.txt", b"one", folder_id=a).json["document_id"]
    upload(client, "Spec.txt", b"two", url=f"/documents/{doc_id}/versions")
    before = [(v.id, v.version_number, v.sha256, v.storage_key) for v in db.session.get(Document, doc_id).versions]
    assert post_json(client, f"/documents/{doc_id}/move", {"folder_id": b}).status_code == 200
    doc = db.session.get(Document, doc_id)
    assert doc.id == doc_id and doc.folder_id == b
    assert [(v.id, v.version_number, v.sha256, v.storage_key) for v in doc.versions] == before  # no new version
    assert client.get(f"/documents/{doc_id}/versions/1/download").data == b"one"
    assert client.get(f"/documents/{doc_id}/download").data == b"two"


def test_version_operations_on_missing_or_trashed_document(client):
    doc_id = upload(client, "x.txt", b"x").json["document_id"]
    post_json(client, f"/documents/{doc_id}/delete")
    assert upload(client, "x.txt", b"y", url=f"/documents/{doc_id}/versions").status_code == 404
    assert post_json(client, f"/documents/{doc_id}/versions/1/restore").status_code == 404
    assert upload(client, "x.txt", b"y", url="/documents/999/versions").status_code == 404


# ------------------------------------------------------------------- folder ZIP download

def test_folder_zip_contains_nested_structure_and_current_versions(client):
    top = mkfolder(client, "Projects")
    sub = mkfolder(client, "Apollo", top)
    deep = mkfolder(client, "Specs", sub)
    mkfolder(client, "Empty", top)
    other = mkfolder(client, "Unrelated")
    upload(client, "readme.txt", b"top level", folder_id=top)
    d = upload(client, "plan.txt", b"old plan", folder_id=sub).json["document_id"]
    upload(client, "plan.txt", b"new plan", url=f"/documents/{d}/versions")
    upload(client, "diagram.png", PNG, folder_id=deep)
    upload(client, "secret.txt", b"not in this folder", folder_id=other)
    gone = upload(client, "trashed.txt", b"deleted", folder_id=top).json["document_id"]
    post_json(client, f"/documents/{gone}/delete")

    r, zf = zip_of(client, f"/folders/{top}/download")
    assert "Projects.zip" in r.headers["Content-Disposition"]
    names = set(zf.namelist())
    assert names == {"Projects/readme.txt", "Projects/Apollo/plan.txt", "Projects/Apollo/Specs/diagram.png",
                     "Projects/Empty/"}
    assert zf.read("Projects/Apollo/plan.txt") == b"new plan"          # current version only
    assert zf.read("Projects/Apollo/Specs/diagram.png") == PNG
    assert zf.testzip() is None


def test_folder_zip_from_listing_and_detail_views(client):
    top = mkfolder(client, "Reports")
    sub = mkfolder(client, "2025", top)
    root_page = client.get("/folders/").data.decode()
    assert f'href="/folders/{top}/download"' in root_page               # folder card in the listing
    detail = client.get(f"/folders/{top}").data.decode()
    assert f'href="/folders/{top}/download"' in detail                  # folder detail header
    assert f'href="/folders/{sub}/download"' in detail                  # subfolder card
    assert "Download Folder (ZIP)" in detail


def test_empty_folder_zip(client):
    f = mkfolder(client, "Nothing here")
    _, zf = zip_of(client, f"/folders/{f}/download")
    assert zf.namelist() == ["Nothing here/"]


def test_zip_with_version_history_is_opt_in(client):
    f = mkfolder(client, "Legal")
    d = upload(client, "nda.txt", b"v1", folder_id=f).json["document_id"]
    upload(client, "nda.txt", b"v2", url=f"/documents/{d}/versions")
    _, zf = zip_of(client, f"/folders/{f}/download")
    assert zf.namelist() == ["Legal/nda.txt"]
    r, zf = zip_of(client, f"/folders/{f}/download?versions=1")
    assert "with versions" in r.headers["Content-Disposition"]
    assert zf.read("Legal/nda.txt") == b"v2"
    assert zf.read("Legal/_version_history/nda.txt/v1.0 - nda.txt") == b"v1"
    assert zf.read("Legal/_version_history/nda.txt/v1.1 - nda.txt") == b"v2"


def test_library_zip_and_no_sensitive_files(app, client):
    # Put look-alike sensitive files next to the storage to prove only documents are exported.
    Path(app.config["GMAIL_CREDENTIALS_FILE"]).write_text('{"installed": {"client_secret": "x"}}')
    Path(app.config["GMAIL_TOKEN_FILE"]).write_text('{"token": "x"}')
    account_email = build_email()
    from dms.models import GmailAccount
    from dms.services.emails import store_email
    acc = GmailAccount(email="t@example.com")
    db.session.add(acc)
    db.session.commit()
    store_email(acc, "abc", account_email)

    f = mkfolder(client, "Finance")
    upload(client, "budget.txt", b"numbers", folder_id=f)
    upload(client, "root.txt", b"root doc")
    _, zf = zip_of(client, "/folders/download")
    names = zf.namelist()
    assert sorted(names) == ["Documents/Finance/budget.txt", "Documents/root.txt"]
    # Exactly the two documents: no token, credentials, database or archived .eml entries.
    blob = b"".join(zf.read(n) for n in names if not n.endswith("/"))
    assert b"client_secret" not in blob and b'"token"' not in blob and b"Quarterly report" not in blob
    assert blob == b"numbers" + b"root doc" or blob == b"root doc" + b"numbers"
    # internal storage keys never appear
    keys = [v.storage_key for v in db.session.query(DocumentVersion)]
    assert not any(k in n for k in keys for n in names)


def test_zip_handles_duplicate_and_unsafe_names(client):
    f = mkfolder(client, "Mixed")
    mkfolder(client, "report.txt", f)                       # a folder with a document-like name
    upload(client, "report.txt", b"the document", folder_id=f)
    doc = upload(client, "a.txt", b"one", folder_id=f).json["document_id"]
    upload(client, "a_.txt", b"two", folder_id=f)
    # Force names that collide after sanitising, and a traversal-looking name.
    d = db.session.get(Document, doc)
    d.name = "a?.txt"
    evil = upload(client, "evil.txt", b"evil", folder_id=f).json["document_id"]
    db.session.get(Document, evil).name = "../../evil.txt"
    db.session.commit()
    _, zf = zip_of(client, f"/folders/{f}/download")
    names = zf.namelist()
    assert len(names) == len(set(n.lower() for n in names))  # no duplicates
    assert all(n.startswith("Mixed/") and ".." not in n.split("/") for n in names)
    assert "Mixed/report.txt/" in names and "Mixed/report (1).txt" in names
    assert zf.read("Mixed/report (1).txt") == b"the document"
    contents = sorted(zf.read(n) for n in names if n.startswith("Mixed/a"))
    assert contents == [b"one", b"two"]


def test_zip_of_missing_folder_and_missing_file(app, client):
    assert client.get("/folders/999/download").status_code == 404
    f = mkfolder(client, "Broken")
    d = upload(client, "lost.txt", b"x", folder_id=f).json["document_id"]
    get_storage().delete(db.session.get(Document, d).current_version.storage_key)
    _, zf = zip_of(client, f"/folders/{f}/download")
    assert "Broken/MISSING_FILES.txt" in zf.namelist()
    assert b"lost.txt" in zf.read("Broken/MISSING_FILES.txt")


def test_zip_does_not_persist_files(app, client, tmp_path):
    f = mkfolder(client, "Temp")
    upload(client, "a.txt", b"a", folder_id=f)
    before = {p for p in tmp_path.rglob("*")}
    zip_of(client, f"/folders/{f}/download")
    after = {p for p in tmp_path.rglob("*")}
    assert not [p for p in after - before if p.suffix == ".zip"]


# ------------------------------------------------------------------- failure handling

def test_failed_upload_leaves_no_record_or_orphan_file(app, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(doc_svc, "log_activity", boom)
    r = upload(client, "doomed.pdf", PDF)
    assert r.status_code == 500 and r.json["ok"] is False
    assert db.session.query(Document).count() == 0
    assert db.session.query(DocumentVersion).count() == 0
    assert stored_document_files(app) == []


def test_failed_new_version_keeps_previous_state(app, client, monkeypatch):
    doc_id = upload(client, "keep.txt", b"v1").json["document_id"]
    files_before = stored_document_files(app)

    def boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(doc_svc, "log_activity", boom)
    r = upload(client, "keep.txt", b"v2", url=f"/documents/{doc_id}/versions")
    assert r.status_code == 500
    monkeypatch.undo()
    doc = db.session.get(Document, doc_id)
    assert [v.version_number for v in doc.versions] == [1]
    assert doc.current_version.version_number == 1
    assert stored_document_files(app) == files_before
    assert client.get(f"/documents/{doc_id}/download").data == b"v1"


def test_invalid_upload_creates_nothing(app, client):
    assert upload(client, "bad.pdf", b"not pdf").status_code == 400
    assert upload(client, "tool.exe", b"MZ").status_code == 400
    assert db.session.query(Document).count() == 0 and stored_document_files(app) == []


# ------------------------------------------------------------------- email archive stays separate

@pytest.fixture
def archived(app):
    monkey_sleep = sync.time.sleep
    sync.time.sleep = lambda s: None
    gmail = FakeGmail()
    sync.run_sync(service=gmail)
    raw = build_email()
    gmail.add("e1", raw)
    gmail.archive("e1")
    assert sync.run_sync(service=gmail).imported == 1
    yield gmail, raw, db.session.query(ArchivedEmail).one()
    sync.time.sleep = monkey_sleep


def test_attachment_import_does_not_touch_archived_email(client, archived):
    gmail, raw, record = archived
    pdf = next(a for a in record.attachments if a.filename == "report.pdf")
    folder = mkfolder(client, "From email")
    r = post_json(client, "/documents/import-attachment",
                  {"email_id": record.id, "part_index": pdf.part_index, "folder_id": folder,
                   "idempotency_key": "import-key-0001"})
    assert r.status_code == 201
    doc = db.session.get(Document, r.json["document_id"])
    assert doc.name == "report.pdf" and doc.folder_id == folder
    assert client.get(f"/documents/{doc.id}/download").data == PDF
    assert "Imported from archived email" in doc.current_version.note
    # retry does not duplicate
    r2 = post_json(client, "/documents/import-attachment",
                   {"email_id": record.id, "part_index": pdf.part_index, "idempotency_key": "import-key-0001"})
    assert r2.status_code == 200 and db.session.query(Document).count() == 1

    # The archive is untouched: same bytes, same record, still downloadable, no new emails.
    assert get_storage().read_bytes(record.storage_key) == raw
    assert client.get(f"/emails/{record.id}/eml").data == raw
    assert db.session.query(ArchivedEmail).count() == 1
    # Versioning the imported document never affects the email.
    upload(client, "report.pdf", PDF + b"edited", url=f"/documents/{doc.id}/versions")
    assert get_storage().read_bytes(record.storage_key) == raw
    # Repeated synchronisation still creates no duplicates.
    gmail.move_to_inbox("e1")
    gmail.archive("e1")
    sync.run_sync(service=gmail)
    sync.run_sync(service=gmail)
    assert db.session.query(ArchivedEmail).count() == 1
    # Emails are not documents: they do not show in the library or its ZIP export.
    assert b"Quarterly report" not in client.get("/documents/").data
    _, zf = zip_of(client, "/folders/download")
    assert not any(n.endswith(".eml") for n in zf.namelist())


def test_attachment_import_errors(client, archived):
    _, _, record = archived
    inline = next(a for a in record.attachments if a.is_inline)  # image/png part -> allowed type
    assert post_json(client, "/documents/import-attachment",
                     {"email_id": 999, "part_index": inline.part_index}).status_code == 404
    assert post_json(client, "/documents/import-attachment",
                     {"email_id": record.id, "part_index": 999}).status_code == 404


def test_email_page_offers_save_to_library(client, archived):
    _, _, record = archived
    page = client.get(f"/emails/{record.id}").data.decode()
    assert "data-import-attachment" in page and "Save to Document Library" in page


def test_navigation_separates_library_and_archive(client):
    page = client.get("/").data.decode()
    assert "Document Library" in page and "Email Archive" in page
    assert page.index("Document Library") < page.index("All Documents") < page.index("Email Archive") \
        < page.index("Archived Emails")


def test_folder_download_is_not_folder_history(client):
    f = mkfolder(client, "Audit")
    client.get(f"/folders/{f}/download")
    actions = [e.action for e in db.session.query(FolderActivity).filter_by(folder_id=f)]
    assert actions == ["created"]  # downloads are not structural changes
