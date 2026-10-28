"""Acceptance tests for the Phase 1 BRD functional requirements (FR-FLD, FR-DOC, FR-VER, FR-SRC, FR-MET, FR-SEC)."""
import io
import re
import time
import zipfile
from datetime import timedelta

import pytest

from dms.extensions import db
from dms.models import (AccessEntry, ActivityLog, Document, DocumentVersion, Folder, Role, User, UserDevice, utcnow)
from dms.services import settings
from dms.storage import get_storage

from .conftest import PDF, PNG, login_as, make_user, post_json, upload

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def mk(client, name, parent=None):
    r = post_json(client, "/folders/", {"name": name, "parent_id": parent or ""})
    assert r.status_code == 201, r.json
    return r.json["folder_id"]


def docx(text="Hello"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def set_setting(app, key, value):
    settings.set_value(key, value)
    db.session.commit()


def role(name):
    return db.session.query(Role).filter_by(name=name).one()


@pytest.fixture
def reader(app):
    return make_user("rita", ("Reader",), email="rita@example.com")


@pytest.fixture
def reader_client(app, reader):
    return login_as(app.test_client(), reader)


@pytest.fixture
def contributor(app):
    return make_user("carl", ("Contributor",))


# =========================================================================== 7.1 folders

def test_fld01_create_rename_move_copy_delete_unlimited_depth(client):
    parent = None
    ids = []
    for i in range(12):  # deep hierarchy
        parent = mk(client, f"Level {i}", parent)
        ids.append(parent)
    assert db.session.get(Folder, ids[-1]).path.count("/") == 11
    doc = upload(client, "deep.txt", b"deep content", folder_id=ids[-1]).json["document_id"]
    r = post_json(client, f"/folders/{ids[5]}/copy", {"parent_id": ""})
    assert r.status_code == 201
    copy = db.session.get(Folder, r.json["folder_id"])
    assert copy.name == "Level 5" and copy.parent_id is None
    # the copy contains the whole subtree and an independent copy of the document at 1.0
    node = copy
    while node.live_children:
        node = node.live_children[0]
    copied = [d for d in node.documents]
    assert len(copied) == 1 and copied[0].id != doc and copied[0].current_version.label == "1.0"
    assert client.get(f"/documents/{copied[0].id}/download").data == b"deep content"
    # copying into itself is refused
    assert post_json(client, f"/folders/{ids[0]}/copy", {"parent_id": ids[3]}).status_code == 400
    assert post_json(client, f"/folders/{ids[1]}/rename", {"name": "Renamed"}).status_code == 200
    assert post_json(client, f"/folders/{ids[2]}/move", {"parent_id": ""}).status_code == 200
    assert post_json(client, f"/folders/{ids[0]}/delete").status_code == 200


def test_fld03_naming_conventions(client, app):
    for bad in ("a/b", "bad:name", "what?", "x|y", 'q"t', "trailing.", "CON"):
        r = post_json(client, "/folders/", {"name": bad})
        assert r.status_code == 400, bad
    mk(client, "Finance")
    assert post_json(client, "/folders/", {"name": "finance"}).status_code == 400  # duplicate (case-insensitive)
    set_setting(app, "folder_name_pattern", r"[A-Z][A-Za-z0-9 ]*")
    assert post_json(client, "/folders/", {"name": "lowercase"}).status_code == 400
    assert post_json(client, "/folders/", {"name": "Upper"}).status_code == 201
    doc = upload(client, "a.txt", b"x").json["document_id"]
    assert post_json(client, f"/documents/{doc}/rename", {"name": "bad*name"}).status_code == 400


def test_fld04_breadcrumbs_favourites_recent_shortcuts(client):
    a = mk(client, "Projects")
    b = mk(client, "Apollo", a)
    other = mk(client, "Shared")
    doc = upload(client, "plan.txt", b"plan", folder_id=b).json["document_id"]
    page = client.get(f"/folders/{b}").data.decode()
    assert f'href="/folders/{a}">Projects</a>' in page  # breadcrumb
    # favourites
    assert post_json(client, "/folders/favourite", {"kind": "document", "id": doc}).json["starred"] is True
    assert post_json(client, "/folders/favourite", {"kind": "folder", "id": a}).status_code == 200
    fav = client.get("/favourites").data.decode()
    assert "plan.txt" in fav and "Projects" in fav
    # recent items (per user)
    client.get(f"/documents/{doc}")
    assert "Recently opened by you" in client.get("/recent").data.decode()
    # shortcut in another folder
    assert post_json(client, f"/documents/{doc}/links", {"folder_id": other}).status_code == 200
    shared = client.get(f"/folders/{other}").data.decode()
    assert "plan.txt" in shared and "shortcut" in shared
    assert post_json(client, f"/documents/{doc}/links", {"folder_id": other}).status_code == 400  # no duplicates


def test_fld05_recycle_bin_restore_and_expiry(client, app):
    from dms.services.retention import run_maintenance
    f = mk(client, "Old")
    d1 = upload(client, "a.txt", b"a", folder_id=f).json["document_id"]
    d2 = upload(client, "b.txt", b"b").json["document_id"]
    post_json(client, f"/folders/{f}/delete")
    post_json(client, f"/documents/{d2}/delete")
    page = client.get("/recycle-bin").data.decode()
    assert "Old" in page and "b.txt" in page
    run_maintenance()
    assert db.session.get(Document, d2) is not None  # not expired yet (default 30 days)
    keys = [v.storage_key for v in db.session.get(Document, d1).versions]
    old = utcnow() - timedelta(days=31)
    db.session.get(Folder, f).deleted_at = old
    db.session.get(Document, d1).deleted_at = old
    db.session.get(Document, d2).deleted_at = old
    db.session.commit()
    result = run_maintenance()
    assert result["recycle_bin_purged"] == 2
    assert db.session.get(Folder, f) is None and db.session.get(Document, d1) is None
    assert not any(get_storage().exists(k) for k in keys)
    # the period is configurable
    assert settings.get("recycle_bin_days") == 30


# =========================================================================== 7.2 capture & storage

def test_doc01_multi_upload_and_api_upload(client, app):
    data = {"file": [(io.BytesIO(PDF), "one.pdf"), (io.BytesIO(b"two"), "two.txt")]}
    r = client.post("/documents/upload", data=data, content_type="multipart/form-data",
                    headers={"Accept": "application/json", "X-Requested-With": "fetch"})
    assert r.status_code == 201 and db.session.query(Document).count() == 2


def test_doc02_file_types_allow_block_and_size_limits(client, app):
    assert upload(client, "clip.mp4", MP4).status_code == 201                    # video
    assert upload(client, "drawing.dwg", b"AC1032" + b"\x00" * 40).status_code == 201  # CAD
    assert upload(client, "song.mp3", b"ID3\x03" + b"\x00" * 40).status_code == 201   # audio
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.txt", "x")
    assert upload(client, "bundle.zip", buf.getvalue()).status_code == 201           # archive
    assert upload(client, "tool.exe", b"MZ\x90\x00").status_code == 400              # blocked
    assert upload(client, "fake.mp4", b"not a video").status_code == 400             # content check
    # administrators change the lists and limits
    set_setting(app, "blocked_extensions", ["pdf"])
    r = upload(client, "doc.pdf", PDF)
    assert r.status_code == 400 and "blocked" in r.json["error"]
    set_setting(app, "blocked_extensions", [])
    set_setting(app, "type_size_limits", {"txt": 1})
    assert upload(client, "big.txt", b"x" * (1024 * 1024 + 1)).status_code == 400
    assert upload(client, "small.txt", b"x" * 100).status_code == 201
    set_setting(app, "allowed_extensions", ["txt"])
    assert upload(client, "img.png", PNG).status_code == 400


def test_doc03_previews_office_video(client):
    d = upload(client, "letter.docx", docx("Dear customer")).json["document_id"]
    r = client.get(f"/documents/{d}/render")
    assert r.status_code == 200 and b"Dear customer" in r.data
    assert "sandbox" in r.headers["Content-Security-Policy"] and "script-src" not in r.headers["Content-Security-Policy"]
    assert b"documents/%d/render" % d in client.get(f"/documents/{d}").data
    v = upload(client, "clip.mp4", MP4).json["document_id"]
    page = client.get(f"/documents/{v}").data
    assert b"<video" in page
    r = client.get(f"/documents/{v}/preview", headers={"Range": "bytes=4-11"})
    assert r.status_code == 206 and r.data == b"ftypmp42"
    assert r.headers["Content-Range"] == f"bytes 4-11/{len(MP4)}"


def test_doc04_check_out_prevents_concurrent_overwrite(client, app, contributor):
    d = upload(client, "spec.txt", b"v1").json["document_id"]
    other = login_as(app.test_client(), contributor)
    assert post_json(other, f"/documents/{d}/checkout").status_code == 200
    # the administrator (anyone else) cannot overwrite while it is checked out
    r = upload(client, "spec.txt", b"admin edit", url=f"/documents/{d}/versions")
    assert r.status_code == 400 and "checked out" in r.json["error"]
    assert post_json(client, f"/documents/{d}/rename", {"name": "x"}).status_code == 400
    assert post_json(client, f"/documents/{d}/delete").status_code == 400
    # the holder checks in with new content
    r = other.post(f"/documents/{d}/checkin", data={"file": (io.BytesIO(b"v2"), "spec.txt"), "note": "edited"},
                   content_type="multipart/form-data", headers={"Accept": "application/json", "X-Requested-With": "fetch"})
    assert r.status_code == 201
    doc = db.session.get(Document, d)
    assert doc.checked_out_by_id is None and doc.current_version.label == "1.1"
    # records managers can override someone else's check-out
    post_json(other, f"/documents/{d}/checkout")
    assert post_json(client, f"/documents/{d}/release").status_code == 200
    assert db.session.get(Document, d).checked_out_by_id is None


def test_doc05_malware_is_blocked_before_storage(client):
    before = set(get_storage().iter_keys())
    r = upload(client, "eicar.txt", EICAR)
    assert r.status_code == 400 and "malware" in r.json["error"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", EICAR)
    assert upload(client, "macro.docx", buf.getvalue()).status_code == 400
    assert db.session.query(Document).count() == 0
    assert set(get_storage().iter_keys()) == before  # nothing stored
    assert db.session.query(ActivityLog).filter_by(action="malware_blocked").count() == 2
    ok = upload(client, "clean.txt", b"clean").json["document_id"]
    assert db.session.get(Document, ok).current_version.scan_status == "clean"


# =========================================================================== 7.3 versions

def test_ver01_02_major_minor_and_mandatory_comments(client, app):
    d = upload(client, "plan.txt", b"a").json["document_id"]
    url = f"/documents/{d}/versions"
    assert upload(client, "plan.txt", b"b", url=url).json["label"] == "1.1"
    r = client.post(url, data={"file": (io.BytesIO(b"c"), "plan.txt"), "major": "1"}, content_type="multipart/form-data",
                    headers={"Accept": "application/json", "X-Requested-With": "fetch"})
    assert r.json["label"] == "2.0"
    set_setting(app, "require_version_comment", "major")
    r = client.post(url, data={"file": (io.BytesIO(b"d"), "plan.txt"), "major": "1"}, content_type="multipart/form-data",
                    headers={"Accept": "application/json", "X-Requested-With": "fetch"})
    assert r.status_code == 400 and "comment" in r.json["error"]
    assert upload(client, "plan.txt", b"d", url=url).json["label"] == "2.1"  # minor still optional
    set_setting(app, "require_version_comment", "all")
    assert upload(client, "plan.txt", b"e", url=url).status_code == 400
    assert upload(client, "plan.txt", b"e", url=url, note="fixed typo").json["label"] == "2.2"
    assert [v.label for v in db.session.get(Document, d).versions] == ["2.2", "2.1", "2.0", "1.1", "1.0"]


def test_ver03_04_compare_and_restore(client):
    d = upload(client, "contract.txt", b"Party A pays 100\nTerm is one year\nGoverned by law X\n").json["document_id"]
    upload(client, "contract.txt", b"Party A pays 250\nTerm is one year\nGoverned by law X\nNew clause\n",
           url=f"/documents/{d}/versions")
    page = client.get(f"/documents/{d}/compare?a=1&b=2").data.decode()
    assert "<del>100</del>" in page and "<ins>250</ins>" in page and 'diff-insert"><td class="num muted"></td><td class="diff-left"></td><td class="num muted">4</td><td class="diff-right">New clause' in page
    red = client.get(f"/documents/{d}/compare?a=1&b=2&mode=redline").data.decode()
    assert "diff-redline" in red and "<del>100</del><ins>250</ins>" in red and "<ins>New clause</ins>" in red
    assert client.get(f"/documents/{d}/compare?pick=1&pick=2").status_code == 302
    r = post_json(client, f"/documents/{d}/versions/1/restore")
    assert r.status_code == 201 and r.json["label"] == "1.2"
    assert len(db.session.get(Document, d).versions) == 3  # history kept


def test_ver05_version_retention_rules(client, app):
    from dms.services.retention import run_maintenance
    d = upload(client, "log.txt", b"0").json["document_id"]
    for i in range(1, 6):
        upload(client, "log.txt", str(i).encode(), url=f"/documents/{d}/versions")
    set_setting(app, "version_keep_last", 2)
    assert run_maintenance()["versions_pruned"] == 4
    doc = db.session.get(Document, d)
    assert [v.label for v in doc.versions] == ["1.5", "1.4"]
    assert client.get(f"/documents/{d}/download").data == b"5"
    # folder-level override
    f = mk(client, "Keep all")
    db.session.get(Folder, f).version_keep_last = 0
    db.session.commit()


# =========================================================================== 7.4 search

def _search(client, **params):
    return client.get("/search/", query_string=params).data.decode()


def test_src01_02_04_full_text_advanced_search_and_highlighting(client):
    upload(client, "q3.txt", b"The quarterly budget forecast for the northern region")
    upload(client, "memo.docx", docx("Budget approval memo for the annual budget"))
    upload(client, "notes.txt", b"Meeting notes about hiring")
    page = _search(client, q="budget")
    assert "q3.txt" in page and "memo.docx" in page and "notes.txt" not in page
    assert "<mark>budget</mark>" in page.lower()
    assert "notes.txt" in _search(client, q="hiring OR nonexistentword")
    page = _search(client, q='"annual budget"')
    assert "memo.docx" in page and "q3.txt" not in page
    assert "q3.txt" in _search(client, q="forec*") and "q3.txt" in _search(client, q="f?recast")
    assert "q3.txt" in _search(client, q="budgit~")                  # fuzzy
    page = _search(client, q="budget NOT memo")
    assert "q3.txt" in page and "memo.docx" not in page
    page = _search(client, q="budget", type="Word")                   # type filter
    assert "memo.docx" in page and "q3.txt" not in page
    page = _search(client, q="budget", size_max="0.0001", size_unit="mb")
    assert "memo.docx" not in page
    page = _search(client, q="budget")
    assert "Content" in page and "Type" in page                       # facets
    assert "name:" in _search(client, q="name:q3") or "q3.txt" in _search(client, q="name:q3")
    assert "Add at least one word" in _search(client, q="-budget")


def test_src01_email_bodies_and_attachments_are_searchable(client, app):
    from dms.models import GmailAccount
    from dms.services.emails import store_email
    from .conftest import build_email
    acct = GmailAccount(email="tester@example.com")
    db.session.add(acct)
    db.session.commit()
    store_email(acct, "abc123", build_email(subject="Invoice dispute"), label_ids=[])
    page = _search(client, q="Bob")             # body text
    assert "Invoice dispute" in page
    page = _search(client, q="report.pdf")      # attachment name
    assert "Invoice dispute" in page


def test_src03_results_are_security_trimmed(client, app, reader_client):
    secret = mk(client, "Board")
    upload(client, "minutes.txt", b"confidential merger discussion", folder_id=secret)
    upload(client, "public.txt", b"public merger announcement")
    assert "minutes.txt" in _search(client, q="merger")
    from dms.services.access import set_inheritance
    set_inheritance(db.session.get(Folder, secret), False, copy_inherited=False)
    page = _search(reader_client, q="merger")
    assert "public.txt" in page and "minutes.txt" not in page
    assert b"minutes.txt" not in reader_client.get("/documents/?q=minutes").data
    assert b"minutes" not in reader_client.get("/documents/search.json?q=minutes").data
    assert reader_client.get(f"/folders/{secret}").status_code == 404


# =========================================================================== 7.5 metadata

def test_met01_02_custom_fields_vocabularies_tags_and_validation(client, app):
    assert client.post("/admin/metadata/vocabularies", data={"name": "Departments", "terms": "Legal\nFinance"}).status_code == 303
    from dms.models import DocumentType, MetadataField, Vocabulary
    vocab = db.session.query(Vocabulary).one()
    client.post("/admin/metadata/types", data={"name": "Contract"})
    ctype = db.session.query(DocumentType).one()
    client.post("/admin/metadata/fields", data={"name": "department", "label": "Department", "field_type": "choice",
                                                "vocabulary_id": vocab.id, "required": "1"})
    client.post("/admin/metadata/fields", data={"name": "value", "label": "Contract value", "field_type": "number",
                                                "document_type_id": ctype.id})
    dept = db.session.query(MetadataField).filter_by(name="department").one()
    value = db.session.query(MetadataField).filter_by(name="value").one()
    schema = client.get(f"/documents/schema.json?type_id={ctype.id}").json
    assert {f["name"] for f in schema["fields"]} == {"department", "value"}

    def up(**form):
        data = {"file": (io.BytesIO(PDF + str(time.time_ns()).encode()), "c.pdf"), **form}
        return client.post("/documents/upload", data=data, content_type="multipart/form-data",
                           headers={"Accept": "application/json", "X-Requested-With": "fetch"})

    assert up().status_code == 400                                              # required field missing
    assert up(**{f"meta_{dept.id}": "Sales"}).status_code == 400                  # not in vocabulary
    assert up(**{f"meta_{dept.id}": "Legal", "document_type_id": ctype.id, f"meta_{value.id}": "abc"}).status_code == 400
    r = up(**{f"meta_{dept.id}": "Legal", "document_type_id": ctype.id, f"meta_{value.id}": "1500", "tags": "nda, 2026"})
    assert r.status_code == 201
    doc = db.session.get(Document, r.json["document_id"])
    assert {mv.field.name: mv.value for mv in doc.metadata_values} == {"department": "Legal", "value": "1500"}
    assert sorted(t.name for t in doc.tags) == ["2026", "nda"] and doc.document_type.name == "Contract"
    page = client.get(f"/documents/{doc.id}").data.decode()
    assert "Admin User" in page  # system metadata: author
    assert "c.pdf" in _search(client, q="meta:Legal")
    set_setting(app, "tags_controlled", True)
    r = up(**{f"meta_{dept.id}": "Finance", "tags": "unapproved"})
    assert r.status_code == 400 and "approved tag" in r.json["error"]


# =========================================================================== 7.6 security

def test_sec01_predefined_and_custom_roles(client, app):
    names = {r.name for r in db.session.query(Role)}
    assert {"Admin", "Records Manager", "Contributor", "Reader", "Auditor"} <= names
    r = client.post("/admin/roles/new", data={"name": "Legal Reviewer", "perms": ["view", "download"], "caps": ["audit"]})
    assert r.status_code == 303
    custom = role("Legal Reviewer")
    assert custom.capability_set == {"audit"} and not custom.is_system
    user = make_user("lena", ("Legal Reviewer",))
    c = login_as(app.test_client(), user)
    assert c.get("/activity").status_code == 200
    assert c.get("/admin/").status_code == 403


def test_sec01_02_role_permissions_enforced(client, app, reader_client):
    f = mk(client, "Policies")
    d = upload(client, "policy.pdf", PDF, folder_id=f).json["document_id"]
    assert reader_client.get(f"/documents/{d}").status_code == 200
    assert reader_client.get(f"/documents/{d}/download").status_code == 200
    assert upload(reader_client, "x.txt", b"x", folder_id=f).status_code == 403
    assert post_json(reader_client, f"/documents/{d}/delete").status_code == 403
    assert post_json(reader_client, f"/documents/{d}/rename", {"name": "y"}).status_code == 403
    assert reader_client.get("/activity").status_code == 403
    assert reader_client.get("/admin/users").status_code == 403
    assert reader_client.get("/emails/").status_code == 403
    auditor = login_as(app.test_client(), make_user("audrey", ("Auditor",)))
    assert auditor.get("/activity").status_code == 200
    assert auditor.get(f"/documents/{d}/download").status_code == 403   # view only
    assert auditor.get(f"/documents/{d}").status_code == 200


def test_fld02_sec02_inheritance_override_and_levels(client, app, reader, reader_client):
    hr = mk(client, "HR")
    payroll = mk(client, "Payroll", hr)
    d = upload(client, "salaries.txt", b"secret", folder_id=payroll).json["document_id"]
    # break inheritance on Payroll without copying: readers lose access below it
    r = post_json(client, f"/permissions/folder/{payroll}/inheritance", {"inherit": "0", "copy": "0"})
    assert r.status_code == 200
    assert reader_client.get(f"/documents/{d}").status_code == 404
    assert reader_client.get(f"/folders/{hr}").status_code == 200
    # grant the reader access on the folder: inherited by its document
    post_json(client, f"/permissions/folder/{payroll}/grant", {"principal": f"user:{reader.id}", "perms": ["view"]})
    assert reader_client.get(f"/documents/{d}").status_code == 200
    assert reader_client.get(f"/documents/{d}/download").status_code == 403
    # document-level grant adds download
    post_json(client, f"/permissions/document/{d}/grant", {"principal": f"user:{reader.id}", "perms": ["download"]})
    assert reader_client.get(f"/documents/{d}/download").status_code == 200
    # version-level restriction
    upload(client, "salaries.txt", b"secret v2", url=f"/documents/{d}/versions")
    post_json(client, f"/permissions/version/{d}/1/inheritance", {"inherit": "0", "copy": "0"})
    assert reader_client.get(f"/documents/{d}/versions/1/download").status_code == 403
    assert reader_client.get(f"/documents/{d}/versions/2/download").status_code == 200
    # breaking with copy keeps existing access
    post_json(client, f"/permissions/folder/{hr}/inheritance", {"inherit": "0", "copy": "1"})
    assert reader_client.get(f"/folders/{hr}").status_code == 200
    # restore inheritance
    post_json(client, f"/permissions/folder/{payroll}/inheritance", {"inherit": "1"})
    assert db.session.get(Folder, payroll).inherit_permissions


def test_sec02_share(client, app, contributor, reader):
    f = mk(client, "Team")
    db.session.get(Folder, f).inherit_permissions = False
    db.session.add(AccessEntry(target_type="folder", target_id=f, principal_type="user", principal_id=contributor.id,
                               permissions="view,download,share"))
    db.session.commit()
    c = login_as(app.test_client(), contributor)
    r = post_json(c, f"/permissions/folder/{f}/share", {"principal": f"user:{reader.id}", "perms": ["view"]})
    assert r.status_code == 200
    r = post_json(c, f"/permissions/folder/{f}/share", {"principal": f"user:{reader.id}", "perms": ["edit"]})
    assert r.status_code == 400  # cannot share more than you have
    assert login_as(app.test_client(), reader).get(f"/folders/{f}").status_code == 200


def test_sec03_login_lockout_and_setup(app, anon):
    r = anon.get("/documents/")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]
    assert anon.get("/api/v1/me").status_code == 401
    assert anon.post("/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    r = anon.post("/auth/login", data={"username": "admin", "password": "Correct-Horse-9"})
    assert r.status_code == 302
    assert anon.get("/").status_code == 200
    bad = app.test_client()
    for _ in range(5):
        bad.post("/auth/login", data={"username": "admin", "password": "nope"})
    r = bad.post("/auth/login", data={"username": "admin", "password": "Correct-Horse-9"})
    assert r.status_code == 401 and b"locked" in r.data


def test_sec03_first_run_setup(tmp_path):
    from dms import create_app
    from dms.config import TestConfig
    app = create_app(TestConfig, SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp_path / 's.sqlite').as_posix()}",
                     INSTANCE_DIR=tmp_path, STORAGE_DIR=tmp_path / "storage")
    with app.app_context():
        c = app.test_client()
        assert "/auth/setup" in c.get("/").headers["Location"]
        assert c.post("/auth/setup", data={"username": "boss", "password": "short", "password2": "short"}).status_code == 200
        r = c.post("/auth/setup", data={"username": "boss", "password": "Long-Enough-Pass1", "password2": "Long-Enough-Pass1"})
        assert r.status_code == 302
        assert db.session.query(User).one().is_admin
        assert c.get("/auth/setup").status_code == 302  # closed once a user exists
        db.session.remove()
        db.engine.dispose()


def test_sec03_mfa_totp(app, admin):
    from dms.security import mfa
    c = login_as(app.test_client(), admin)
    c.get("/account/mfa")
    with c.session_transaction() as s:
        secret = s["mfa_pending_secret"]
    assert c.post("/account/mfa/enable", data={"code": "000000"}).status_code == 303
    assert not db.session.get(User, admin.id).mfa_enabled
    c.post("/account/mfa/enable", data={"code": mfa.totp(secret, int(time.time() // 30))})
    assert db.session.get(User, admin.id).mfa_enabled
    # a new sign-in now needs the second factor
    fresh = app.test_client()
    r = fresh.post("/auth/login", data={"username": "admin", "password": "Correct-Horse-9"})
    assert "/auth/mfa" in r.headers["Location"]
    assert "/auth/mfa" in fresh.get("/").headers["Location"]
    assert fresh.post("/auth/mfa", data={"code": "123456"}).status_code == 401
    r = fresh.post("/auth/mfa", data={"code": mfa.totp(secret, int(time.time() // 30))})
    assert r.status_code == 302 and fresh.get("/").status_code == 200


def test_sec03_mfa_required_by_policy(app, admin, client):
    set_setting(app, "mfa_required", True)
    r = client.get("/documents/")
    assert r.status_code == 302 and "/account/mfa" in r.headers["Location"]


def test_sec03_ldap_and_group_role_mapping(app, anon, monkeypatch):
    from dms.security import ldap_auth
    app.config["LDAP_URL"] = "ldaps://dc.example.com"
    set_setting(app, "group_role_map", {"DMS-Records": "Records Manager"})
    monkeypatch.setattr(ldap_auth, "authenticate", lambda u, p: {
        "dn": "CN=Jo,DC=example", "email": "jo@example.com", "name": "Jo Smith",
        "groups": ["CN=DMS-Records,OU=Groups", "DMS-Records"]} if p == "dirpass" else None)
    r = anon.post("/auth/login", data={"username": "jo", "password": "dirpass"})
    assert r.status_code == 302
    user = db.session.query(User).filter_by(email="jo@example.com").one()
    assert user.auth_source == "ldap" and [r.name for r in user.roles] == ["Records Manager"]


def test_sec03_saml_sign_in(app, anon, tmp_path):
    import base64
    from datetime import datetime, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from lxml import etree
    from signxml import XMLSigner

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "idp.pem").write_bytes(cert_pem)
    app.config.update(SAML_ENABLED=True, SAML_IDP_SSO_URL="https://idp.example.com/sso", SAML_IDP_ENTITY_ID="https://idp",
                      SAML_IDP_CERT_FILE=tmp_path / "idp.pem")

    from urllib.parse import parse_qs, urlparse
    from dms.extensions import csrf
    from dms.routes.auth import _saml_serializer
    assert "dms.routes.auth.saml_acs" in csrf._exempt_views  # the IdP cannot send a CSRF token

    def start(client):
        loc = client.get("/auth/saml/login").headers["Location"]
        assert loc.startswith("https://idp.example.com/sso?SAMLRequest=")
        relay = parse_qs(urlparse(loc).query)["RelayState"][0]
        return relay, _saml_serializer("relay").loads(relay)["rid"]

    relay, req_id = start(anon)
    acs = "http://localhost/auth/saml/acs"
    sp = "http://localhost/auth/saml/metadata"
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    assertion = (
        f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a1" Version="2.0" IssueInstant="{fmt(now)}">'
        f'<saml:Issuer>https://idp</saml:Issuer><saml:Subject><saml:NameID>sam@example.com</saml:NameID>'
        f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData '
        f'InResponseTo="{req_id}" Recipient="{acs}" NotOnOrAfter="{fmt(now + timedelta(minutes=5))}"/></saml:SubjectConfirmation></saml:Subject>'
        f'<saml:Conditions NotBefore="{fmt(now - timedelta(minutes=1))}" NotOnOrAfter="{fmt(now + timedelta(minutes=5))}">'
        f'<saml:AudienceRestriction><saml:Audience>{sp}</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
        f'<saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>sam@example.com</saml:AttributeValue></saml:Attribute>'
        f'<saml:Attribute Name="displayName"><saml:AttributeValue>Sam IdP</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>'
        f'</saml:Assertion>')
    signed = XMLSigner(c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(
        etree.fromstring(assertion), key=key, cert=cert_pem.decode())
    response = (f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="_r1" Version="2.0" '
                f'IssueInstant="{fmt(now)}" Destination="{acs}" InResponseTo="{req_id}"><samlp:Status>'
                f'<samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
                ).encode() + etree.tostring(signed) + b"</samlp:Response>"
    form = {"SAMLResponse": base64.b64encode(response).decode(), "RelayState": relay}
    r = anon.post("/auth/saml/acs", data=form)
    assert r.status_code == 302 and "/auth/saml/complete" in r.headers["Location"]
    r = anon.get(r.headers["Location"])
    assert r.status_code == 302 and "/auth/login" not in r.headers["Location"]
    assert anon.get("/").status_code == 200
    # the same response cannot be replayed
    r = app.test_client().post("/auth/saml/acs", data=form)
    assert "/auth/login" in r.headers["Location"]
    user = db.session.query(User).filter_by(email="sam@example.com").one()
    assert user.auth_source == "saml" and user.display_name == "Sam IdP"
    # a tampered response is rejected
    tampered = response.replace(b"sam@example.com</saml:NameID>", b"evil@example.com</saml:NameID>")
    c2 = app.test_client()
    relay2, _ = start(c2)
    r = c2.post("/auth/saml/acs", data={"SAMLResponse": base64.b64encode(tampered).decode(), "RelayState": relay2})
    assert "/auth/login" in r.headers["Location"]
    assert db.session.query(User).filter_by(email="evil@example.com").count() == 0


def test_sec04_encryption_at_rest_and_key_rotation(client, app):
    from dms.crypto import MAGIC, add_key_to_file, load_key_ring
    secret = b"Top secret payroll figures " * 5000
    d = upload(client, "pay.txt", secret).json["document_id"]
    key = db.session.get(Document, d).current_version.storage_key
    raw = get_storage().local_path(key).read_bytes()
    assert raw.startswith(MAGIC) and b"payroll" not in raw
    assert client.get(f"/documents/{d}/download").data == secret
    # tampering is detected
    storage = get_storage()
    # key rotation re-wraps data keys; content stays readable
    path = app.config["INSTANCE_DIR"] / "master.key"
    new_kid = add_key_to_file(path)
    storage.ring = load_key_ring(app.config)
    assert storage.rewrap(key) is True
    assert storage.local_path(key).read_bytes()[9:9 + len(new_kid)] == new_kid.encode()
    assert client.get(f"/documents/{d}/download").data == secret
    # legacy plaintext files are converted
    storage.local_path("documents/aa/bb/legacy").parent.mkdir(parents=True, exist_ok=True)
    storage.local_path("documents/aa/bb/legacy").write_bytes(b"old plaintext")
    assert storage.read_bytes("documents/aa/bb/legacy") == b"old plaintext"
    assert storage.encrypt_existing("documents/aa/bb/legacy") is True
    assert storage.local_path("documents/aa/bb/legacy").read_bytes().startswith(MAGIC)
    assert storage.read_bytes("documents/aa/bb/legacy") == b"old plaintext"


def test_sec04_crypto_detects_tampering(app):
    from dms.crypto import CHUNK_SIZE
    storage = get_storage()
    data = bytes(range(256)) * (CHUNK_SIZE // 128)  # two full chunks
    storage.save_bytes("documents/t/t/x", data)
    assert storage.read_bytes("documents/t/t/x") == data
    path = storage.local_path("documents/t/t/x")
    blob = bytearray(path.read_bytes())
    blob[-5] ^= 1
    path.write_bytes(bytes(blob))
    with pytest.raises(Exception):
        storage.read_bytes("documents/t/t/x")
    storage.save_bytes("documents/t/t/empty", b"")
    assert storage.read_bytes("documents/t/t/empty") == b""


def test_sec04_hsts_when_https_enforced(client, app):
    app.config["FORCE_HTTPS"] = True
    r = client.get("/", base_url="https://localhost")
    assert "max-age=" in r.headers.get("Strict-Transport-Security", "")


def test_sec05_ip_restriction_session_timeout_and_devices(client, app, admin):
    set_setting(app, "ip_allowlist", ["10.0.0.0/8"])
    assert client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/", environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code == 200
    assert client.get("/").status_code == 200  # localhost always allowed
    set_setting(app, "ip_allowlist", [])
    # idle timeout
    with client.session_transaction() as s:
        s["seen"] = int(time.time()) - 31 * 60
    r = client.get("/documents/")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]
    # device approval policy for non-admins
    set_setting(app, "device_policy", "approval")
    make_user("dora", ("Reader",))
    c = app.test_client()
    r = c.post("/auth/login", data={"username": "dora", "password": "Correct-Horse-9"})
    assert "/auth/device-pending" in r.headers["Location"]
    device = db.session.query(UserDevice).join(User).filter(User.username == "dora").one()
    assert device.status == "pending"
    device.status = "approved"
    db.session.commit()
    r = c.post("/auth/login", data={"username": "dora", "password": "Correct-Horse-9"})
    assert r.status_code == 302 and "device-pending" not in r.headers["Location"]
    assert c.get("/").status_code == 200
    # blocked platforms
    set_setting(app, "blocked_platforms", ["android"])
    phone = app.test_client()
    r = phone.post("/auth/login", data={"username": "dora", "password": "Correct-Horse-9"},
                   headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14)"})
    assert "/auth/blocked" in r.headers["Location"]


# =========================================================================== API (sync / mobile clients)

def test_api_tokens_for_sync_and_mobile(client, app, reader):
    from dms.security.auth import new_api_token
    raw, _ = new_api_token(db.session.get(User, 1), "sync")
    h = {"Authorization": f"Bearer {raw}"}
    c = app.test_client()
    assert c.get("/api/v1/me", headers=h).json["username"] == "admin"
    r = c.post("/api/v1/documents", headers=h, data={"file": (io.BytesIO(b"from sync"), "sync.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 201
    doc_id = r.json["document"]["id"]
    assert c.get(f"/api/v1/documents/{doc_id}/content", headers=h).data == b"from sync"
    listing = c.get("/api/v1/documents?changed_since=2000-01-01T00:00:00Z", headers=h).json
    assert any(d["id"] == doc_id for d in listing["documents"])
    assert c.post(f"/api/v1/documents/{doc_id}/checkout", headers=h).status_code == 200
    rtoken, _ = new_api_token(reader, "phone")
    r = c.post("/api/v1/documents", headers={"Authorization": f"Bearer {rtoken}"},
               data={"file": (io.BytesIO(b"x"), "x.txt")}, content_type="multipart/form-data")
    assert r.status_code == 403
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer dms_invalid"}).status_code == 401


def test_audit_trail_records_user_and_ip(client):
    upload(client, "a.txt", b"a")
    entry = db.session.query(ActivityLog).filter_by(action="uploaded").one()
    assert entry.user_name == "Admin User" and entry.ip == "127.0.0.1"
