import io
from pathlib import Path

from jinja2 import BaseLoader, TemplateNotFound

import dms
from dms import _register_api_cors, create_app
from dms.config import Config, TestConfig
from dms.extensions import db
from dms.models import GmailAccount, Role
from dms.services.emails import store_email

from .conftest import make_user
from .conftest import build_email


class MissingTemplateLoader(BaseLoader):
    def get_source(self, _environment, template):
        raise TemplateNotFound(template)


def test_cookie_api_login_requires_csrf_and_authenticates(anon):
    user = make_user("angular-user", ("Admin",), password="StrongPassword1!")
    csrf = anon.get("/api/v1/auth/csrf").json["csrf_token"]

    missing_csrf = anon.post("/api/v1/auth/login", json={"username": user.username,
                                                         "password": "StrongPassword1!"})
    assert missing_csrf.status_code == 400
    assert missing_csrf.json["code"] == "csrf_failed"

    login = anon.post("/api/v1/auth/login", json={"username": user.username,
                                                  "password": "StrongPassword1!"},
                      headers={"X-CSRFToken": csrf})
    assert login.status_code == 200
    assert login.json["user"]["username"] == user.username

    profile = anon.get("/api/v1/me")
    assert profile.status_code == 200
    assert profile.json["username"] == user.username

    csrf = login.json["csrf_token"]
    create = anon.post("/api/v1/folders", json={"name": "Angular-created"},
                       headers={"X-CSRFToken": csrf})
    assert create.status_code == 201
    assert create.json["folder"]["name"] == "Angular-created"


def test_api_is_available_without_template_loader(app, client, anon):
    app.jinja_env.loader = MissingTemplateLoader()
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json["username"] == "admin"
    unauthorized = anon.get("/api/v1/me")
    assert unauthorized.status_code == 401
    assert unauthorized.is_json


def test_app_starts_and_serves_api_with_missing_template_directory(monkeypatch, tmp_path):
    flask_factory = dms.Flask

    def flask_without_templates(import_name, **kwargs):
        kwargs["template_folder"] = str(tmp_path / "does-not-exist")
        return flask_factory(import_name, **kwargs)

    monkeypatch.setattr(dms, "Flask", flask_without_templates)
    app = create_app(
        TestConfig,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp_path / 'template-free.sqlite').as_posix()}",
        INSTANCE_DIR=tmp_path / "instance",
        STORAGE_DIR=tmp_path / "storage",
        LEGACY_EMAIL_DIR=tmp_path / "legacy_emails",
        GMAIL_TOKEN_FILE=tmp_path / "token.json",
        GMAIL_CREDENTIALS_FILE=tmp_path / "credentials.json",
        LEGACY_UI_ENABLED=False,
    )
    try:
        response = app.test_client().get("/api/v1/auth/options")
        assert response.status_code == 200
        assert not (tmp_path / "does-not-exist").exists()
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_api_only_runtime_disables_ui_but_keeps_provider_callbacks(tmp_path):
    assert Config.LEGACY_UI_ENABLED is False
    app = create_app(
        TestConfig,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp_path / 'api-only.sqlite').as_posix()}",
        INSTANCE_DIR=tmp_path / "instance",
        STORAGE_DIR=tmp_path / "storage",
        LEGACY_EMAIL_DIR=tmp_path / "legacy_emails",
        GMAIL_TOKEN_FILE=tmp_path / "token.json",
        GMAIL_CREDENTIALS_FILE=tmp_path / "credentials.json",
        LEGACY_UI_ENABLED=False,
    )
    try:
        assert app.template_folder is None
        assert app.static_folder is None
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        assert "/api/v1/auth/options" in rules
        assert "/auth/oidc/<name>" in rules
        assert "/auth/oidc/<name>/callback" in rules
        assert "/auth/saml/acs" in rules
        assert "/auth/saml/metadata" in rules
        assert "/gmail/oauth2callback" in rules
        assert "/dashboard" not in rules
        assert "/documents/upload" not in rules
        assert "static" not in {rule.endpoint for rule in app.url_map.iter_rules()}

        client = app.test_client()
        assert client.get("/api/v1/auth/options").status_code == 200
        missing_ui = client.get("/dashboard")
        assert missing_ui.status_code == 404
        assert missing_ui.is_json
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_cors_is_exact_origin_and_supports_credentials(app, client):
    app.config["API_CORS_ALLOWED_ORIGINS"] = ("http://localhost:4200",)
    _register_api_cors(app)
    allowed = client.options("/api/v1/me", headers={
        "Origin": "http://localhost:4200",
        "Access-Control-Request-Method": "GET",
    })
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:4200"
    assert allowed.headers["Access-Control-Allow-Credentials"] == "true"
    assert "Origin" in allowed.headers["Vary"]

    denied = client.options("/api/v1/me", headers={"Origin": "https://unexpected.example"})
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_frontend_assets_remain_in_repository():
    project = Path(__file__).resolve().parents[1]
    assert (project / "dms" / "templates").is_dir()
    assert (project / "dms" / "static").is_dir()


def test_cookie_api_document_upload_search_and_download(client):
    csrf = client.get("/api/v1/auth/csrf").json["csrf_token"]
    uploaded = client.post(
        "/api/v1/documents",
        data={"file": (io.BytesIO(b"Angular API test"), "angular.txt")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf},
    )
    assert uploaded.status_code == 201
    document = uploaded.json["document"]

    detail = client.get(f"/api/v1/documents/{document['id']}")
    assert detail.status_code == 200
    assert detail.json["document"]["name"] == "angular.txt"

    content = client.get(f"/api/v1/documents/{document['id']}/content")
    assert content.status_code == 200
    assert content.data == b"Angular API test"

    results = client.get("/api/v1/search?q=angular")
    assert results.status_code == 200
    assert any(row["id"] == document["id"] for row in results.json["results"])


def test_cookie_api_email_list_and_eml_download(client, app):
    with app.app_context():
        account = GmailAccount(email="api-test@example.com")
        db.session.add(account)
        db.session.commit()
        record, _ = store_email(account, "api123", build_email(subject="API mail"))
        email_id = record.id

    listing = client.get("/api/v1/emails")
    assert listing.status_code == 200
    assert any(item["id"] == email_id for item in listing.json["emails"])

    download = client.get(f"/api/v1/emails/{email_id}/eml")
    assert download.status_code == 200
    assert download.mimetype == "message/rfc822"


def test_admin_api_user_patch_preserves_unmentioned_fields(client):
    csrf = client.get("/api/v1/auth/csrf").json["csrf_token"]
    reader = db.session.query(Role).filter_by(name="Reader").one()
    created = client.post("/api/v1/admin/users", json={
        "username": "managed-user",
        "display_name": "Managed User",
        "email": "managed@example.com",
        "auth_source": "local",
        "roles": [reader.id],
        "password": "StrongPassword1!",
    }, headers={"X-CSRFToken": csrf})
    assert created.status_code == 201
    user_id = created.json["user"]["id"]

    updated = client.patch(f"/api/v1/admin/users/{user_id}", json={
        "display_name": "Updated Name",
    }, headers={"X-CSRFToken": csrf})
    assert updated.status_code == 200
    assert updated.json["user"]["display_name"] == "Updated Name"
    assert updated.json["user"]["email"] == "managed@example.com"
    assert updated.json["user"]["roles"] == [{"id": reader.id, "name": "Reader"}]


def test_api_returns_json_not_found_shape(client):
    response = client.get("/api/v1/documents/999999")
    assert response.status_code == 404
    assert response.json["code"] == "not_found"


def test_api_permissions_favourites_and_recycle_bin_lifecycle(client):
    csrf = client.get("/api/v1/auth/csrf").json["csrf_token"]
    uploaded = client.post(
        "/api/v1/documents",
        data={"file": (io.BytesIO(b"lifecycle"), "lifecycle.txt")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf},
    )
    document_id = uploaded.json["document"]["id"]

    favourite = client.put(f"/api/v1/favourites/document/{document_id}",
                           headers={"X-CSRFToken": csrf})
    assert favourite.status_code == 200 and favourite.json["favourite"]
    assert client.get("/api/v1/favourites").json["favourites"][0]["id"] == document_id

    reader = db.session.query(Role).filter_by(name="Reader").one()
    grant = client.post(f"/api/v1/permissions/document/{document_id}/grant", json={
        "principal_type": "role", "principal_id": reader.id, "permissions": ["view", "download"],
    }, headers={"X-CSRFToken": csrf})
    assert grant.status_code == 200
    entries = client.get(f"/api/v1/permissions/document/{document_id}").json["entries"]
    reader_entry = next(item for item in entries if item["principal_type"] == "role"
                        and item["principal_id"] == reader.id)
    assert set(reader_entry["permissions"]) == {"view", "download"}

    deleted = client.delete(f"/api/v1/documents/{document_id}", headers={"X-CSRFToken": csrf})
    assert deleted.status_code == 200
    assert document_id in [item["id"] for item in client.get("/api/v1/recycle-bin").json["documents"]]
    restored = client.post(f"/api/v1/documents/{document_id}/restore", headers={"X-CSRFToken": csrf})
    assert restored.status_code == 200
    assert not restored.json["document"]["deleted"]

    removed = client.delete(f"/api/v1/favourites/document/{document_id}",
                            headers={"X-CSRFToken": csrf})
    assert removed.status_code == 200 and not removed.json["favourite"]


def test_api_version_history_operations(client):
    csrf = client.get("/api/v1/auth/csrf").json["csrf_token"]
    uploaded = client.post(
        "/api/v1/documents",
        data={"file": (io.BytesIO(b"original"), "versions.txt")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf},
    )
    document_id = uploaded.json["document"]["id"]
    version = client.post(
        f"/api/v1/documents/{document_id}/versions",
        data={"file": (io.BytesIO(b"updated"), "versions.txt"), "major": "true"},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf},
    )
    assert version.status_code == 201
    assert version.json["version"] == "2.0"
    restored = client.post(f"/api/v1/documents/{document_id}/versions/1/restore",
                           json={"idempotency_key": "api-restore-test"},
                           headers={"X-CSRFToken": csrf})
    assert restored.status_code == 201
    assert restored.json["document"]["version"] == "2.1"
