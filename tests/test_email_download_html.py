"""Download an archived email as .eml (the original, unchanged) or .html (as shown in the DMS, with a
download link for each attached file)."""
import hashlib
from email.message import EmailMessage

import pytest

from dms.extensions import db
from dms.models import GmailAccount
from dms.services.emails import store_email

from .conftest import PDF, PNG, Api, build_email, make_user


@pytest.fixture
def alice(app, admin):
    return Api(app, make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


@pytest.fixture
def account(app, alice):
    acc = GmailAccount(email="alice@gmail.example", user_id=alice.user.id, is_active=True, token_enc="sealed")
    db.session.add(acc)
    db.session.commit()
    return acc


def two_attachments():
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>"
    msg["Subject"] = "Two files"
    msg.set_content("See attached.")
    msg.add_alternative('<p>See <b>attached</b>.</p><img src="cid:logo1">', subtype="html")
    msg.get_payload()[-1].add_related(PNG, maintype="image", subtype="png", cid="<logo1>")
    msg.add_attachment(PDF, maintype="application", subtype="pdf", filename="report.pdf")
    msg.add_attachment(b"a,b\n1,2\n", maintype="text", subtype="csv", filename="data & figures.csv")
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def test_html_download_shows_the_email_and_links_each_attachment(app, alice, account):
    app.config["ANGULAR_FRONTEND_URL"] = "https://dms.example.com/app"
    raw = two_attachments()
    record, _ = store_email(account, "m1", raw)
    parts = {a.filename: a.part_index for a in record.attachments}

    r = alice.get(f"/emails/{record.id}/html")
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert ".html" in r.headers["Content-Disposition"]
    page = r.data.decode("utf-8")

    # Headers and body as shown in the DMS, with the inline image embedded (works offline).
    assert "Two files" in page and "Alice &lt;alice@example.com&gt;" in page and "Bob &lt;bob@example.com&gt;" in page
    assert "<b>attached</b>" in page and "data:image/png;base64," in page
    assert "cid:" not in page

    # One absolute link per attached file (the inline image is not listed), after the body.
    assert "Attachments (2)" in page
    for name in ("report.pdf", "data &amp; figures.csv"):
        assert name in page
    for idx in (parts["report.pdf"], parts["data & figures.csv"]):
        assert f'href="https://dms.example.com/api/v1/emails/{record.id}/attachments/{idx}"' in page
    assert page.index("<b>attached</b>") < page.index("Attachments (2)")

    # The link downloads the file as an attachment.
    d = alice.get(f"/emails/{record.id}/attachments/{parts['report.pdf']}")
    assert d.status_code == 200 and d.data == PDF
    assert d.headers["Content-Disposition"].startswith("attachment;")

    # The .eml download is still the archived original, byte for byte.
    eml = alice.get(f"/emails/{record.id}/eml")
    assert eml.data == raw and hashlib.sha256(eml.data).hexdigest() == record.sha256


def test_html_download_without_attachments_has_no_attachment_block(app, alice, account):
    record, _ = store_email(account, "m2", build_email(attachment=False))
    page = alice.get(f"/emails/{record.id}/html").data.decode("utf-8")
    assert "Hello <b>Bob</b>" in page
    assert "Attachments (" not in page and "/attachments/" not in page
    assert "<script" not in page and "javascript:" not in page   # sanitised like the preview


def test_infected_email_lists_attachments_without_links(app, alice, account):
    record, _ = store_email(account, "m3", build_email())
    record.scan_status = "infected"
    db.session.commit()
    page = alice.get(f"/emails/{record.id}/html").data.decode("utf-8")
    assert "report.pdf" in page and "Malware was detected" in page
    assert "/attachments/" not in page


def test_html_download_needs_download_permission(app, api, alice, bob, account):
    record, _ = store_email(account, "m4", build_email())
    # Not shared: neither another user nor the administrator can get it.
    assert bob.get(f"/emails/{record.id}/html").status_code == 404
    assert api.get(f"/emails/{record.id}/html").status_code == 404
    # View-only share: no download.
    alice.post(f"/emails/{record.id}/shares",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert bob.get(f"/emails/{record.id}/html").status_code == 403
    idx = next(a.part_index for a in record.attachments if a.filename == "report.pdf")
    assert bob.get(f"/emails/{record.id}/attachments/{idx}").status_code == 403
    # View & download share: allowed, and the links work for them.
    alice.post(f"/emails/{record.id}/shares",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "download"})
    assert bob.get(f"/emails/{record.id}/html").status_code == 200
    assert bob.get(f"/emails/{record.id}/attachments/{idx}").data == PDF


def test_attachment_link_requires_login(app, anon, alice, account):
    record, _ = store_email(account, "m5", build_email())
    idx = next(a.part_index for a in record.attachments if a.filename == "report.pdf")
    assert anon.get(f"/api/v1/emails/{record.id}/attachments/{idx}").status_code == 401
    assert anon.get(f"/api/v1/emails/{record.id}/html").status_code == 401
