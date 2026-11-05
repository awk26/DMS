import io

from dms import _register_api_cors
from dms.extensions import db
from dms.models import GmailAccount, Role, User
from dms.services.emails import store_email

from .conftest import build_email, make_user


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


def test_app_is_api_only_and_keeps_provider_callbacks(app, client, anon):
    assert app.template_folder is None and app.static_folder is None
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/api/v1/auth/options", "/auth/oidc/<name>", "/auth/oidc/<name>/callback", "/auth/saml/acs",
            "/auth/saml/metadata", "/gmail/oauth2callback"} <= rules
    assert not [r for r in rules if not r.startswith(("/api/v1", "/auth/oidc", "/auth/saml", "/gmail/oauth2callback"))]
    assert client.get("/api/v1/me").json["username"] == "admin"
    unauthorized = anon.get("/api/v1/me")
    assert unauthorized.status_code == 401 and unauthorized.is_json
    missing_ui = client.get("/dashboard")
    assert missing_ui.status_code == 404 and missing_ui.is_json


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


def test_cookie_api_email_list_and_eml_download(client, admin):
    # Same app context (and database session) as the requests: a nested context would use another
    # MySQL connection whose snapshot the requests' session cannot see.
    account = GmailAccount(email="api-test@example.com", user_id=admin.id)  # only the owner sees it
    db.session.add(account)
    db.session.commit()
    record, _ = store_email(account, "api123", build_email(subject="API mail"))
    email_id = record.id

    listing = client.get("/api/v1/emails")
    assert listing.status_code == 200
    assert any(item["id"] == email_id for item in listing.json["emails"]), listing.json

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


def test_api_tokens_for_sync_and_mobile(app, admin):
    from dms.security.auth import new_api_token
    raw, _ = new_api_token(db.session.get(User, admin.id), "sync")
    h = {"Authorization": f"Bearer {raw}"}
    c = app.test_client()
    assert c.get("/api/v1/me", headers=h).json["username"] == "admin"
    r = c.post("/api/v1/documents", headers=h, data={"file": (io.BytesIO(b"from sync"), "sync.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 201
    doc_id = r.json["document"]["id"]
    assert c.get(f"/api/v1/documents/{doc_id}/content", headers=h).data == b"from sync"
    listing = c.get("/api/v1/documents?changed_since=2000-01-01T00:00:00Z", headers=h).json
    assert any(d["id"] == doc_id for d in listing["documents"])
    assert c.post(f"/api/v1/documents/{doc_id}/checkout", headers=h).status_code == 200
    rtoken, _ = new_api_token(make_user("rita", ("Reader",), email="rita@example.com"), "phone")
    r = c.post("/api/v1/documents", headers={"Authorization": f"Bearer {rtoken}"},
               data={"file": (io.BytesIO(b"x"), "x.txt")}, content_type="multipart/form-data")
    assert r.status_code == 403
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer dms_invalid"}).status_code == 401
