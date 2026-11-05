"""Cross-cutting security checks: response headers, error bodies and storage integrity."""
import pytest

from dms.storage import StorageError, get_storage


def test_security_headers(api):
    r = api.get("/me")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Cache-Control"] == "no-store"


def test_errors_do_not_leak_tracebacks(api, monkeypatch):
    def broken():
        raise RuntimeError("secret internal detail")

    monkeypatch.setitem(api.client.application.view_functions, "api.dashboard", broken)
    r = api.get("/dashboard")
    assert r.status_code == 500 and r.is_json
    assert r.json["code"] == "internal_error"
    assert b"secret internal detail" not in r.data and b"Traceback" not in r.data


def test_no_html_pages_are_served(app):
    """The UI is the React app; the backend serves only /api/v1 and provider callbacks."""
    client = app.test_client()
    for url in ("/", "/documents/", "/folders/", "/emails/", "/shared", "/auth/login", "/static/css/app.css"):
        r = client.get(url)
        assert r.status_code == 404 and r.is_json, url


def test_storage_rejects_traversal_keys(app):
    storage = get_storage()
    for key in ("../credentials.json", "documents/../../x", "/etc/passwd", "documents\\..\\x", ""):
        with pytest.raises(StorageError):
            storage.local_path(key)


def test_encrypted_storage_detects_tampering(app):
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
