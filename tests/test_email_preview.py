import pytest

from dms.extensions import db
from dms.models import GmailAccount
from dms.services.emails import extract_metadata, store_email

from .conftest import build_email


@pytest.fixture
def account(app):
    acc = GmailAccount(email="tester@example.com")
    db.session.add(acc)
    db.session.commit()
    return acc


def test_html_preview_is_sanitised_and_isolated(client, account):
    record, _ = store_email(account, "abc1", build_email())
    page = client.get(f"/emails/{record.id}")
    assert page.status_code == 200
    assert b'sandbox="allow-popups allow-popups-to-escape-sandbox"' in page.data
    body = client.get(f"/emails/{record.id}/body")
    assert body.status_code == 200
    csp = body.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp and "script-src" not in csp
    html = body.data.decode()
    assert "<script" not in html and "alert(1)" not in html
    assert "javascript:" not in html
    assert f"/emails/{record.id}/inline/" in html  # cid: image rewritten
    assert "<b>Bob</b>" in html


def test_inline_image_served_only_for_safe_types(client, account):
    record, _ = store_email(account, "abc2", build_email())
    inline = next(a for a in record.attachments if a.is_inline)
    r = client.get(f"/emails/{record.id}/inline/{inline.part_index}")
    assert r.status_code == 200 and r.mimetype == "image/png"
    pdf = next(a for a in record.attachments if a.filename == "report.pdf")
    assert client.get(f"/emails/{record.id}/inline/{pdf.part_index}").status_code == 404
    assert client.get(f"/emails/{record.id}/attachments/999").status_code == 404


def test_plain_text_only_message(client, account):
    record, _ = store_email(account, "abc3", build_email(html=False, attachment=False, inline_image=False))
    assert record.attachment_count == 0
    html = client.get(f"/emails/{record.id}/body").data.decode()
    assert '<pre class="plain">Hello Bob,' in html


def test_html_only_message(client, account):
    record, _ = store_email(account, "abc4", build_email(text=False, attachment=False, inline_image=False))
    assert "Hello Bob" in record.snippet and "<b>" not in record.snippet
    assert b"<b>Bob</b>" in client.get(f"/emails/{record.id}/body").data


def test_malformed_message_still_archived(account):
    raw = b"This is not really a MIME message\r\n\r\n\xff\xfe garbage"
    record, created = store_email(account, "abc5", raw)
    assert created and record.size == len(raw)
    assert extract_metadata(raw)["subject"] == ""


def test_email_search_and_filters(client, account):
    store_email(account, "s1", build_email(subject="Invoice March"))
    store_email(account, "s2", build_email(subject="Holiday plan", attachment=False))
    page = client.get("/emails/?q=invoice").data
    assert b"Invoice March" in page and b"Holiday plan" not in page
    page = client.get("/emails/?attachments=1").data
    assert b"Invoice March" in page and b"Holiday plan" not in page
    assert client.get("/emails/?state=archived&sort=subject").status_code == 200
    assert client.get("/emails/9999").status_code == 404
