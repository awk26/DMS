"""Shared test fixtures.

Tests run against a dedicated MySQL database (MySQL is the only supported database).

* ``TEST_DATABASE_URL`` selects it. When unset, the database from ``DATABASE_URL`` is used with
  ``_test`` appended to its name (``DMS`` -> ``DMS_test``) on the same server.
* The database name must end in ``_test`` and differ from ``DATABASE_URL``'s, because every table
  in it is emptied after each test.
* It is created if missing; tables are created and the stored procedures installed once per run.
"""
import base64
import io
import json
import os
from email.message import EmailMessage

import httplib2
import pytest
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from dms import create_app
from dms.config import TestConfig
from dms.extensions import db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
       b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# ------------------------------------------------------------------- MySQL test database

def _test_database_url():
    from dms.config import database_uri
    explicit = os.environ.get("TEST_DATABASE_URL")
    app_url = database_uri()  # DB_HOST/DB_USER/... or DATABASE_URL, same as the app
    if explicit:
        url = make_url(explicit)
    elif app_url:
        base = make_url(app_url)
        url = base.set(database=f"{base.database}_test")
    else:
        pytest.exit("Set TEST_DATABASE_URL (or DATABASE_URL) to a MySQL server to run the tests.", returncode=2)
    if not url.drivername.startswith("mysql"):
        pytest.exit(f"TEST_DATABASE_URL must be MySQL, got '{url.drivername}'.", returncode=2)
    if not (url.database or "").endswith("_test"):
        pytest.exit(f"Refusing to run: test database '{url.database}' must end in '_test' "
                    "(every table in it is emptied after each test).", returncode=2)
    if app_url and make_url(app_url).database == url.database \
            and make_url(app_url).host == url.host:
        pytest.exit("Refusing to run: TEST_DATABASE_URL points at the application database.", returncode=2)
    return url


TEST_DB_URL = _test_database_url()
TEST_DB_URI = TEST_DB_URL.render_as_string(hide_password=False)


def _paths(tmp_path):
    return dict(INSTANCE_DIR=tmp_path, STORAGE_DIR=tmp_path / "storage",
                GMAIL_TOKEN_FILE=tmp_path / "token.json", GMAIL_CREDENTIALS_FILE=tmp_path / "credentials.json")


def make_test_app(tmp_path, config=TestConfig, **overrides):
    """create_app() against the MySQL test database, with every file path under ``tmp_path``."""
    return create_app(config, **{"SQLALCHEMY_DATABASE_URI": TEST_DB_URI, **_paths(tmp_path), **overrides})


_admin_engine = None


def _engine():
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    return _admin_engine


def _empty_all_tables():
    with _engine().connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"))]
        conn.execute(text("SET SESSION lock_wait_timeout = 30"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for table in tables:
                conn.execute(text(f"TRUNCATE TABLE `{table}`"))
        finally:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()


@pytest.fixture(scope="session", autouse=True)
def mysql_test_database(tmp_path_factory):
    """Create the test database, its tables and stored procedures once per run."""
    server = create_engine(TEST_DB_URL.set(database=""))
    with server.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_URL.database}` "
                          "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    server.dispose()
    setup_app = make_test_app(tmp_path_factory.mktemp("db-setup"))  # create_all() + upgrade()
    with setup_app.app_context():
        from dms.sp import install_procedures
        install_procedures()
        db.session.remove()
        db.engine.dispose()
    _empty_all_tables()
    yield
    if _admin_engine is not None:
        _admin_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database():
    """Every test starts from empty tables (apps re-seed the predefined roles on start-up)."""
    yield
    _empty_all_tables()


@pytest.fixture
def app(tmp_path):
    app = make_test_app(tmp_path, MAX_CONTENT_LENGTH=2 * 1024 * 1024)
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


def make_user(username, roles=("Admin",), password="Correct-Horse-9", **kw):
    from dms.models import User
    from dms.security.auth import hash_password, role_by_name
    user = User(username=username, password_hash=hash_password(password),
                roles=[role_by_name(r) for r in roles], **kw)
    db.session.add(user)
    db.session.commit()
    return user


def login_as(client, user):
    """Start an authenticated session for ``user`` (first factor + MFA done, approved device)."""
    import time
    from dms.models import UserDevice
    device = UserDevice(user_id=user.id, device_hash=f"test-{user.id}-{time.time_ns()}", status="approved")
    db.session.add(device)
    db.session.commit()
    now = int(time.time())
    with client.session_transaction() as sess:
        sess.clear()
        sess.update({"uid": user.id, "auth_at": now, "seen": now, "mfa_ok": True, "device_id": device.id,
                     "method": "password"})
    return client


@pytest.fixture
def admin(app):
    return make_user("admin", ("Admin",), email="admin@example.com", display_name="Admin User")


@pytest.fixture
def client(app, admin):
    return login_as(app.test_client(), admin)


@pytest.fixture
def anon(app, admin):
    return app.test_client()


class Api:
    """Test client for /api/v1 bound to one user's personal access token (no session or CSRF needed)."""

    def __init__(self, app, user):
        from dms.security.auth import new_api_token
        self.user = user
        raw, _ = new_api_token(user, "tests")
        self.client = app.test_client()
        self.headers = {"Authorization": f"Bearer {raw}"}

    def _call(self, method, url, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        return getattr(self.client, method)("/api/v1" + url, headers=headers, **kw)

    def get(self, url, **kw):
        return self._call("get", url, **kw)

    def post(self, url, json=None, **kw):
        return self._call("post", url, json=json, **kw) if json is not None else self._call("post", url, **kw)

    def patch(self, url, json=None, **kw):
        return self._call("patch", url, json=json or {}, **kw)

    def put(self, url, json=None, **kw):
        return self._call("put", url, json=json or {}, **kw)

    def delete(self, url, json=None, **kw):
        return self._call("delete", url, json=json, **kw) if json is not None else self._call("delete", url, **kw)

    def folder(self, name, parent=None):
        r = self.post("/folders", json={"name": name, "parent_id": parent})
        assert r.status_code == 201, r.json
        return r.json["folder"]["id"]

    def upload(self, name, data, folder_id=None, **fields):
        form = {"file": (io.BytesIO(data), name), **{k: str(v) for k, v in fields.items()}}
        if folder_id:
            form["folder_id"] = str(folder_id)
        return self.post("/documents", data=form, content_type="multipart/form-data")

    def doc(self, name, data, folder_id=None, **fields):
        r = self.upload(name, data, folder_id, **fields)
        assert r.status_code == 201, r.json
        return r.json["document"]["id"]

    def version(self, doc_id, name, data, **fields):
        form = {"file": (io.BytesIO(data), name), **{k: str(v) for k, v in fields.items()}}
        return self.post(f"/documents/{doc_id}/versions", data=form, content_type="multipart/form-data")


@pytest.fixture
def api(app, admin):
    return Api(app, admin)


# ------------------------------------------------------------------- mocked Gmail API

def http_error(status, message="error"):
    resp = httplib2.Response({"status": str(status)})
    return HttpError(resp, json.dumps({"error": {"code": status, "message": message}}).encode())


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeGmail:
    """In-memory stand-in for the Gmail API surface used by the sync engine."""

    def __init__(self, email="tester@example.com"):
        self.email = email
        self.history_id = 1000
        self.store = {}
        self.events = []           # list of (history_id, record)
        self.expired_below = None  # startHistoryId values below this return 404
        self.fail_get = {}         # message id -> exception to raise on messages.get(raw)
        self.get_calls = []

    # helpers for tests ---------------------------------------------------------------
    def _bump(self, record):
        self.history_id += 1
        record["id"] = str(self.history_id)
        self.events.append((self.history_id, record))

    def add(self, mid, raw, labels=("INBOX", "UNREAD"), thread_id=None):
        self.store[mid] = {"raw": raw, "labels": list(labels), "threadId": thread_id or mid,
                              "internalDate": "1760000000000"}
        self._bump({"messagesAdded": [{"message": {"id": mid}}]})

    def _labels(self, mid, add=(), remove=()):
        msg = self.store[mid]
        msg["labels"] = [l for l in msg["labels"] if l not in remove] + [l for l in add if l not in msg["labels"]]
        rec = {}
        if remove:
            rec["labelsRemoved"] = [{"message": {"id": mid}, "labelIds": list(remove)}]
        if add:
            rec["labelsAdded"] = [{"message": {"id": mid}, "labelIds": list(add)}]
        self._bump(rec)

    def archive(self, mid):
        self._labels(mid, remove=("INBOX",))

    def move_to_inbox(self, mid):
        self._labels(mid, add=("INBOX",))

    def trash(self, mid):
        self._labels(mid, add=("TRASH",), remove=("INBOX",) if "INBOX" in self.store[mid]["labels"] else ())

    def delete(self, mid):
        del self.store[mid]
        self._bump({"messagesDeleted": [{"message": {"id": mid}}]})

    # API surface -------------------------------------------------------------------
    def users(self):
        return self

    def getProfile(self, userId):
        return FakeRequest(lambda: {"emailAddress": self.email, "historyId": str(self.history_id)})

    def history_list(self, userId, startHistoryId, historyTypes=None, maxResults=100, pageToken=None):
        def run():
            start = int(startHistoryId)
            if self.expired_below is not None and start < self.expired_below:
                raise http_error(404, "Requested entity was not found.")
            records = [r for hid, r in self.events if hid > start]
            offset = int(pageToken or 0)
            page = records[offset:offset + 2]  # tiny pages to exercise pagination
            resp = {"historyId": str(self.history_id), "history": page}
            if offset + 2 < len(records):
                resp["nextPageToken"] = str(offset + 2)
            return resp
        return FakeRequest(run)

    def history(self):
        outer = self

        class H:
            def list(self, **kw):
                return outer.history_list(**kw)
        return H()

    def messages(self):
        outer = self

        class M:
            def get(self, userId, id, format="full"):
                def run():
                    outer.get_calls.append((id, format))
                    if format == "raw" and id in outer.fail_get:
                        exc = outer.fail_get[id]
                        if isinstance(exc, list):
                            if exc:
                                raise exc.pop(0)
                        else:
                            raise exc
                    msg = outer.store.get(id)
                    if msg is None:
                        raise http_error(404, "Not Found")
                    out = {"id": id, "threadId": msg["threadId"], "labelIds": list(msg["labels"]),
                           "internalDate": msg["internalDate"], "snippet": "snippet"}
                    if format == "raw":
                        out["raw"] = base64.urlsafe_b64encode(msg["raw"]).decode()
                    return out
                return FakeRequest(run)

            def list(self, userId, q="", maxResults=100, includeSpamTrash=False, pageToken=None):
                def run():
                    skip = {"INBOX", "TRASH", "SPAM", "DRAFT", "SENT"}
                    ids = [m for m, v in outer.store.items() if not (set(v["labels"]) & skip)]
                    return {"messages": [{"id": m, "threadId": outer.store[m]["threadId"]} for m in ids]}
                return FakeRequest(run)
        return M()


def build_email(subject="Quarterly report", html=True, text=True, attachment=True, inline_image=True):
    msg = EmailMessage()
    msg["From"] = "Alice Example <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>"
    msg["Cc"] = "Carol <carol@example.com>"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 13 Oct 2025 09:30:00 +0000"
    msg["Message-ID"] = "<abc123@example.com>"
    if text:
        msg.set_content("Hello Bob,\nPlease find the report attached.\n")
    if html:
        body = ('<p>Hello <b>Bob</b></p><script>alert(1)</script>'
                '<img src="cid:logo123"><a href="javascript:alert(2)">x</a>'
                '<img src="https://tracker.example.com/pixel.gif">')
        if text:
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body, subtype="html")
        if inline_image:
            html_part = msg.get_payload()[-1] if msg.is_multipart() else msg
            html_part.add_related(PNG, maintype="image", subtype="png", cid="<logo123>")
    if attachment:
        msg.add_attachment(PDF, maintype="application", subtype="pdf", filename="report.pdf")
    # Gmail returns CRLF line endings in raw messages.
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
