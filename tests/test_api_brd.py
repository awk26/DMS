"""BRD Phase 1 acceptance tests against the REST API (/api/v1) that the React frontend uses.

The ``app``, ``admin`` and ``api`` fixtures and the ``Api`` client come from conftest.py.
"""
import io
import time
import zipfile

import pytest

from dms.extensions import db
from dms.models import Document, Role, User
from dms.services import settings
from dms.storage import get_storage

from .conftest import PDF, PNG, Api, make_user

# Written before "restricted by default": Contributor/Reader/Auditor keep repository-wide access here.
pytestmark = pytest.mark.usefixtures("legacy_repository")


EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def docx(text="Hello"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


@pytest.fixture
def reader(app):
    return Api(app, make_user("rita", ("Reader",), email="rita@example.com"))


@pytest.fixture
def contributor(app):
    return Api(app, make_user("carl", ("Contributor",), email="carl@example.com"))


def set_setting(key, value):
    settings.set_value(key, value)
    db.session.commit()


# =========================================================================== 7.1 Repository & folders

def test_fld01_create_rename_move_copy_delete_unlimited_depth(api):
    parent = None
    for depth in range(12):
        parent = api.folder(f"Level {depth}", parent)
    deep = api.get(f"/folders/{parent}").json["folder"]
    assert deep["path"].count("/") == 11

    a, b = api.folder("Alpha"), api.folder("Beta")
    api.doc("inside.txt", b"inside", a)
    assert api.patch(f"/folders/{a}", {"name": "Alpha renamed"}).json["folder"]["name"] == "Alpha renamed"
    assert api.patch(f"/folders/{a}", {"parent_id": b}).json["folder"]["parent_id"] == b
    assert api.patch(f"/folders/{b}", {"parent_id": a}).status_code == 400  # not into its own subfolder

    copy = api.post(f"/folders/{a}/copy", json={"parent_id": None})
    assert copy.status_code == 201
    copied = api.get(f"/folders/{copy.json['folder']['id']}").json
    assert [d["name"] for d in copied["documents"]] == ["inside.txt"]

    assert api.delete(f"/folders/{a}").status_code == 200
    assert api.get(f"/folders/{a}").status_code == 404


def test_fld02_inheritance_with_override_at_any_level(api, reader):
    top = api.folder("Projects")
    secret = api.folder("Secret", top)
    api.doc("plan.txt", b"plan", secret)
    assert reader.get(f"/folders/{secret}").status_code == 200  # inherited from the repository

    r = api.patch(f"/permissions/folder/{secret}/inheritance", {"inherit": False, "copy": False})
    assert r.status_code == 200 and r.json["inherit_permissions"] is False
    assert reader.get(f"/folders/{secret}").status_code == 404
    assert reader.get(f"/folders/{top}").status_code == 200

    grant = api.post(f"/permissions/folder/{secret}/grant",
                     json={"principal_type": "user", "principal_id": reader.user.id, "permissions": ["view"]})
    assert grant.status_code == 200
    assert reader.get(f"/folders/{secret}").status_code == 200

    assert api.patch(f"/permissions/folder/{secret}/inheritance", {"inherit": True}).status_code == 200
    perms = api.get(f"/permissions/folder/{secret}").json
    assert perms["inherit_permissions"] is True


def test_fld03_naming_conventions_invalid_characters_and_duplicates(api):
    for bad in ("a/b", "x:y", "what?", "CON", "  ", "trailing."):
        r = api.post("/folders", json={"name": bad})
        assert r.status_code == 400, bad
        assert r.json["code"] == "validation_error"
    api.folder("Finance")
    assert api.post("/folders", json={"name": "finance"}).status_code == 400  # duplicate, case-insensitive
    set_setting("folder_name_pattern", r"^[A-Z][A-Za-z0-9 ]*$")
    assert api.post("/folders", json={"name": "lowercase"}).status_code == 400
    assert api.post("/folders", json={"name": "Upper"}).status_code == 201


def test_fld04_breadcrumbs_favourites_recent_and_shortcuts(api):
    a = api.folder("A")
    b = api.folder("B", a)
    c = api.folder("C", b)
    crumbs = api.get(f"/folders/{c}").json["breadcrumbs"]
    assert [x["name"] for x in crumbs] == ["A", "B", "C"]

    root = api.get("/folders/root").json
    assert root["folder"] is None and any(f["id"] == a for f in root["folders"])

    doc = api.doc("report.pdf", PDF, c)
    detail = api.get(f"/documents/{doc}").json
    assert [x["name"] for x in detail["breadcrumbs"]] == ["A", "B", "C"]
    recent = api.get("/recent").json["viewed"]
    assert recent and recent[0]["document"]["id"] == doc

    assert api.put(f"/favourites/folder/{b}").status_code == 200
    assert api.put(f"/favourites/document/{doc}").status_code == 200
    favs = api.get("/favourites").json["favourites"]
    assert {(f["type"], f["id"]) for f in favs} == {("folder", b), ("document", doc)}

    other = api.folder("Elsewhere")
    link = api.post(f"/documents/{doc}/links", json={"folder_id": other})
    assert link.status_code == 201
    listing = api.get(f"/folders/{other}").json
    assert [s["document"]["id"] for s in listing["shortcuts"]] == [doc]
    assert api.get(f"/documents/{doc}").json["links"][0]["folder_id"] == other
    assert api.delete(f"/documents/links/{link.json['link_id']}").status_code == 200
    assert api.get(f"/folders/{other}").json["shortcuts"] == []


def test_fld05_recycle_bin_restore_and_configurable_period(api):
    f = api.folder("Old")
    doc = api.doc("old.txt", b"old", f)
    api.delete(f"/folders/{f}")
    bin_ = api.get("/recycle-bin").json
    assert [x["id"] for x in bin_["folders"]] == [f]
    assert bin_["folders"][0]["expires_at"]
    assert api.post(f"/folders/{f}/restore").status_code == 200
    assert api.get(f"/documents/{doc}").status_code == 200

    current = api.get("/admin/settings").json["settings"]
    assert current["recycle_bin_days"] == 30
    assert api.put("/admin/settings", {"recycle_bin_days": 7}).status_code == 400
    assert api.put("/admin/settings", {"recycle_bin_days": 45}).json["settings"]["recycle_bin_days"] == 45


# =========================================================================== 7.2 Document capture & storage

def test_doc01_single_and_multiple_upload(api):
    assert api.upload("one.txt", b"one").status_code == 201
    form = {"file": [(io.BytesIO(b"first"), "first.txt"), (io.BytesIO(b"second"), "second.txt"),
                     (io.BytesIO(b"MZ bad"), "bad.exe")]}
    r = api.post("/documents", data=form, content_type="multipart/form-data")
    assert r.status_code == 201
    assert sorted(d["name"] for d in r.json["documents"]) == ["first.txt", "second.txt"]
    assert len(r.json["errors"]) == 1 and r.json["errors"][0]["filename"] == "bad.exe"


def test_doc02_file_types_allow_block_and_size_limits(api):
    assert api.upload("drawing.dwg", b"AC1027" + b"\x00" * 50).status_code == 201
    assert api.upload("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64).status_code == 201
    assert api.upload("virus.exe", b"MZ....").status_code == 400
    current = api.get("/admin/settings").json["settings"]
    r = api.put("/admin/settings", {"type_size_limits": {"txt": 1}, "blocked_extensions": current["blocked_extensions"] + ["csv"]})
    assert r.status_code == 200, r.json
    assert r.json["settings"]["type_size_limits"] == {"txt": 1}
    assert api.upload("big.txt", b"x" * (1024 * 1024 + 10)).status_code == 400
    assert api.upload("data.csv", b"a,b").status_code == 400


def test_doc03_in_browser_preview(api, app):
    pdf = api.doc("file.pdf", PDF)
    r = api.get(f"/documents/{pdf}/preview")
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("inline")
    app.config["API_CORS_ALLOWED_ORIGINS"] = ("http://localhost:4200",)
    framed = api.get(f"/documents/{pdf}/preview").headers["Content-Security-Policy"]
    assert framed == "frame-ancestors 'self' http://localhost:4200"  # the Angular app may embed previews
    img = api.doc("pic.png", PNG)
    assert api.get(f"/documents/{img}/preview").mimetype == "image/png"
    word = api.doc("letter.docx", docx("Dear customer"))
    info = api.get(f"/documents/{word}").json["preview"]
    assert info["kind"] == "office" and info["office_mode"] in ("html", "pdf")
    render = api.get(f"/documents/{word}/render")
    assert render.status_code == 200 and b"Dear customer" in render.data
    assert "sandbox" in render.headers["Content-Security-Policy"]
    assert api.upload("page.html", b"<script>alert(1)</script>").status_code == 400  # active content blocked
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "a")
    bundle = api.doc("bundle.zip", archive.getvalue())
    assert api.get(f"/documents/{bundle}/preview").status_code == 404  # no inline preview for archives
    assert api.get(f"/documents/{pdf}").json["preview"]["kind"] == "pdf"


def test_doc04_check_out_prevents_concurrent_overwrite(api, app, contributor):
    records = Api(app, make_user("rm", ("Records Manager",)))
    doc = contributor.doc("shared.txt", b"v1")
    assert contributor.post(f"/documents/{doc}/checkout").json["document"]["checked_out_by"] == "carl"
    # The administrator has no access to a user's document at all (privacy); someone with edit is blocked by the lock.
    assert api.version(doc, "shared.txt", b"v2 by admin").status_code in (403, 404)
    assert records.version(doc, "shared.txt", b"v2 by records").status_code == 400
    assert records.patch(f"/documents/{doc}", {"name": "renamed.txt"}).status_code == 400
    assert records.post(f"/documents/{doc}/release").json["document"]["checked_out_by"] is None
    contributor.post(f"/documents/{doc}/checkout")
    form = {"file": (io.BytesIO(b"v2"), "shared.txt"), "note": "edited"}
    r = contributor.post(f"/documents/{doc}/checkin", data=form, content_type="multipart/form-data")
    assert r.json["version"] == "1.1" and r.json["document"]["checked_out_by"] is None


def test_doc05_malware_is_rejected_before_storage(api, app):
    before = len(list((app.config["STORAGE_DIR"]).rglob("*"))) if app.config["STORAGE_DIR"].exists() else 0
    r = api.upload("eicar.txt", EICAR)
    assert r.status_code == 400 and "malware" in r.json["error"]
    assert db.session.query(Document).count() == 0
    after = len([p for p in app.config["STORAGE_DIR"].rglob("*") if p.is_file()]) if app.config["STORAGE_DIR"].exists() else 0
    assert after <= before


# =========================================================================== 7.3 Version control

def test_ver01_02_new_version_on_each_change_major_minor_and_comments(api):
    doc = api.doc("spec.txt", b"one")
    assert api.version(doc, "spec.txt", b"two").json["version"] == "1.1"
    assert api.version(doc, "spec.txt", b"three", major="1").json["version"] == "2.0"
    versions = api.get(f"/documents/{doc}").json["versions"]  # newest first
    assert [v["label"] for v in versions] == ["2.0", "1.1", "1.0"]
    assert versions[0]["current"] and versions[0]["created_by"] == "admin"
    set_setting("require_version_comment", "major")
    assert api.version(doc, "spec.txt", b"four", major="1").status_code == 400
    assert api.version(doc, "spec.txt", b"four", major="1", note="Big change").json["version"] == "3.0"
    assert api.version(doc, "spec.txt", b"five").json["version"] == "3.1"


def test_ver03_04_view_download_compare_and_restore(api):
    doc = api.doc("notes.txt", b"alpha\nbeta\ngamma\n")
    api.version(doc, "notes.txt", b"alpha\nBETA changed\ngamma\ndelta\n")
    assert api.get(f"/documents/{doc}/content?version=1").data == b"alpha\nbeta\ngamma\n"
    assert api.get(f"/documents/{doc}/preview?version=1").status_code == 200
    cmp = api.get(f"/documents/{doc}/compare?a=1&b=2")
    assert cmp.status_code == 200, cmp.json
    body = cmp.json
    assert body["a"]["label"] == "1.0" and body["b"]["label"] == "1.1"
    assert body["stats"] == {"added": 1, "removed": 0, "changed": 1}
    assert any("<ins>" in row["right"] for row in body["rows"] if row["kind"] == "change")
    assert any("<del>" in row["html"] for row in body["redline"] if row["kind"] == "change")

    restored = api.post(f"/documents/{doc}/versions/1/restore", json={})
    assert restored.status_code == 201 and restored.json["version"] == "1.2"
    versions = api.get(f"/documents/{doc}").json["versions"]
    assert len(versions) == 3  # history kept, nothing overwritten
    assert api.get(f"/documents/{doc}/content").data == b"alpha\nbeta\ngamma\n"


def test_ver05_version_retention_rules(api):
    folder = api.folder("Retained")
    doc = api.doc("r.txt", b"0", folder)
    for i in range(1, 5):
        api.version(doc, "r.txt", str(i).encode())
    assert api.patch(f"/folders/{folder}/retention", {"keep_last": "2"}).json["keep_last"] == 2
    assert api.post("/admin/maintenance").status_code == 200
    assert len(api.get(f"/documents/{doc}").json["versions"]) == 2


# =========================================================================== 7.4 Search

def _search(api, **params):
    r = api.get("/search", query_string=params)
    assert r.status_code == 200, r.json
    return r.json


def test_src01_02_04_full_text_advanced_search_facets_and_highlighting(api):
    api.doc("budget.txt", b"The annual budget for marketing is approved")
    api.doc("travel.txt", b"Travel policy for the marketing team")
    api.doc("contract.docx", docx("Supplier contract renewal terms"))
    names = lambda res: sorted(r["title"] for r in res["results"])
    assert names(_search(api, q="renewal")) == ["contract.docx"]                  # Office content
    assert names(_search(api, q="marketing AND budget")) == ["budget.txt"]
    assert names(_search(api, q="marketing NOT budget")) == ["travel.txt"]
    assert names(_search(api, q='"annual budget"')) == ["budget.txt"]
    assert names(_search(api, q="budg*")) == ["budget.txt"]
    assert names(_search(api, q="travek~")) == ["travel.txt"]                     # fuzzy, distance 1
    assert names(_search(api, q="travle~2")) == ["travel.txt"]
    res = _search(api, q="marketing", type="Text", sort="name")
    assert names(res) == ["budget.txt", "travel.txt"]
    assert "<mark>" in res["results"][0]["snippet"]
    assert res["facets"]
    assert _search(api, q="marketing", size_min="1", size_unit="mb")["results"] == []


def test_src03_results_are_security_trimmed(api, reader):
    hidden = api.folder("Hidden")
    api.doc("salaries.txt", b"confidential salaries", hidden)
    assert len(_search(reader, q="salaries")["results"]) == 1
    api.patch(f"/permissions/folder/{hidden}/inheritance", {"inherit": False, "copy": False})
    assert _search(reader, q="salaries")["results"] == []
    assert len(_search(api, q="salaries")["results"]) == 1
    assert all(d["name"] != "salaries.txt" for d in reader.get("/documents").json["documents"])


# =========================================================================== 7.5 Metadata

def test_met01_02_system_and_custom_metadata_vocabularies_tags_and_validation(api, contributor):
    vocab = api.post("/admin/metadata/vocabularies", json={"name": "Regions", "terms": "North\nSouth"}).json["id"]
    dtype = api.post("/admin/metadata/types", json={"name": "Invoice"}).json["id"]
    field = api.post("/admin/metadata/fields", json={
        "name": "region", "label": "Region", "field_type": "choice", "vocabulary_id": vocab,
        "required": "1", "document_type_id": dtype}).json["id"]

    schema = contributor.get(f"/metadata/schema?document_type_id={dtype}").json
    assert [f["name"] for f in schema["fields"]] == ["region"] and schema["fields"][0]["required"]
    assert schema["fields"][0]["choices"] == ["North", "South"]
    assert any(t["id"] == dtype for t in schema["types"])

    missing = contributor.upload("inv.pdf", PDF, document_type_id=dtype)
    assert missing.status_code == 400 and "required" in missing.json["error"]
    wrong = contributor.upload("inv.pdf", PDF, document_type_id=dtype, **{f"meta_{field}": "East"})
    assert wrong.status_code == 400
    ok = contributor.upload("inv.pdf", PDF, document_type_id=dtype, tags="finance, q3", **{f"meta_{field}": "North"})
    assert ok.status_code == 201
    doc = ok.json["document"]
    assert doc["metadata"] == {"region": "North"} and sorted(doc["tags"]) == ["finance", "q3"]
    assert doc["author"] == "carl" and doc["version"] == "1.0" and doc["size"] == len(PDF)
    assert doc["extension"] == "pdf" and doc["created_at"]

    set_setting("tags_controlled", True)
    assert contributor.upload("x.pdf", PDF, tags="unapproved").status_code == 400


# =========================================================================== 7.6 Security & access control

def test_sec01_predefined_and_custom_roles(api, reader):
    roles = {r["name"]: r for r in api.get("/admin/roles").json["roles"]}
    assert {"Admin", "Records Manager", "Contributor", "Reader", "Auditor"} <= set(roles)
    r = api.post("/admin/roles", json={"name": "Legal", "permissions": ["view", "download", "edit"],
                                       "capabilities": ["audit"]})
    assert r.status_code == 201 and r.json["role"]["permissions"] == ["download", "edit", "view"]
    assert api.delete(f"/admin/roles/{roles['Reader']['id']}").status_code == 400
    assert reader.get("/admin/roles").status_code == 403
    assert reader.post("/folders", json={"name": "Nope"}).status_code == 403
    assert reader.get("/activity").status_code == 200  # personal audit log: own entries only


def test_sec02_permissions_at_repository_folder_document_and_version_level(api, reader):
    doc = api.doc("levels.txt", b"v1")
    api.version(doc, "levels.txt", b"v2")
    # version level: hide version 1 from the reader
    api.patch(f"/permissions/version/{doc}/1/inheritance", {"inherit": False, "copy": False})
    assert reader.get(f"/documents/{doc}/content?version=1").status_code == 403
    assert reader.get(f"/documents/{doc}/content?version=2").status_code == 200
    assert [v["number"] for v in reader.get(f"/documents/{doc}").json["versions"]] == [2]
    # document level: download removed for the reader
    api.patch(f"/permissions/document/{doc}/inheritance", {"inherit": False, "copy": False})
    api.post(f"/permissions/document/{doc}/grant",
             json={"principal_type": "user", "principal_id": reader.user.id, "permissions": ["view"]})
    assert reader.get(f"/documents/{doc}").status_code == 200
    assert reader.get(f"/documents/{doc}/content").status_code == 403
    # share levels
    owner_doc = api.doc("share.txt", b"s")
    assert reader.post(f"/permissions/document/{owner_doc}/share",
                       json={"principal_type": "user", "principal_id": reader.user.id, "level": "edit"}).status_code == 403
    assert api.post(f"/permissions/document/{owner_doc}/share",
                    json={"principal_type": "user", "principal_id": reader.user.id, "level": "edit"}).status_code == 200
    assert "edit" in reader.get(f"/documents/{owner_doc}").json["document"]["permissions"]
    # directory lookup for the share dialog
    found = reader.get("/directory?q=adm").json
    assert [u["username"] for u in found["users"]] == ["admin"]
    assert any(r["name"] == "Reader" for r in found["roles"])


def test_sec02_cannot_lock_yourself_out_of_the_repository(app):
    manager = Api(app, make_user("boss", ("Records Manager",)))
    r = manager.post("/permissions/repository/0/grant",
                     json={"principal_type": "user", "principal_id": manager.user.id, "permissions": ["view"]})
    assert r.status_code == 400


def test_sec03_cookie_login_with_mfa_and_sso_options(app, admin):
    from dms.security import mfa
    secret = mfa.new_secret()
    admin.mfa_secret, admin.mfa_enabled = mfa.seal(secret), True
    db.session.commit()
    c = app.test_client()
    csrf = c.get("/api/v1/auth/csrf").json["csrf_token"]
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "Correct-Horse-9"},
               headers={"X-CSRFToken": csrf})
    assert r.status_code == 202 and r.json["mfa_required"]
    csrf = r.json["csrf_token"]
    assert c.get("/api/v1/me").json["code"] == "mfa_required"
    code = mfa.totp(secret, int(time.time()) // 30)
    r = c.post("/api/v1/auth/mfa", json={"code": code}, headers={"X-CSRFToken": csrf})
    assert r.status_code == 200
    assert c.get("/api/v1/me").json["username"] == "admin"
    opts = c.get("/api/v1/auth/options").json
    assert "sso" in opts and "ldap_enabled" in opts


def test_sec04_encryption_at_rest(api):
    from dms.crypto import MAGIC
    secret = b"Top secret payroll figures " * 100
    doc = api.doc("pay.txt", secret)
    key = db.session.get(Document, doc).current_version.storage_key
    raw = get_storage().local_path(key).read_bytes()
    assert raw.startswith(MAGIC) and b"payroll" not in raw
    assert api.get(f"/documents/{doc}/content").data == secret
    status = api.get("/admin/status").json
    assert status["encryption"]["enabled"] is True


def test_sec05_ip_restriction_and_session_timeout(app, api, admin):
    set_setting("ip_allowlist", ["10.0.0.0/8"])
    assert api.get("/me", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert api.get("/me", environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code == 200
    set_setting("ip_allowlist", [])

    c = app.test_client()
    csrf = c.get("/api/v1/auth/csrf").json["csrf_token"]
    c.post("/api/v1/auth/login", json={"username": "admin", "password": "Correct-Horse-9"},
           headers={"X-CSRFToken": csrf})
    assert c.get("/api/v1/me").status_code == 200
    with c.session_transaction() as s:
        s["seen"] = int(time.time()) - 31 * 60
    assert c.get("/api/v1/me").status_code == 401


def test_admin_settings_round_trip_keeps_values(api):
    before = api.get("/admin/settings").json["settings"]
    r = api.put("/admin/settings", before)
    assert r.status_code == 200, r.json
    assert r.json["settings"] == before
    r = api.put("/admin/settings", {"mfa_required": True, "ip_allowlist": ["10.0.0.0/8", "127.0.0.1"],
                                    "group_role_map": {"DMS-Readers": "Reader"}})
    assert r.status_code == 200, r.json
    after = r.json["settings"]
    assert after["mfa_required"] is True and after["self_signup"] is before["self_signup"]
    assert after["allowed_extensions"] == before["allowed_extensions"]
    assert after["ip_allowlist"] == ["10.0.0.0/8", "127.0.0.1/32"]
    assert after["group_role_map"] == {"DMS-Readers": "Reader"}
    assert api.put("/admin/settings", {"unknown_key": 1}).status_code == 400
