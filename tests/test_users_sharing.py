"""Self sign-up, My Files, ownership, simple sharing and the user-managed recycle bin."""
import pytest

from dms.extensions import db
from dms.models import AccessEntry, Document, Folder, User
from dms.services import settings

from .conftest import PDF, login_as, make_user, post_json, upload

STRONG = "Correct-Horse-9x"


def signup(client, username="newbie", email="newbie@example.com", password=STRONG, password2=None, name="New Bie"):
    return client.post("/auth/register", data={"display_name": name, "email": email, "username": username,
                                               "password": password, "password2": password2 or password})


def home_of(client, user):
    r = client.get("/my-files")
    assert r.status_code == 302
    db.session.refresh(user)
    return user.home_folder_id


@pytest.fixture
def alice(app, admin):
    return make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice")


@pytest.fixture
def bob(app, admin):
    return make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob")


def as_user(app, user):
    return login_as(app.test_client(), user)


# --------------------------------------------------------------------------- sign-up

def test_login_page_offers_sign_up(anon):
    assert "Create an account" in anon.get("/auth/login").data.decode()


def test_sign_up_creates_contributor_with_private_home_and_signs_in(anon):
    r = signup(anon)
    assert r.status_code == 302 and "/auth/login" not in r.headers["Location"]
    user = db.session.query(User).filter_by(username="newbie").one()
    assert [r.name for r in user.roles] == ["Contributor"]
    assert user.home_folder_id is not None
    home = db.session.get(Folder, user.home_folder_id)
    assert home.inherit_permissions is False
    page = anon.get("/").data.decode()
    assert "Welcome, New Bie" in page and "My Files" in page


def test_sign_up_validation(anon, admin):
    assert signup(anon, username="admin", email="x@example.com").status_code == 400  # taken
    assert signup(anon, email="admin@example.com").status_code == 400  # email taken
    assert signup(anon, password="short").status_code == 400
    assert signup(anon, password2="Different-Horse-9").status_code == 400
    assert signup(anon, email="not-an-email").status_code == 400
    assert db.session.query(User).filter_by(username="newbie").first() is None


def test_sign_up_can_be_disabled(anon):
    settings.set_value("self_signup", False)
    db.session.commit()
    assert anon.get("/auth/register").status_code == 302
    assert signup(anon).status_code == 302
    assert db.session.query(User).filter_by(username="newbie").first() is None
    assert "Create an account" not in anon.get("/auth/login").data.decode()


def test_sign_up_never_grants_admin(anon):
    settings.set_value("signup_default_role", "Admin")
    db.session.commit()
    signup(anon)
    user = db.session.query(User).filter_by(username="newbie").one()
    assert not user.is_admin


# --------------------------------------------------------------------------- My Files

def test_my_files_is_private(app, alice, bob):
    a = as_user(app, alice)
    home = home_of(a, alice)
    doc_id = upload(a, "plan.pdf", PDF, folder_id=home).json["document_id"]
    b = as_user(app, bob)
    assert b.get(f"/folders/{home}").status_code == 404
    assert b.get(f"/documents/{doc_id}").status_code == 404
    assert "plan.pdf" not in b.get("/documents/").data.decode()


def test_my_files_cannot_be_deleted_renamed_or_moved(app, alice):
    a = as_user(app, alice)
    home = home_of(a, alice)
    assert post_json(a, f"/folders/{home}/delete").status_code == 400
    assert post_json(a, f"/folders/{home}/rename", {"name": "Other"}).status_code == 400
    assert db.session.get(Folder, home).deleted_at is None


# --------------------------------------------------------------------------- ownership

def test_creator_owns_uploads_in_shared_library(app, client, alice, bob):
    lib = post_json(client, "/folders/", {"name": "Projects"}).json["folder_id"]
    a = as_user(app, alice)
    doc_id = upload(a, "mine.pdf", PDF, folder_id=lib).json["document_id"]
    sub = post_json(a, "/folders/", {"name": "Alice work", "parent_id": lib}).json["folder_id"]
    b = as_user(app, bob)
    # Bob (also a Contributor) can see the shared library but not delete Alice's things
    assert post_json(b, f"/documents/{doc_id}/delete").status_code == 403
    assert post_json(b, f"/folders/{sub}/delete").status_code == 403
    # Alice can delete what she created
    assert post_json(a, f"/documents/{doc_id}/delete").status_code == 200
    assert post_json(a, f"/folders/{sub}/delete").status_code == 200


# --------------------------------------------------------------------------- sharing

def test_owner_shares_with_simple_levels(app, alice, bob):
    a = as_user(app, alice)
    home = home_of(a, alice)
    doc_id = upload(a, "report.pdf", PDF, folder_id=home).json["document_id"]
    b = as_user(app, bob)
    assert b.get(f"/documents/{doc_id}").status_code == 404

    page = a.get(f"/permissions/document/{doc_id}").data.decode()
    assert "Share with people" in page and "Bob" in page

    assert post_json(a, f"/permissions/document/{doc_id}/share",
                     {"principal": f"user:{bob.id}", "level": "view"}).status_code == 200
    assert b.get(f"/documents/{doc_id}").status_code == 200
    assert b.get(f"/documents/{doc_id}/download").status_code == 403
    shared = b.get("/shared").data.decode()
    assert "report.pdf" in shared

    # Raise to edit, then remove
    post_json(a, f"/permissions/document/{doc_id}/share", {"principal": f"user:{bob.id}", "level": "edit"})
    assert b.get(f"/documents/{doc_id}/download").status_code == 200
    entry = db.session.query(AccessEntry).filter_by(target_type="document", target_id=doc_id,
                                                    principal_type="user", principal_id=bob.id).one()
    assert post_json(a, f"/permissions/document/{doc_id}/entries/{entry.id}/remove").status_code == 200
    assert b.get(f"/documents/{doc_id}").status_code == 404


def test_shared_folder_gives_access_to_contents(app, alice, bob):
    a = as_user(app, alice)
    home = home_of(a, alice)
    folder = post_json(a, "/folders/", {"name": "Team", "parent_id": home}).json["folder_id"]
    doc_id = upload(a, "inside.pdf", PDF, folder_id=folder).json["document_id"]
    post_json(a, f"/permissions/folder/{folder}/share", {"principal": f"user:{bob.id}", "level": "edit"})
    b = as_user(app, bob)
    assert "Team" in b.get("/shared").data.decode()
    assert b.get(f"/documents/{doc_id}").status_code == 200
    assert upload(b, "bob.pdf", PDF, folder_id=folder).status_code == 201


def test_sharer_without_full_control_cannot_grant_it(app, client, alice, bob):
    lib = post_json(client, "/folders/", {"name": "Common"}).json["folder_id"]
    doc_id = upload(client, "c.pdf", PDF, folder_id=lib).json["document_id"]
    a = as_user(app, alice)  # Contributor: may share, may not manage
    r = post_json(a, f"/permissions/document/{doc_id}/share", {"principal": f"user:{bob.id}", "level": "full"})
    assert r.status_code == 400
    assert post_json(a, f"/permissions/document/{doc_id}/share",
                     {"principal": f"user:{bob.id}", "level": "view"}).status_code == 200


def test_cannot_change_own_access(app, alice):
    a = as_user(app, alice)
    home = home_of(a, alice)
    doc_id = upload(a, "x.pdf", PDF, folder_id=home).json["document_id"]
    r = post_json(a, f"/permissions/document/{doc_id}/share", {"principal": f"user:{alice.id}", "level": "view"})
    assert r.status_code == 400


# --------------------------------------------------------------------------- favourites

def test_star_from_document_list(app, alice):
    a = as_user(app, alice)
    home = home_of(a, alice)
    doc_id = upload(a, "fav.pdf", PDF, folder_id=home).json["document_id"]
    page = a.get(f"/folders/{home}").data.decode()
    assert "Add to favourites" in page
    post_json(a, "/folders/favourite", {"kind": "document", "id": doc_id})
    assert "fav.pdf" in a.get("/favourites").data.decode()


# --------------------------------------------------------------------------- recycle bin

def test_user_can_delete_forever_and_empty_bin(app, alice, bob):
    a = as_user(app, alice)
    home = home_of(a, alice)
    d1 = upload(a, "one.pdf", PDF, folder_id=home).json["document_id"]
    d2 = upload(a, "two.pdf", PDF, folder_id=home).json["document_id"]
    f1 = post_json(a, "/folders/", {"name": "Old", "parent_id": home}).json["folder_id"]
    b = as_user(app, bob)
    bh = home_of(b, bob)
    bd = upload(b, "bobs.pdf", PDF, folder_id=bh).json["document_id"]
    for url in (f"/documents/{d1}/delete", f"/documents/{d2}/delete", f"/folders/{f1}/delete"):
        assert post_json(a, url).status_code == 200
    post_json(b, f"/documents/{bd}/delete")

    page = a.get("/recycle-bin").data.decode()
    assert "Empty recycle bin" in page and "30 days" in page and "bobs.pdf" not in page

    assert post_json(a, f"/documents/{d1}/purge").status_code == 200
    assert db.session.get(Document, d1) is None
    assert post_json(b, f"/documents/{d2}/purge").status_code == 403  # not Bob's

    assert post_json(a, "/recycle-bin/empty").status_code == 200
    assert db.session.get(Document, d2) is None and db.session.get(Folder, f1) is None
    assert db.session.get(Document, bd) is not None  # Bob's bin untouched


def test_admin_settings_show_sign_up_options(client):
    page = client.get("/admin/settings").data.decode()
    assert 'name="self_signup"' in page and 'name="signup_default_role"' in page
    assert "<option selected>Contributor</option>" in page and "<option>Admin</option>" not in page


def test_recycle_bin_keeps_items_at_least_a_month(client):
    form = {k: v for k, v in settings.all_settings().items() if not isinstance(v, (list, dict, bool))}
    form.update(recycle_bin_days="7", allowed_extensions="pdf", blocked_extensions="exe")
    r = post_json(client, "/admin/settings", form)
    assert r.status_code == 400
    assert settings.get("recycle_bin_days") == 30
