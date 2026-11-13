"""Images that archived emails link to (newsletter logos on the sender's servers) are downloaded once,
stored with the email and shown from the DMS; the archived .eml is never changed."""
import hashlib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from dms.extensions import db
from dms.models import EmailImage, GmailAccount
from dms.services import email_images
from dms.services.emails import store_email

from .conftest import PNG, Api, make_user

LOGO = "https://mcusercontent.com/abc/images/logo.png"
BANNER = "https://cdn.example.com/banner.png?x=1&y=2"
BROKEN = "https://cdn.example.com/missing.png"


@pytest.fixture
def account(app, admin):
    app.config["EMAIL_REMOTE_IMAGES"] = True
    acc = GmailAccount(email="tester@example.com", user_id=admin.id)
    db.session.add(acc)
    db.session.commit()
    return acc


@pytest.fixture
def fake_web(monkeypatch):
    calls = []

    def download(url, max_bytes, timeout):
        calls.append(url)
        if url == BROKEN:
            raise email_images.Refused("HTTP 404")
        return PNG, "image/png"
    monkeypatch.setattr(email_images, "download", download)
    return calls


def newsletter():
    msg = EmailMessage()
    msg["From"] = "J M BAXI GROUP <corp@example.com>"
    msg["To"] = "pawan@example.com"
    msg["Subject"] = "Diwali"
    msg.set_content("Happy Diwali")
    msg.add_alternative(
        f'<table background="{LOGO}"><tr><td><img src="{LOGO}" alt="logo">'
        f'<img src="{BANNER.replace("&", "&amp;")}"><img src="{BROKEN}">'
        f'<div style="background-image:url(\'{BANNER}\')">Happy Diwali</div></td></tr></table>', subtype="html")
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def test_linked_images_are_stored_and_shown_from_the_dms(app, api, account, fake_web):
    raw = newsletter()
    record, _ = store_email(account, "n1", raw)
    html = api.get(f"/emails/{record.id}/body").json["body_html"]   # first view downloads them
    assert sorted(fake_web) == sorted([LOGO, BANNER, BROKEN])          # each address fetched once
    rows = {r.url: r for r in db.session.query(EmailImage).filter_by(email_id=record.id)}
    assert rows[LOGO].status == "stored" and rows[BANNER].status == "stored" and rows[BROKEN].status == "failed"

    assert LOGO not in html and "cdn.example.com/banner" not in html  # never loaded from the internet
    assert f"/api/v1/emails/{record.id}/images/{rows[LOGO].id}" in html
    assert f"url('/api/v1/emails/{record.id}/images/{rows[BANNER].id}')" in html
    r = api.get(f"/emails/{record.id}/images/{rows[LOGO].id}")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == PNG
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"
    assert api.get(f"/emails/{record.id}/images/{rows[BROKEN].id}").status_code == 404

    # Opening it again does not download anything again.
    api.get(f"/emails/{record.id}/body")
    assert len(fake_web) == 3

    # The archived original is unchanged.
    assert api.get(f"/emails/{record.id}/eml").data == raw
    assert hashlib.sha256(api.get(f"/emails/{record.id}/eml").data).hexdigest() == record.sha256


def test_eml_copy_with_images_embeds_them(app, api, account, fake_web):
    record, _ = store_email(account, "n2", newsletter())
    api.get(f"/emails/{record.id}/body")
    r = api.get(f"/emails/{record.id}/eml?with_images=1")
    assert r.status_code == 200 and "(with images).eml" in r.headers["Content-Disposition"]
    copy = BytesParser(policy=policy.default).parsebytes(r.data)
    html = copy.get_body(preferencelist=("html",)).get_content()
    assert LOGO not in html and "cid:dms-img-" in html
    images = [p for p in copy.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 2 and all(p.get_payload(decode=True) == PNG for p in images)
    assert BROKEN in html                                   # not stored: left as the original link
    assert "Copy with linked images embedded" in copy["X-DMS-Export"]


def test_other_users_cannot_see_the_images(app, account, fake_web, api):
    record, _ = store_email(account, "n3", newsletter())
    api.get(f"/emails/{record.id}/body")
    row = db.session.query(EmailImage).filter_by(email_id=record.id, status="stored").first()
    eve = Api(app, make_user("eve", ("Admin",), email="eve@example.com"))
    assert eve.get(f"/emails/{record.id}/images/{row.id}").status_code == 404
    assert eve.get(f"/emails/{record.id}/eml?with_images=1").status_code == 404


def test_disabled_setting_downloads_nothing(app, api, account, fake_web):
    app.config["EMAIL_REMOTE_IMAGES"] = False
    record, _ = store_email(account, "n4", newsletter())
    assert api.get(f"/emails/{record.id}/body").status_code == 200
    assert fake_web == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.png", "http://10.0.0.5/x.png", "http://192.168.1.1/x.png", "http://169.254.169.254/latest",
    "http://[::1]/x.png", "ftp://example.com/x.png", "http://example.com:8080/x.png", "http://user:pw@example.com/x",
])
def test_private_or_unusual_addresses_are_refused(url):
    with pytest.raises(email_images.Refused):
        email_images._check_public(url)


def test_only_image_urls_in_the_body_are_collected():
    text = ('<img src="https://a.example/1.png"><script src="https://evil.example/x.js"></script>'
            '<iframe src="https://evil.example/frame"></iframe><td background="//b.example/2.gif">'
            '<p style="background:url(https://c.example/3.jpg)">x</p><img src="cid:logo"><img src="/relative.png">')
    assert email_images.image_urls([text]) == [
        "https://a.example/1.png", "https://b.example/2.gif", "https://c.example/3.jpg"]
