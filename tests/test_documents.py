import io
import zipfile

from dms.extensions import db
from dms.models import Document, DocumentVersion
from dms.storage import get_storage

from .conftest import PDF, PNG, post_json, upload


def make_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def test_upload_and_download_original_bytes(client):
    r = upload(client, "Contract.pdf", PDF)
    assert r.status_code == 201, r.json
    doc = db.session.get(Document, r.json["document_id"])
    assert doc.name == "Contract.pdf" and doc.current_version.version_number == 1
    d = client.get(f"/documents/{doc.id}/download")
    assert d.status_code == 200
    assert d.data == PDF
    assert "attachment" in d.headers["Content-Disposition"]


def test_preview_pdf_and_image_inline(client):
    pdf_id = upload(client, "a.pdf", PDF).json["document_id"]
    img_id = upload(client, "b.png", PNG).json["document_id"]
    p = client.get(f"/documents/{pdf_id}/preview")
    assert p.status_code == 200 and p.mimetype == "application/pdf" and p.data == PDF
    assert "attachment" not in p.headers.get("Content-Disposition", "")
    i = client.get(f"/documents/{img_id}/preview")
    assert i.status_code == 200 and i.mimetype == "image/png"
    page = client.get(f"/documents/{pdf_id}")
    assert b"preview-frame" in page.data


def test_unsupported_preview_offers_download(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    doc_id = upload(client, "bundle.zip", buf.getvalue()).json["document_id"]
    assert client.get(f"/documents/{doc_id}/preview").status_code == 404
    page = client.get(f"/documents/{doc_id}")
    assert b"Preview not available" in page.data and b"Download original" in page.data


def test_text_preview_is_escaped(client):
    doc_id = upload(client, "notes.txt", b"<script>alert(1)</script> hello").json["document_id"]
    page = client.get(f"/documents/{doc_id}").data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_rejects_invalid_uploads(client):
    assert upload(client, "malware.exe", b"MZ\x90\x00").status_code == 400
    assert upload(client, "fake.pdf", b"not a pdf at all").status_code == 400
    assert upload(client, "fake.docx", b"PK\x03\x04garbage").status_code == 400
    assert upload(client, "empty.txt", b"").status_code == 400
    assert upload(client, "image.svg", b"<svg/>").status_code == 400
    r = upload(client, "big.pdf", PDF + b"0" * (3 * 1024 * 1024))
    assert r.status_code == 413
    assert db.session.query(Document).count() == 0


def test_filename_path_traversal_is_neutralised(client, app):
    r = upload(client, "../../etc/passwd.txt", b"hello")
    doc = db.session.get(Document, r.json["document_id"])
    assert doc.name == "passwd.txt"
    key = doc.current_version.storage_key
    assert key.startswith("documents/") and "passwd" not in key
    path = get_storage().local_path(key)
    assert str(path).startswith(str(app.config["STORAGE_DIR"].resolve()))


def test_duplicate_names_get_suffix(client):
    a = upload(client, "report.pdf", PDF).json["document_id"]
    b = upload(client, "report.pdf", PDF).json["document_id"]
    assert db.session.get(Document, b).name == "report (1).pdf"
    assert a != b


def test_upload_retry_is_idempotent(client):
    key = "11111111-2222-3333-4444-555555555555"
    r1 = upload(client, "x.pdf", PDF, key=key)
    r2 = upload(client, "x.pdf", PDF, key=key)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json["document_id"] == r2.json["document_id"]
    assert db.session.query(Document).count() == 1
    assert db.session.query(DocumentVersion).count() == 1


def test_versioning_preserves_every_version(client):
    doc_id = upload(client, "plan.txt", b"version one").json["document_id"]
    r = upload(client, "plan.txt", b"version two", url=f"/documents/{doc_id}/versions", note="edits")
    assert r.status_code == 201 and r.json["version"] == 2
    r = upload(client, "plan-final.txt", b"version three", url=f"/documents/{doc_id}/versions")
    assert r.json["version"] == 3
    doc = db.session.get(Document, doc_id)
    assert [v.version_number for v in doc.versions] == [3, 2, 1]
    assert client.get(f"/documents/{doc_id}/download").data == b"version three"
    assert client.get(f"/documents/{doc_id}/versions/1/download").data == b"version one"
    assert client.get(f"/documents/{doc_id}/versions/2/download").data == b"version two"
    # every version has its own stored file
    keys = {v.storage_key for v in doc.versions}
    assert len(keys) == 3 and all(get_storage().exists(k) for k in keys)


def test_version_rules(client):
    doc_id = upload(client, "plan.txt", b"same").json["document_id"]
    url = f"/documents/{doc_id}/versions"
    assert upload(client, "plan.txt", b"same", url=url).status_code == 400          # identical content
    assert upload(client, "plan.pdf", PDF, url=url).status_code == 400              # different type
    key = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert upload(client, "plan.txt", b"new", url=url, key=key).status_code == 201
    assert upload(client, "plan.txt", b"new", url=url, key=key).status_code == 200  # retried request
    assert db.session.query(DocumentVersion).filter_by(document_id=doc_id).count() == 2


def test_restore_creates_new_version(client):
    doc_id = upload(client, "spec.txt", b"original").json["document_id"]
    upload(client, "spec.txt", b"changed", url=f"/documents/{doc_id}/versions")
    r = post_json(client, f"/documents/{doc_id}/versions/1/restore", {"idempotency_key": "restore-key-0001"})
    assert r.status_code == 201 and r.json["version"] == 3
    r2 = post_json(client, f"/documents/{doc_id}/versions/1/restore", {"idempotency_key": "restore-key-0001"})
    assert r2.status_code == 200
    doc = db.session.get(Document, doc_id)
    assert doc.current_version.version_number == 3 and doc.current_version.restored_from == 1
    assert client.get(f"/documents/{doc_id}/download").data == b"original"
    assert client.get(f"/documents/{doc_id}/versions/2/download").data == b"changed"
    # restoring the current version is refused
    assert post_json(client, f"/documents/{doc_id}/versions/3/restore").status_code == 400


def test_restore_detects_corrupted_version(client):
    doc_id = upload(client, "spec.txt", b"original").json["document_id"]
    upload(client, "spec.txt", b"changed", url=f"/documents/{doc_id}/versions")
    v1 = db.session.query(DocumentVersion).filter_by(document_id=doc_id, version_number=1).one()
    get_storage().local_path(v1.storage_key).write_bytes(b"tampered")
    r = post_json(client, f"/documents/{doc_id}/versions/1/restore")
    assert r.status_code == 400 and "integrity" in r.json["error"]


def test_rename_move_trash_restore_purge(client):
    folder_id = post_json(client, "/folders/", {"name": "Contracts"}).json["folder_id"]
    doc_id = upload(client, "draft.pdf", PDF).json["document_id"]
    assert post_json(client, f"/documents/{doc_id}/rename", {"name": "Final"}).status_code == 200
    assert db.session.get(Document, doc_id).name == "Final.pdf"
    assert post_json(client, f"/documents/{doc_id}/move", {"folder_id": folder_id}).status_code == 200
    assert db.session.get(Document, doc_id).folder_id == folder_id
    assert post_json(client, f"/documents/{doc_id}/move", {"folder_id": 999}).status_code == 404

    assert post_json(client, f"/documents/{doc_id}/delete").status_code == 200
    assert b"Final.pdf" not in client.get("/documents/").data
    assert b"Final.pdf" in client.get("/recycle-bin").data
    assert post_json(client, f"/documents/{doc_id}/restore").status_code == 200
    assert db.session.get(Document, doc_id).deleted_at is None

    # purge requires trash first, then removes stored files
    assert post_json(client, f"/documents/{doc_id}/purge").status_code == 400
    upload(client, "x.pdf", PDF + b"v2", url=f"/documents/{doc_id}/versions")
    keys = [v.storage_key for v in db.session.get(Document, doc_id).versions]
    post_json(client, f"/documents/{doc_id}/delete")
    assert post_json(client, f"/documents/{doc_id}/purge").status_code == 200
    assert db.session.get(Document, doc_id) is None
    assert not any(get_storage().exists(k) for k in keys)


def test_listing_search_filter_sort(client):
    upload(client, "alpha.pdf", PDF)
    upload(client, "beta.txt", b"b" * 50)
    upload(client, "gamma.png", PNG)
    assert b"alpha.pdf" in client.get("/documents/?q=alp").data
    assert b"beta.txt" not in client.get("/documents/?q=alp").data
    page = client.get("/documents/?type=Image").data
    assert b"gamma.png" in page and b"alpha.pdf" not in page
    page = client.get("/documents/?sort=name&dir=asc").data.decode()
    assert page.index("alpha.pdf") < page.index("beta.txt") < page.index("gamma.png")
    assert client.get("/documents/?from=2000-01-01&to=bad-date&sort=size").status_code == 200


def test_dashboard_shows_real_counts(client):
    page = client.get("/").data
    assert b'data-stat="documents">0<' in page
    upload(client, "one.pdf", PDF)
    upload(client, "two.txt", b"x")
    post_json(client, "/folders/", {"name": "F1"})
    page = client.get("/").data
    assert b'data-stat="documents">2<' in page
    assert b'data-stat="folders">1<' in page
    assert b'data-stat="emails">0<' in page
    assert b"one.pdf" in page


def test_missing_document_404(client):
    assert client.get("/documents/12345").status_code == 404
    assert client.get("/documents/12345/download").status_code == 404
    doc_id = upload(client, "a.pdf", PDF).json["document_id"]
    assert client.get(f"/documents/{doc_id}/versions/9/download").status_code == 404
