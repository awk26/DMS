"""EML preview: the complete body (text, formatting, embedded images) is shown; attachments are
listed with name, type and size only and are never rendered inside the body."""
from email.message import EmailMessage

import pytest

from dms.extensions import db
from dms.models import GmailAccount
from dms.services.emails import store_email

from .conftest import PDF, PNG, build_email


@pytest.fixture
def account(app, admin):
    acc = GmailAccount(email="tester@example.com", user_id=admin.id)
    db.session.add(acc)
    db.session.commit()
    return acc


def raw(msg):
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def base(subject="Test"):
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>"
    msg["Subject"] = subject
    return msg


def detail(api, record):
    r = api.get(f"/emails/{record.id}")
    assert r.status_code == 200, r.json
    return {a["filename"]: a for a in r.json["email"]["attachments"]}


def body(api, record):
    r = api.get(f"/emails/{record.id}/body")
    assert r.status_code == 200, r.json
    return r.json["body_html"]


def test_inline_logo_is_shown_and_attachments_are_listed_with_details_only(api, account):
    record, _ = store_email(account, "m1", build_email())
    parts = detail(api, record)
    pdf = parts["report.pdf"]
    assert pdf["is_inline"] is False and pdf["content_type"] == "application/pdf" and pdf["size"] == len(PDF)
    inline = [a for a in parts.values() if a["is_inline"]]
    assert len(inline) == 1 and inline[0]["content_type"] == "image/png"

    html = body(api, record)
    assert f"/api/v1/emails/{record.id}/inline/{inline[0]['part_index']}" in html
    assert "report.pdf" not in html and f"/inline/{pdf['part_index']}" not in html
    assert api.get(f"/emails/{record.id}/inline/{inline[0]['part_index']}").status_code == 200
    assert api.get(f"/emails/{record.id}/inline/{pdf['part_index']}").status_code == 404


def test_attached_image_not_shown_in_the_body_is_an_attachment(api, account):
    msg = base()
    msg.set_content("See the photo.")
    msg.add_alternative("<p>See the photo.</p>", subtype="html")
    msg.add_attachment(PNG, maintype="image", subtype="png", filename="photo.png", cid="<photo1>")
    record, _ = store_email(account, "m2", raw(msg))
    photo = detail(api, record)["photo.png"]
    assert photo["is_inline"] is False                       # has a Content-ID but the body never shows it
    assert "inline/" not in body(api, record)
    assert api.get(f"/emails/{record.id}/inline/{photo['part_index']}").status_code == 404


def test_logo_marked_as_attachment_but_used_by_the_body_is_inline(api, account):
    """Outlook often sends signature logos with Content-Disposition: attachment and a wrong type."""
    msg = base()
    msg.set_content("Regards")
    msg.add_alternative('<p>Regards</p><img src="cid:sig-logo">', subtype="html")
    msg.add_attachment(PNG, maintype="application", subtype="octet-stream", filename="image001.png",
                       cid="<sig-logo>")
    record, _ = store_email(account, "m3", raw(msg))
    logo = detail(api, record)["image001.png"]
    assert logo["is_inline"] is True
    assert f"/inline/{logo['part_index']}" in body(api, record)
    r = api.get(f"/emails/{record.id}/inline/{logo['part_index']}")
    assert r.status_code == 200 and r.mimetype == "image/png"   # detected from the bytes


def test_formatting_styles_and_embedded_data_images_are_kept(api, account):
    msg = base()
    msg.set_content(
        "<html><head><title>Hidden title</title>"
        "<style>@import url(https://evil.example.com/x.css); .brand > td {color:#0a5}</style></head>"
        "<body><table class='brand'><tr><td style=\"background:url(cid:bg1)\"><b>Bold</b> "
        "<i>italic</i> <font color='red'>red</font></td></tr></table>"
        "<img src='data:image/png;base64,iVBORw0KGgo='>"
        "<a href='data:text/html,boom'>bad</a></body></html>", subtype="html")
    msg.add_related(PNG, maintype="image", subtype="png", cid="<bg1>")
    record, _ = store_email(account, "m4", raw(msg))
    html = body(api, record)
    assert ".brand > td {color:#0a5}" in html                    # <style> rules kept
    assert "@import" not in html and "evil.example.com" not in html
    assert "Hidden title" not in html
    assert "<b>Bold</b>" in html and "<i>italic</i>" in html and 'color="red"' in html
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in html  # embedded image kept
    assert "data:text/html" not in html                          # data: links are not
    assert f"url('/api/v1/emails/{record.id}/inline/" in html     # cid: in CSS rewritten


def test_every_body_part_is_shown_but_forwarded_messages_are_attachments(api, account):
    forwarded = base("Inner")
    forwarded.set_content("SECRET INNER TEXT")
    msg = base()
    msg.make_mixed()
    first, second = EmailMessage(), EmailMessage()
    first.set_content("<p>First part</p>", subtype="html")
    second.set_content("<p>Second part</p>", subtype="html")
    msg.attach(first)
    msg.attach(second)
    msg.add_attachment(forwarded)
    record, _ = store_email(account, "m5", raw(msg))
    html = body(api, record)
    assert "First part" in html and "Second part" in html
    assert "SECRET INNER TEXT" not in html
    assert any(a["content_type"] == "message/rfc822" and not a["is_inline"]
               for a in detail(api, record).values())
