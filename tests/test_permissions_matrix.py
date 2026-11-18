"""DMS Permissions Matrix v2 - one test (or more) per rule of the specification.

Section numbers in the test names refer to the PDF:
1 access model, 2 share levels, 3 role matrix, 4 items in someone else's folder, 5 email archive,
6 deletion and retention, 7 user lifecycle, 9 implementation checklist.
"""
from datetime import timedelta

import pytest
from sqlalchemy import text

from dms.extensions import db
from dms.models import AccessEntry, ActivityLog, ApiToken, Document, Folder, GmailAccount, Role, User, utcnow
from dms.security import policy
from dms.security.permissions import Resolver
from dms.services.emails import store_email
from dms.services.folders import ensure_home

from .conftest import PDF, Api, build_email, make_user


# --------------------------------------------------------------------------- fixtures and helpers

@pytest.fixture
def alice(app, admin):
    return Api(app, make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


@pytest.fixture
def carol(app, admin):
    return Api(app, make_user("carol", ("Contributor",), email="carol@example.com", display_name="Carol"))


def home(client):
    folder = ensure_home(client.user)
    db.session.commit()
    return folder.id


def share(owner, kind, item_id, person, level, **extra):
    return owner.post(f"/permissions/{kind}/{item_id}/share",
                      json={"principal_type": "user", "principal_id": person.user.id, "level": level, **extra})


def shared(owner, kind, item_id, person, level):
    r = share(owner, kind, item_id, person, level)
    assert r.status_code == 200, r.json
    return r


def fresh(model, item_id):
    db.session.rollback()
    return db.session.get(model, item_id)


def audit(action=None, **filters):
    db.session.rollback()
    q = db.session.query(ActivityLog)
    if action:
        q = q.filter_by(action=action)
    return q.filter_by(**filters).all()


def setup_shared_folder(alice, bob, level):
    """Alice's project folder (with a document and a subfolder) shared with Bob at ``level``."""
    folder = alice.folder("Project", home(alice))
    doc = alice.doc("plan.pdf", PDF, folder_id=folder)
    assert alice.version(doc, "plan.pdf", PDF + b"v2").status_code in (200, 201)
    shared(alice, "folder", folder, bob, level)
    return folder, doc


def deactivate(api, user):
    r = api.patch(f"/admin/users/{user.id}", json={"action": "save", "is_active": False})
    assert r.status_code == 200, r.json


def reactivate(api, user):
    r = api.patch(f"/admin/users/{user.id}", json={"action": "save", "is_active": True})
    assert r.status_code == 200, r.json


# =========================================================================== 1. access model

def test_1_1_inactive_user_is_denied_everything(api, alice):
    doc = alice.doc("mine.pdf", PDF, folder_id=home(alice))
    deactivate(api, alice.user)
    assert alice.get(f"/documents/{doc}").status_code == 401        # API token no longer accepted
    assert alice.get("/folders/root").status_code == 401
    user = fresh(User, alice.user.id)
    assert policy.can(user, "item.view", fresh(Document, doc)) is False   # even its own items


def test_1_2_legal_hold_blocks_delete_purge_and_move_even_for_admin(api, alice):
    folder = alice.folder("Case files", home(alice))
    doc = alice.doc("evidence.pdf", PDF, folder_id=folder)
    other = alice.folder("Elsewhere", home(alice))
    assert api.put(f"/admin/legal-hold/document/{doc}", json={"hold": True, "reason": "Case 42"}).status_code == 200

    for client in (alice, api):
        assert client.delete(f"/documents/{doc}").status_code == 403
        assert client.patch(f"/documents/{doc}", json={"folder_id": other}).status_code == 403
        # A folder holding a document under hold cannot be deleted or moved either.
        assert client.delete(f"/folders/{folder}").status_code == 403
        assert client.patch(f"/folders/{folder}", json={"parent_id": other}).status_code == 403
    # Everything else still works.
    assert alice.get(f"/documents/{doc}/content").status_code == 200
    assert alice.patch(f"/documents/{doc}", json={"tags": "case"}).status_code == 200
    assert "legal_hold" in alice.get(f"/documents/{doc}").json["document"] and \
        alice.get(f"/documents/{doc}").json["document"]["legal_hold"] is True

    # Released: delete works again, and only Admin may purge.
    assert api.put(f"/admin/legal-hold/document/{doc}", json={"hold": False}).status_code == 200
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert alice.delete(f"/documents/{doc}/purge").status_code == 403
    assert api.delete(f"/documents/{doc}/purge").status_code == 200
    assert {e.action for e in audit(entity_type="document", entity_id=doc)} >= {"legal_hold_set", "legal_hold_released"}


def test_1_2_legal_hold_on_a_folder_covers_what_is_inside_and_blocks_purge(api, alice):
    folder = alice.folder("Held", home(alice))
    doc = alice.doc("inside.pdf", PDF, folder_id=folder)
    assert alice.delete(f"/documents/{doc}").status_code == 200       # in the recycle bin first
    assert api.put(f"/admin/legal-hold/folder/{folder}", json={"hold": True}).status_code == 200
    assert api.delete(f"/documents/{doc}/purge").status_code == 403    # purge blocked by the folder's hold
    holds = api.get("/admin/legal-holds").json
    assert [f["id"] for f in holds["folders"]] == [folder]


def test_1_3_admin_is_allowed_everything_and_every_override_is_audited(api, alice):
    doc = alice.doc("private.pdf", PDF, folder_id=home(alice))
    assert api.get(f"/documents/{doc}/content").status_code == 200
    assert api.patch(f"/documents/{doc}", json={"name": "renamed.pdf"}).status_code == 200
    rows = audit("override", user_id=api.user.id)
    assert {r.details["permission"] for r in rows} >= {"item.download", "item.rename"}


def test_1_4_owner_may_do_all_owner_actions(alice):
    folder = alice.folder("Mine", home(alice))
    doc = alice.doc("a.pdf", PDF, folder_id=folder)
    actions = set(alice.get(f"/documents/{doc}").json["document"]["actions"])
    assert actions >= {"item.view", "item.download", "item.version.create", "item.metadata.edit",
                       "item.version.restore", "item.rename", "item.move", "item.copy", "item.delete",
                       "item.restore", "item.share"}
    assert "item.purge" not in actions                                # purge is Admin only


def test_1_6_items_the_user_cannot_see_answer_404_not_403(alice, bob):
    doc = alice.doc("secret.pdf", PDF, folder_id=home(alice))
    for method, path in (("get", f"/documents/{doc}"), ("get", f"/documents/{doc}/content"),
                         ("delete", f"/documents/{doc}"), ("patch", f"/documents/{doc}")):
        r = getattr(bob, method)(path, json={"name": "x.pdf"}) if method == "patch" else getattr(bob, method)(path)
        assert r.status_code == 404, (method, path, r.status_code)


def test_1_ownership_is_the_creator(alice):
    folder = alice.folder("Created", home(alice))
    doc = alice.doc("up.pdf", PDF, folder_id=folder)
    assert fresh(Folder, folder).owner_id == alice.user.id
    assert fresh(Document, doc).owner_id == alice.user.id
    assert alice.get(f"/documents/{doc}").json["document"]["owner"] == "alice"


# =========================================================================== 2. share levels

def test_2_view_level(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "view")
    assert bob.get(f"/documents/{doc}").status_code == 200                          # metadata, history
    assert len(bob.get(f"/documents/{doc}").json["versions"]) == 2
    assert bob.get(f"/documents/{doc}/preview", headers={"Sec-Fetch-Dest": "iframe"}).status_code == 200
    assert bob.get(f"/documents/{doc}/content").status_code == 403                  # no download
    assert bob.get(f"/documents/{doc}/content?version=1").status_code == 403
    assert bob.version(doc, "plan.pdf", PDF + b"bob").status_code == 403            # no new version
    assert bob.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 403    # no metadata edit
    assert bob.post("/folders", json={"name": "Sub", "parent_id": folder}).status_code == 403
    assert bob.upload("b.pdf", PDF, folder_id=folder).status_code == 403


def test_2_download_level(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "download")
    assert bob.get(f"/documents/{doc}/content").status_code == 200
    assert bob.get(f"/documents/{doc}/content?version=1").status_code == 200        # any version
    assert bob.version(doc, "plan.pdf", PDF + b"bob").status_code == 403
    assert bob.patch(f"/documents/{doc}", json={"tags": "x"}).status_code == 403
    assert bob.upload("b.pdf", PDF, folder_id=folder).status_code == 403


def test_2_edit_level(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "edit")
    assert bob.get(f"/documents/{doc}/content?version=1").status_code == 200
    assert bob.version(doc, "plan.pdf", PDF + b"bob v3").status_code in (200, 201)  # new version
    assert bob.patch(f"/documents/{doc}", json={"tags": "reviewed"}).status_code == 200
    assert bob.post("/folders", json={"name": "Bob sub", "parent_id": folder}).status_code == 201
    assert bob.upload("bob.pdf", PDF, folder_id=folder).status_code == 201


def test_2_edit_level_never_allows_rename_move_delete_restore_version_or_reshare(alice, bob, carol):
    folder, doc = setup_shared_folder(alice, bob, "edit")
    other = bob.folder("Bob place", home(bob))
    assert bob.patch(f"/documents/{doc}", json={"name": "renamed.pdf"}).status_code == 403
    assert bob.patch(f"/documents/{doc}", json={"folder_id": other}).status_code == 403
    assert bob.post(f"/documents/{doc}/copy", json={"folder_id": other}).status_code == 403
    assert bob.delete(f"/documents/{doc}").status_code == 403
    assert bob.post(f"/documents/{doc}/versions/1/restore", json={}).status_code == 403
    assert share(bob, "document", doc, carol, "view").status_code == 403
    assert bob.patch(f"/folders/{folder}", json={"name": "Renamed"}).status_code == 403
    assert bob.delete(f"/folders/{folder}").status_code == 403
    assert bob.get(f"/permissions/document/{doc}").json["entries"] == []           # cannot see the ACL


def test_2_share_dialog_offers_exactly_view_download_edit(alice, bob):
    doc = alice.doc("d.pdf", PDF, folder_id=home(alice))
    assert [lvl["key"] for lvl in alice.get("/directory").json["share_levels"]] == ["view", "download", "edit"]
    assert share(alice, "document", doc, bob, "full").status_code == 400
    assert share(alice, "document", doc, bob, "upload").status_code == 400


def test_2_folder_share_applies_to_items_added_later(alice, bob):
    folder = alice.folder("Later", home(alice))
    shared(alice, "folder", folder, bob, "download")
    doc = alice.doc("added-later.pdf", PDF, folder_id=folder)
    sub = alice.folder("Sub later", folder)
    deeper = alice.doc("deeper.pdf", PDF, folder_id=sub)
    assert bob.get(f"/documents/{doc}/content").status_code == 200
    assert bob.get(f"/documents/{deeper}/content").status_code == 200


def test_2_legacy_full_control_grant_to_a_non_owner_now_works_as_edit(alice, bob, carol):
    doc = alice.doc("legacy.pdf", PDF, folder_id=home(alice))
    db.session.add(AccessEntry(target_type="document", target_id=doc, principal_type="user",
                               principal_id=bob.user.id, permissions="view,upload,download,edit,delete,share,manage"))
    db.session.commit()
    assert bob.patch(f"/documents/{doc}", json={"tags": "ok"}).status_code == 200
    assert bob.delete(f"/documents/{doc}").status_code == 403
    assert share(bob, "document", doc, carol, "view").status_code == 403
    assert set(bob.get(f"/documents/{doc}").json["document"]["permissions"]) == {"view", "download", "upload", "edit"}


# =========================================================================== 3. role permission matrix

def test_3_contributor_creates_folders_and_uploads_in_own_space(alice):
    folder = alice.folder("My project", home(alice))
    assert alice.upload("x.pdf", PDF, folder_id=folder).status_code == 201


def test_3_create_folder_needs_edit_level_on_parent(alice, bob):
    folder = alice.folder("Parent", home(alice))
    shared(alice, "folder", folder, bob, "download")
    assert bob.post("/folders", json={"name": "Nope", "parent_id": folder}).status_code == 403
    shared(alice, "folder", folder, bob, "edit")
    assert bob.post("/folders", json={"name": "Yes", "parent_id": folder}).status_code == 201


def test_3_restore_older_version_owner_and_admin_only(api, alice, bob):
    _, doc = setup_shared_folder(alice, bob, "edit")
    assert bob.post(f"/documents/{doc}/versions/1/restore", json={}).status_code == 403
    assert alice.post(f"/documents/{doc}/versions/1/restore", json={}).status_code in (200, 201)
    assert api.post(f"/documents/{doc}/versions/2/restore", json={}).status_code in (200, 201)


def test_3_only_admin_purges_and_empties_the_recycle_bin(api, alice):
    doc = alice.doc("trash.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    bin_ = alice.get("/recycle-bin").json
    row = next(d for d in bin_["documents"] if d["id"] == doc)
    assert row["can_restore"] is True and row["can_purge"] is False and bin_["can_purge"] is False
    assert alice.delete(f"/documents/{doc}/purge").status_code == 403
    assert alice.delete("/recycle-bin").status_code == 403
    assert api.delete("/recycle-bin").json["purged_documents"] == 1
    assert fresh(Document, doc) is None


def test_3_admin_only_system_actions(api, alice, bob):
    folder = alice.folder("F", home(alice))
    contributor_calls = [
        alice.get("/admin/users"),
        alice.post("/admin/ownership/reassign", json={"to_user_id": alice.user.id,
                                                      "items": [{"type": "folder", "id": folder}]}),
        alice.put(f"/admin/legal-hold/folder/{folder}", json={"hold": True}),
        alice.get("/admin/legal-holds"),
        alice.patch(f"/folders/{folder}/retention", json={"keep_last": "2"}),
        alice.get("/admin/settings"),
        alice.patch(f"/admin/users/{bob.user.id}", json={"action": "save", "is_active": False}),
    ]
    assert [r.status_code for r in contributor_calls] == [403] * len(contributor_calls)
    assert api.patch(f"/folders/{folder}/retention", json={"keep_last": "2"}).status_code == 200


def test_3_view_audit_logs_admin_sees_all_contributor_only_own(api, alice, bob):
    alice.folder("Alice audit", home(alice))
    bob.folder("Bob audit", home(bob))
    alice_events = alice.get("/activity", query_string={"per_page": 100}).json["events"]
    assert all(e["user_id"] == alice.user.id for e in alice_events)
    admin_events = api.get("/activity", query_string={"per_page": 100}).json["events"]
    assert {alice.user.id, bob.user.id} <= {e["user_id"] for e in admin_events}
    only_bob = api.get("/activity", query_string={"user_id": bob.user.id}).json["events"]
    assert only_bob and all(e["user_id"] == bob.user.id for e in only_bob)


# =========================================================================== 4. items in someone else's folder

def test_4_upload_into_a_shared_folder_belongs_to_the_uploader(alice, bob):
    folder, _ = setup_shared_folder(alice, bob, "edit")
    r = bob.upload("bobs.pdf", PDF, folder_id=folder)
    assert r.status_code == 201
    doc = r.json["document"]["id"]
    assert fresh(Document, doc).owner_id == bob.user.id
    assert bob.patch(f"/documents/{doc}", json={"name": "bobs-renamed.pdf"}).status_code == 200
    assert bob.delete(f"/documents/{doc}").status_code == 200


def test_4_folder_owner_gets_view_and_download_on_others_uploads_but_not_rename_or_delete(alice, bob):
    folder, _ = setup_shared_folder(alice, bob, "edit")
    doc = bob.upload("bobs.pdf", PDF, folder_id=folder).json["document"]["id"]
    assert alice.get(f"/documents/{doc}").status_code == 200
    assert alice.get(f"/documents/{doc}/content").status_code == 200
    assert alice.patch(f"/documents/{doc}", json={"name": "taken.pdf"}).status_code == 403
    assert alice.delete(f"/documents/{doc}").status_code == 403
    assert alice.version(doc, "bobs.pdf", PDF + b"alice").status_code == 403
    assert set(alice.get(f"/documents/{doc}").json["document"]["permissions"]) == {"view", "download"}


def test_4_folder_owner_cannot_delete_a_folder_holding_someone_elses_items(api, alice, bob):
    folder, _ = setup_shared_folder(alice, bob, "edit")
    bob_doc = bob.upload("bobs.pdf", PDF, folder_id=folder).json["document"]["id"]
    r = alice.delete(f"/folders/{folder}")
    assert r.status_code == 403 and "belong to other people" in r.json["error"]
    # Once Bob removes his item, Alice may delete the folder.
    assert bob.delete(f"/documents/{bob_doc}").status_code == 200
    assert alice.delete(f"/folders/{folder}").status_code == 200


def test_4_admin_may_delete_a_folder_with_other_peoples_items(api, alice, bob):
    folder, _ = setup_shared_folder(alice, bob, "edit")
    bob.upload("bobs.pdf", PDF, folder_id=folder)
    assert api.delete(f"/folders/{folder}").status_code == 200


# =========================================================================== 5. email archive

@pytest.fixture
def alice_email(app, alice):
    account = GmailAccount(email="alice@gmail.example", user_id=alice.user.id, is_active=True, token_enc="sealed")
    db.session.add(account)
    db.session.commit()
    record, _ = store_email(account, "m1", build_email(subject="Board minutes"))
    return record


def test_5_emails_are_private_to_the_owner_including_from_admin(api, alice, bob, alice_email):
    assert alice.get(f"/emails/{alice_email.id}").status_code == 200
    assert alice.get(f"/emails/{alice_email.id}").json["email"]["is_owner"] is True
    for client in (bob, api):
        assert client.get(f"/emails/{alice_email.id}").status_code == 404
        assert client.get(f"/emails/{alice_email.id}/eml").status_code == 404
        assert client.get("/emails/shared").json["emails"] == []


def test_5_emails_are_shared_through_the_normal_share_levels(api, alice, bob, alice_email):
    r = alice.post(f"/emails/{alice_email.id}/shares",
                   json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert r.status_code == 201, r.json
    assert bob.get(f"/emails/{alice_email.id}").status_code == 200
    assert bob.get(f"/emails/{alice_email.id}/body").status_code == 200
    assert bob.get(f"/emails/{alice_email.id}/eml").status_code == 403                 # view only
    assert [e["id"] for e in bob.get("/emails/shared").json["emails"]] == [alice_email.id]
    # Bob cannot reshare; the administrator still has no access.
    assert bob.post(f"/emails/{alice_email.id}/shares",
                    json={"principal_type": "user", "principal_id": api.user.id, "level": "view"}).status_code == 403
    assert api.get(f"/emails/{alice_email.id}").status_code == 404

    alice.post(f"/emails/{alice_email.id}/shares",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "download"})
    assert bob.get(f"/emails/{alice_email.id}/eml").status_code == 200
    share_id = alice.get(f"/emails/{alice_email.id}/shares").json["shares"][0]["id"]
    assert alice.delete(f"/emails/{alice_email.id}/shares/{share_id}").status_code == 200
    assert bob.get(f"/emails/{alice_email.id}").status_code == 404


def test_5_admin_sees_mailbox_status_and_can_disable_a_link_without_content(api, alice, alice_email, monkeypatch):
    from dms.gmail import auth as gmail_auth
    revoked = []
    monkeypatch.setattr(gmail_auth, "revoke_token", lambda token: revoked.append(token) or True)
    accounts = api.get("/admin/gmail/accounts").json["accounts"]
    assert accounts[0]["email"] == "alice@gmail.example" and "subject" not in str(accounts)
    assert alice.post(f"/admin/gmail/accounts/{accounts[0]['id']}/disconnect").status_code == 403
    assert api.post(f"/admin/gmail/accounts/{accounts[0]['id']}/disconnect").status_code == 200
    account = fresh(GmailAccount, accounts[0]["id"])
    assert account.is_active is False and account.token_enc is None
    assert revoked == ["sealed"]                                        # token revoked at Google
    assert alice.get(f"/emails/{alice_email.id}").status_code == 200    # archived emails remain


def test_5_unlinking_own_mailbox_revokes_the_token_and_keeps_emails(alice, alice_email, monkeypatch):
    from dms.gmail import auth as gmail_auth
    revoked = []
    monkeypatch.setattr(gmail_auth, "revoke_token", lambda token: revoked.append(token) or True)
    assert alice.post("/gmail/disconnect").status_code == 200
    assert revoked == ["sealed"]
    assert alice.get(f"/emails/{alice_email.id}").status_code == 200


def test_5_email_search_includes_emails_shared_with_the_user(api, alice, bob, alice_email):
    def titles(client):
        return [r["title"] for r in client.get("/search", query_string={"q": "Board"}).json["results"]]
    assert titles(bob) == []
    alice.post(f"/emails/{alice_email.id}/shares",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert any("Board minutes" in t for t in titles(bob))
    assert titles(api) == []                                             # admin: no email content


# =========================================================================== 6. deletion and retention

def test_6_delete_is_soft_and_owner_restores_within_the_retention_window(alice):
    doc = alice.doc("soft.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert fresh(Document, doc).deleted_at is not None
    assert alice.post(f"/documents/{doc}/restore").status_code == 200
    assert fresh(Document, doc).deleted_at is None


def test_6_owner_cannot_restore_after_the_window_admin_can(api, alice):
    doc = alice.doc("late.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    item = fresh(Document, doc)
    item.deleted_at = utcnow() - timedelta(days=31)                     # default window: 30 days
    db.session.commit()
    r = alice.post(f"/documents/{doc}/restore")
    assert r.status_code == 403 and "retention period" in r.json["error"]
    assert api.post(f"/documents/{doc}/restore").status_code == 200


def test_6_shared_users_cannot_delete_or_restore(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "edit")
    assert bob.delete(f"/documents/{doc}").status_code == 403
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert bob.post(f"/documents/{doc}/restore").status_code in (403, 404)
    assert all(d["id"] != doc for d in bob.get("/recycle-bin").json["documents"])


def test_6_deleting_a_folder_soft_deletes_its_contents_and_restore_brings_the_tree_back(alice):
    top = alice.folder("Tree", home(alice))
    sub = alice.folder("Branch", top)
    leaf = alice.doc("leaf.pdf", PDF, folder_id=sub)
    assert alice.delete(f"/folders/{top}").status_code == 200
    assert fresh(Document, leaf).deleted_at is not None and fresh(Folder, sub).deleted_at is not None
    assert alice.post(f"/folders/{top}/restore").status_code == 200
    assert fresh(Document, leaf).deleted_at is None and fresh(Folder, sub).deleted_at is None


def test_6_retention_job_never_purges_items_under_legal_hold(api, alice):
    from dms.services.retention import purge_expired_recycle_bin
    held = alice.doc("held.pdf", PDF, folder_id=home(alice))
    free = alice.doc("free.pdf", PDF, folder_id=home(alice))
    for doc in (held, free):
        assert alice.delete(f"/documents/{doc}").status_code == 200
    assert api.put(f"/admin/legal-hold/document/{held}", json={"hold": True}).status_code == 200
    for doc in (held, free):
        fresh(Document, doc).deleted_at = utcnow() - timedelta(days=40)
    db.session.commit()
    purge_expired_recycle_bin()
    assert fresh(Document, free) is None
    assert fresh(Document, held) is not None


# =========================================================================== 7. user lifecycle

def test_7_deactivate_blocks_login_revokes_tokens_and_removes_received_shares(api, alice, bob):
    alice_doc = alice.doc("alices.pdf", PDF, folder_id=home(alice))
    bob_doc = bob.doc("bobs.pdf", PDF, folder_id=home(bob))
    shared(alice, "document", alice_doc, bob, "view")      # Bob received a share
    shared(bob, "document", bob_doc, alice, "view")        # Bob granted a share
    deactivate(api, bob.user)

    assert bob.get("/me").status_code == 401
    assert all(t.revoked_at is not None for t in db.session.query(ApiToken).filter_by(user_id=bob.user.id))
    received = db.session.query(AccessEntry).filter_by(target_type="document", target_id=alice_doc,
                                                       principal_type="user", principal_id=bob.user.id).all()
    assert received == []
    # Files and the shares Bob granted are retained; ownership stays with Bob.
    assert fresh(Document, bob_doc).owner_id == bob.user.id
    assert alice.get(f"/documents/{bob_doc}").status_code == 200
    assert fresh(User, bob.user.id).deactivated_at is not None
    assert audit("user_deactivated", entity_id=bob.user.id)


def test_7_deactivated_users_mailbox_is_not_synced(api, bob):
    from dms.gmail.sync import sync_account
    account = GmailAccount(email="bob@gmail.example", user_id=bob.user.id, is_active=True, token_enc="t")
    db.session.add(account)
    db.session.commit()
    deactivate(api, bob.user)
    assert sync_account(account.id).status == "user_inactive"


def test_7_reactivate_restores_login_and_original_ownership(api, alice):
    doc = alice.doc("keep.pdf", PDF, folder_id=home(alice))
    deactivate(api, alice.user)
    reactivate(api, alice.user)
    again = Api(alice.client.application, fresh(User, alice.user.id))   # old tokens were revoked
    assert again.get(f"/documents/{doc}").status_code == 200
    assert fresh(Document, doc).owner_id == alice.user.id
    assert fresh(User, alice.user.id).deactivated_at is None


def test_7_admin_reassigns_ownership_single_item_and_in_bulk(api, alice, bob):
    one = alice.doc("one.pdf", PDF, folder_id=home(alice))
    folder = alice.folder("Projects", home(alice))
    two = alice.doc("two.pdf", PDF, folder_id=folder)
    r = api.post("/admin/ownership/reassign", json={"to_user_id": bob.user.id, "items": [{"type": "document", "id": one}]})
    assert r.status_code == 200 and r.json["count"] == 1
    assert fresh(Document, one).owner_id == bob.user.id
    assert bob.patch(f"/documents/{one}", json={"name": "now-bobs.pdf"}).status_code == 200
    assert alice.delete(f"/documents/{one}").status_code == 403          # no longer the owner

    deactivate(api, alice.user)
    r = api.post("/admin/ownership/reassign", json={"to_user_id": bob.user.id, "from_user_id": alice.user.id})
    assert r.status_code == 200
    assert fresh(Folder, folder).owner_id == bob.user.id and fresh(Document, two).owner_id == bob.user.id
    assert fresh(Folder, home(alice)).owner_id == alice.user.id          # My Files itself stays
    assert audit("ownership_reassigned")


def test_7_role_change_takes_effect_immediately_and_keeps_ownership(api, alice):
    doc = alice.doc("mine.pdf", PDF, folder_id=home(alice))
    admin_role = db.session.query(Role).filter_by(name="Admin").one()
    assert api.post(f"/admin/roles/{admin_role.id}/members", json={"user_ids": [alice.user.id]}).status_code == 200
    assert alice.get("/admin/users").status_code == 200                  # immediately an administrator
    assert fresh(Document, doc).owner_id == alice.user.id


def test_7_last_admin_cannot_be_demoted_or_deactivated(api, admin):
    r = api.patch(f"/admin/users/{admin.id}", json={"action": "save", "is_active": False})
    assert r.status_code == 400
    role = db.session.query(Role).filter_by(name="Admin").one()
    assert api.delete(f"/admin/roles/{role.id}/members/{admin.id}").status_code == 400


# =========================================================================== 9. implementation checklist

def test_9_7_server_enforces_even_when_the_ui_is_bypassed(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "view")
    assert "item.delete" not in bob.get(f"/documents/{doc}").json["document"]["actions"]
    assert bob.delete(f"/documents/{doc}").status_code == 403          # direct API call still refused


def test_9_8_one_can_function_backs_every_decision(app, alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "download")
    item = db.session.get(Document, doc)
    bob_user = db.session.get(User, bob.user.id)
    assert policy.can(bob_user, "item.download", item) is True
    assert policy.can(bob_user, "item.version.create", item) is False
    assert policy.can(bob_user, "item.delete", item) is False
    with pytest.raises(ValueError):
        policy.can(bob_user, "item.unknown", item)


def test_9_9_search_is_filtered_by_owner_or_share_grant(api, alice, bob):
    alice.doc("quarterly-forecast.txt", b"forecast numbers", folder_id=home(alice))

    def titles(client):
        return [r["title"] for r in client.get("/search", query_string={"q": "forecast"}).json["results"]]
    assert titles(alice) == ["quarterly-forecast.txt"]
    assert titles(bob) == []
    assert titles(api) == ["quarterly-forecast.txt"]                    # Admin: all items


def test_9_10_signed_download_links_are_short_lived_and_rechecked(api, alice, bob):
    doc = alice.doc("linked.pdf", PDF, folder_id=home(alice))
    assert bob.get(f"/documents/{doc}/download-link").status_code == 404   # no link without access
    shared(alice, "document", doc, bob, "download")
    link = bob.get(f"/documents/{doc}/download-link").json
    assert link["expires_in"] == 60
    anonymous = bob.client.application.test_client()
    url = link["url"].replace("/api/v1", "")
    assert anonymous.get("/api/v1" + url).status_code == 200
    assert anonymous.get("/api/v1" + url + "x").status_code == 404        # tampered
    # Access removed after the link was issued: the link stops working.
    entry = db.session.query(AccessEntry).filter_by(target_type="document", target_id=doc,
                                                    principal_id=bob.user.id).one()
    db.session.delete(entry)
    db.session.commit()
    assert anonymous.get("/api/v1" + url).status_code == 404


def test_9_11_moving_an_item_re_evaluates_inherited_grants(alice, bob):
    shared_folder = alice.folder("Shared with Bob", home(alice))
    private = alice.folder("Private", home(alice))
    shared(alice, "folder", shared_folder, bob, "view")
    doc = alice.doc("moving.pdf", PDF, folder_id=shared_folder)
    assert bob.get(f"/documents/{doc}").status_code == 200
    assert alice.patch(f"/documents/{doc}", json={"folder_id": private}).status_code == 200
    assert bob.get(f"/documents/{doc}").status_code == 404


def test_9_13_users_cannot_change_their_own_role(api, admin):
    other = make_user("second-admin", ("Admin",))
    role = db.session.query(Role).filter_by(name="Reader").one()
    r = api.patch(f"/admin/users/{admin.id}", json={"action": "save", "roles": [role.id]})
    assert r.status_code == 400 and "own role" in r.json["error"]
    assert api.post(f"/admin/roles/{role.id}/members", json={"user_ids": [admin.id]}).status_code == 400
    assert other.is_admin


def test_9_13_share_only_with_active_users(api, alice, bob):
    doc = alice.doc("x.pdf", PDF, folder_id=home(alice))
    deactivate(api, bob.user)
    r = share(alice, "document", doc, bob, "view")
    assert r.status_code == 400 and "active" in r.json["error"]


def test_9_13_cannot_share_items_they_do_not_own(alice, bob, carol):
    folder, doc = setup_shared_folder(alice, bob, "edit")
    assert share(bob, "document", doc, carol, "view").status_code == 403
    assert share(bob, "folder", folder, carol, "view").status_code == 403
    assert bob.post(f"/permissions/document/{doc}/grant",
                    json={"principal_type": "user", "principal_id": carol.user.id,
                          "permissions": ["view"]}).status_code == 403


# =========================================================================== upgrade of existing data

def test_upgrade_sets_owner_from_creator_once(app, alice):
    from dms.migrate import _owners
    from dms.services import settings
    folder = alice.folder("Old", home(alice))
    db.session.execute(text("UPDATE folders SET owner_id = NULL WHERE id = :id"), {"id": folder})
    settings.set_value("owner_backfill_done", False)
    db.session.commit()
    _owners()
    assert fresh(Folder, folder).owner_id == alice.user.id
    assert settings.get("owner_backfill_done") is True


def test_resolver_ignores_admin_when_asked(app, api, alice):
    doc = alice.doc("p.pdf", PDF, folder_id=home(alice))
    admin_user = db.session.get(User, api.user.id)
    item = db.session.get(Document, doc)
    assert "view" in Resolver(admin_user).document(item)
    assert Resolver(admin_user, honour_admin=False).document(item) == set()
