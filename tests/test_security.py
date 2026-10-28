import io
import re

import pytest

from dms import create_app
from dms.config import TestConfig

from .conftest import PDF, upload

PAGES = ["/", "/documents/", "/folders/", "/emails/", "/recent", "/versions", "/activity",
         "/documents/trash", "/gmail/", "/does-not-exist"]


def test_no_development_status_labels(client):
    upload(client, "a.pdf", PDF)
    banned = re.compile(r"\b(POC|proof of concept|demo|prototype)\b", re.IGNORECASE)
    for url in PAGES + ["/documents/1"]:
        text = client.get(url).data.decode()
        visible = re.sub(r"<[^>]+>", " ", text)
        assert not banned.search(visible), f"{url} contains a development-status label"


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"


@pytest.fixture
def csrf_client(tmp_path):
    app = create_app(TestConfig, WTF_CSRF_ENABLED=True,
                     SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp_path / 'c.sqlite').as_posix()}",
                     STORAGE_DIR=tmp_path / "storage", INSTANCE_DIR=tmp_path)
    with app.app_context():
        from .conftest import login_as, make_user
        yield login_as(app.test_client(), make_user("csrfadmin"))
        from dms.extensions import db
        db.session.remove()
        db.engine.dispose()


def test_csrf_required_for_state_changes(csrf_client):
    r = csrf_client.post("/folders/", data={"name": "X"})
    assert r.status_code == 400
    r = csrf_client.post("/documents/upload", data={"file": (io.BytesIO(PDF), "a.pdf")},
                         content_type="multipart/form-data")
    assert r.status_code == 400
    # With the token from a rendered page, the request succeeds.
    page = csrf_client.get("/").data.decode()
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    r = csrf_client.post("/folders/", data={"name": "X", "csrf_token": token})
    assert r.status_code == 303


def test_errors_do_not_leak_tracebacks(client, monkeypatch):
    from dms.routes import main

    def broken():
        raise RuntimeError("secret internal detail")

    monkeypatch.setitem(client.application.view_functions, "main.recent", broken)
    r = client.get("/recent")
    assert r.status_code == 500
    assert b"secret internal detail" not in r.data and b"Traceback" not in r.data


def test_open_redirect_blocked(client):
    r = client.post("/folders/", data={"name": "Y", "next": "https://evil.example.com/"})
    assert r.status_code == 303 and "evil.example.com" not in r.headers["Location"]
    r = client.post("/folders/", data={"name": "Z", "next": "//evil.example.com/"})
    assert "evil.example.com" not in r.headers["Location"]


def test_storage_rejects_traversal_keys(app):
    from dms.storage import StorageError, get_storage
    storage = get_storage()
    for key in ("../credentials.json", "documents/../../x", "/etc/passwd", "documents\\..\\x", ""):
        with pytest.raises(StorageError):
            storage.local_path(key)
