"""Archived email preview through the API: sanitised HTML bodies, safe inline images, search and filters."""
import pytest

from dms.extensions import db
from dms.models import GmailAccount
from dms.services.emails import extract_metadata, store_email

from .conftest import Api, build_email, make_user


@pytest.fixture
def account(app, admin):
    """The admin user's mailbox (emails are only visible to the mailbox owner)."""
    acc = GmailAccount(email="tester@example.com", user_id=admin.id)
    db.session.add(acc)
    db.session.commit()
    return acc


def test_html_preview_is_sanitised(api, account):
    record, _ = store_email(account, "abc1", build_email())
    assert api.get(f"/emails/{record.id}").json["email"]["subject"] == "Quarterly report"
    body = api.get(f"/emails/{record.id}/body")
    assert body.status_code == 200
    html = body.json["body_html"]
    assert "<script" not in html and "alert(1)" not in html
    assert "javascript:" not in html
    assert f"/api/v1/emails/{record.id}/inline/" in html          # cid: image rewritten to the API
    assert "<b>Bob</b>" in html


def test_inline_image_served_only_for_safe_types(api, account):
    record, _ = store_email(account, "abc2", build_email())
    inline = next(a for a in record.attachments if a.is_inline)
    r = api.get(f"/emails/{record.id}/inline/{inline.part_index}")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"
    pdf = next(a for a in record.attachments if a.filename == "report.pdf")
    assert api.get(f"/emails/{record.id}/inline/{pdf.part_index}").status_code == 404
    assert api.get(f"/emails/{record.id}/attachments/999").status_code == 404


def test_plain_text_only_message(api, account):
    record, _ = store_email(account, "abc3", build_email(html=False, attachment=False, inline_image=False))
    assert record.attachment_count == 0
    assert "Hello Bob," in api.get(f"/emails/{record.id}/body").json["body_html"]


def test_html_only_message(api, account):
    record, _ = store_email(account, "abc4", build_email(text=False, attachment=False, inline_image=False))
    assert "Hello Bob" in record.snippet and "<b>" not in record.snippet
    assert "<b>Bob</b>" in api.get(f"/emails/{record.id}/body").json["body_html"]


def test_malformed_message_still_archived(account):
    raw = b"This is not really a MIME message\r\n\r\n\xff\xfe garbage"
    record, created = store_email(account, "abc5", raw)
    assert created and record.size == len(raw)
    assert extract_metadata(raw)["subject"] == ""


def test_email_search_and_filters(api, account):
    store_email(account, "s1", build_email(subject="Invoice March"))
    store_email(account, "s2", build_email(subject="Holiday plan", attachment=False))

    def subjects(**params):
        r = api.get("/emails", query_string=params)
        assert r.status_code == 200, r.json
        return [e["subject"] for e in r.json["emails"]]

    assert subjects(q="invoice") == ["Invoice March"]
    assert subjects(attachments="1") == ["Invoice March"]
    assert sorted(subjects(state="archived", sort="subject")) == ["Holiday plan", "Invoice March"]
    assert api.get("/emails/9999").status_code == 404


def test_other_users_cannot_see_someone_elses_emails(app, account):
    record, _ = store_email(account, "p1", build_email(subject="Private"))
    other = Api(app, make_user("eve", ("Admin",), email="eve@example.com"))
    assert other.get("/emails").json["emails"] == []
    assert other.get(f"/emails/{record.id}").status_code == 404
    assert other.get(f"/emails/{record.id}/eml").status_code == 404
