import base64
import io
import json
from email.message import EmailMessage

import httplib2
import pytest
from googleapiclient.errors import HttpError

from dms import create_app
from dms.config import TestConfig
from dms.extensions import db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
       b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def app(tmp_path):
    app = create_app(
        TestConfig,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}",
        INSTANCE_DIR=tmp_path, STORAGE_DIR=tmp_path / "storage",
        LEGACY_EMAIL_DIR=tmp_path / "legacy_emails",
        GMAIL_TOKEN_FILE=tmp_path / "token.json", GMAIL_CREDENTIALS_FILE=tmp_path / "credentials.json",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
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


def upload(client, name, data, folder_id=None, key=None, url="/documents/upload", note=None):
    form = {"file": (io.BytesIO(data), name)}
    if folder_id:
        form["folder_id"] = str(folder_id)
    if key:
        form["idempotency_key"] = key
    if note:
        form["note"] = note
    return client.post(url, data=form, content_type="multipart/form-data",
                       headers={"Accept": "application/json", "X-Requested-With": "fetch"})


def post_json(client, url, data=None):
    return client.post(url, data=data or {}, headers={"Accept": "application/json", "X-Requested-With": "fetch"})


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
