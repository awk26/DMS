"""Permanent delete from the recycle bin removes an item from everywhere: database rows and stored files."""
from datetime import date

from dms.extensions import db
from dms.models import Document, DocumentVersion, Folder
from dms.storage import get_storage
from dms.validity.models import Notification, ValidityException
from dms.validity.notifications import notify

from .conftest import PDF
from .test_permissions_matrix import alice, bob, home  # noqa: F401 - fixtures


def versions_of(doc_id):
    db.session.rollback()
    return db.session.query(DocumentVersion).filter_by(document_id=doc_id).all()


def test_purging_a_folder_removes_its_documents_from_the_database_and_storage(alice):
    folder = alice.folder("Certificates", home(alice))
    doc = alice.doc("certificate.pdf", PDF, folder_id=folder)
    assert alice.version(doc, "certificate.pdf", PDF + b"v2").status_code in (200, 201)
    storage = get_storage()
    versions = versions_of(doc)
    keys = [v.storage_key for v in versions]
    assert len(keys) == 2 and all(storage.exists(k) for k in keys)

    # Files and rows that belong to the document besides its versions.
    rendition = f"renditions/{versions[0].sha256}.pdf"
    storage.save_bytes(rendition, PDF)
    evidence = storage.new_document_key()
    storage.save_bytes(evidence, b"port acceptance letter")
    db.session.add(ValidityException(document_id=doc, scope="all", reason="Accepted by the port",
                                     start_date=date.today(), evidence_storage_key=evidence))
    notify(alice.user.id, category="validity", kind="countdown", title="Expiring", message="Expires soon.",
           dedupe_key=f"test:{doc}", entity_type="document", entity_id=doc)
    db.session.commit()

    assert alice.delete(f"/folders/{folder}").status_code == 200
    # Still restorable from the recycle bin: nothing is removed yet.
    assert all(storage.exists(k) for k in keys + [rendition, evidence])

    assert alice.delete(f"/folders/{folder}/purge").status_code == 200
    db.session.rollback()
    assert db.session.get(Folder, folder) is None and db.session.get(Document, doc) is None
    assert db.session.query(DocumentVersion).filter_by(document_id=doc).count() == 0
    assert db.session.query(ValidityException).filter_by(document_id=doc).count() == 0
    assert db.session.query(Notification).filter_by(entity_type="document", entity_id=doc).count() == 0
    for key in keys + [rendition, evidence]:
        assert not storage.exists(key), key


def test_emptying_the_recycle_bin_removes_stored_files(alice):
    doc = alice.doc("report.pdf", PDF, folder_id=home(alice))
    keys = [v.storage_key for v in versions_of(doc)]
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert alice.delete("/recycle-bin").json["purged_documents"] == 1
    assert not any(get_storage().exists(k) for k in keys)


def test_a_preview_rendition_still_used_by_another_document_is_kept(alice):
    gone = alice.doc("copy-1.pdf", PDF, folder_id=home(alice))
    kept = alice.doc("copy-2.pdf", PDF, folder_id=home(alice))   # same content, same rendition
    rendition = f"renditions/{versions_of(gone)[0].sha256}.pdf"
    get_storage().save_bytes(rendition, PDF)
    assert alice.delete(f"/documents/{gone}").status_code == 200
    assert alice.delete(f"/documents/{gone}/purge").status_code == 200
    assert get_storage().exists(rendition)
    assert all(get_storage().exists(v.storage_key) for v in versions_of(kept))
