"""DMS Permissions Matrix v2 - rules not covered by test_permissions_matrix.py.

Test names start with the PDF section number (1 access model, 2 share levels, 3 role matrix,
5 email archive, 6 deletion and retention, 9 implementation checklist, 11 open items).
"""
from datetime import timedelta

from dms.extensions import db
from dms.models import AccessEntry, ActivityLog, Document, Folder, GmailAccount, Role, utcnow
from dms.services.emails import store_email

from .conftest import PDF, build_email
from .test_permissions_matrix import (  # noqa: F401 - fixtures
    alice, bob, carol, fresh, home, setup_shared_folder, share, shared,
)


def actions_logged():
    db.session.rollback()
    return {(row.category, row.action) for row in db.session.query(ActivityLog)}


def entry_id(owner, kind, item_id, person):
    entries = owner.get(f"/permissions/{kind}/{item_id}").json["entries"]
    return next(e["id"] for e in entries if e["principal_id"] == person.user.id)


# =========================================================================== 1. access model

def test_1_6_hidden_folders_answer_404_not_403(alice, bob):
    folder = alice.folder("Private folder", home(alice))
    assert bob.get(f"/folders/{folder}").status_code == 404
    assert bob.post("/folders", json={"name": "Sneaky", "parent_id": folder}).status_code == 404
    assert bob.upload("sneaky.pdf", PDF, folder_id=folder).status_code == 404
    assert bob.delete(f"/folders/{folder}").status_code == 404


def test_1_3_admin_sees_every_users_folders_and_documents(api, alice):
    folder = alice.folder("Alice only", home(alice))
    doc = alice.doc("alice-only.pdf", PDF, folder_id=folder)
    assert api.get(f"/folders/{folder}").status_code == 200
    assert api.get(f"/documents/{doc}").status_code == 200


# =========================================================================== 2. share levels

def test_2_view_level_gets_no_signed_download_link(alice, bob):
    _, doc = setup_shared_folder(alice, bob, "view")
    assert bob.get(f"/documents/{doc}/download-link").status_code == 403


def test_2_download_level_cannot_copy_into_a_folder_it_cannot_edit(alice, bob):
    folder, doc = setup_shared_folder(alice, bob, "download")
    assert bob.post(f"/documents/{doc}/copy", json={"folder_id": folder}).status_code == 403


# =========================================================================== 3. role permission matrix

def test_3_owner_moves_and_copies_own_items(alice):
    src = alice.folder("Source", home(alice))
    dst = alice.folder("Destination", home(alice))
    doc = alice.doc("move-me.pdf", PDF, folder_id=src)
    assert alice.patch(f"/documents/{doc}", json={"folder_id": dst}).status_code == 200   # move
    assert fresh(Document, doc).folder_id == dst
    assert alice.post(f"/documents/{doc}/copy", json={"folder_id": src}).status_code == 201  # copy


def test_3_owner_changes_a_share_level_and_revokes_it_immediately(alice, bob):
    doc = alice.doc("level.pdf", PDF, folder_id=home(alice))
    shared(alice, "document", doc, bob, "view")
    assert bob.get(f"/documents/{doc}/content").status_code == 403
    shared(alice, "document", doc, bob, "download")                     # change the level
    assert bob.get(f"/documents/{doc}/content").status_code == 200
    bobs = [e for e in alice.get(f"/permissions/document/{doc}").json["entries"]
            if e["principal_type"] == "user" and e["principal_id"] == bob.user.id]
    assert len(bobs) == 1 and bobs[0]["level"] == "download"           # one grant per recipient
    shared(alice, "document", doc, bob, "view")                         # lowering works too
    assert bob.get(f"/documents/{doc}/content").status_code == 403
    eid = entry_id(alice, "document", doc, bob)
    assert alice.delete(f"/permissions/document/{doc}/entries/{eid}").status_code == 200
    assert bob.get(f"/documents/{doc}").status_code == 404              # 9.11: effective at once


def test_3_admin_changes_and_revokes_any_share(api, alice, bob):
    doc = alice.doc("any.pdf", PDF, folder_id=home(alice))
    shared(alice, "document", doc, bob, "view")
    assert share(api, "document", doc, bob, "edit").status_code == 200
    assert bob.patch(f"/documents/{doc}", json={"tags": "admin-granted"}).status_code == 200
    eid = entry_id(api, "document", doc, bob)
    assert api.delete(f"/permissions/document/{doc}/entries/{eid}").status_code == 200
    assert bob.get(f"/documents/{doc}").status_code == 404


def test_3_shared_user_cannot_revoke_or_change_shares(alice, bob, carol):
    doc = alice.doc("theirs.pdf", PDF, folder_id=home(alice))
    shared(alice, "document", doc, bob, "edit")
    shared(alice, "document", doc, carol, "view")
    eid = entry_id(alice, "document", doc, carol)
    assert bob.delete(f"/permissions/document/{doc}/entries/{eid}").status_code == 403
    assert share(bob, "document", doc, carol, "edit").status_code == 403
    assert carol.get(f"/documents/{doc}").status_code == 200


def test_3_contributor_cannot_restore_someone_elses_item_from_the_recycle_bin(alice, bob):
    _, doc = setup_shared_folder(alice, bob, "edit")
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert bob.post(f"/documents/{doc}/restore").status_code in (403, 404)
    assert fresh(Document, doc).deleted_at is not None


def test_3_contributors_purge_their_own_folders_with_everything_inside(alice):
    folder = alice.folder("Mine to purge", home(alice))
    sub = alice.folder("Sub", folder)
    doc = alice.doc("inside.pdf", PDF, folder_id=sub)
    assert alice.delete(f"/folders/{folder}").status_code == 200
    row = next(f for f in alice.get("/recycle-bin").json["folders"] if f["id"] == folder)
    assert row["can_purge"] is True
    assert alice.delete(f"/folders/{folder}/purge").status_code == 200
    assert fresh(Folder, folder) is None and fresh(Folder, sub) is None and fresh(Document, doc) is None


def test_3_a_folder_holding_someone_elses_deleted_item_is_purged_only_by_an_admin(api, alice, bob):
    folder, _ = setup_shared_folder(alice, bob, "edit")
    bobs = bob.doc("bobs.pdf", PDF + b"bob", folder_id=folder)
    assert bob.delete(f"/documents/{bobs}").status_code == 200      # Bob's item, in Bob's recycle bin
    assert alice.delete(f"/folders/{folder}").status_code == 200
    row = next(f for f in alice.get("/recycle-bin").json["folders"] if f["id"] == folder)
    assert row["can_purge"] is False
    assert alice.delete(f"/folders/{folder}/purge").status_code == 403
    r = alice.delete("/recycle-bin")
    assert r.status_code == 200 and r.json["purged_folders"] == 0 and r.json["kept_not_allowed"] == 1
    assert fresh(Folder, folder) is not None and fresh(Document, bobs) is not None
    assert api.delete(f"/folders/{folder}/purge").status_code == 200
    assert fresh(Folder, folder) is None and fresh(Document, bobs) is None


def test_3_admin_only_role_and_user_management(api, alice, bob):
    admin_role = db.session.query(Role).filter_by(name="Admin").one()
    assert alice.post(f"/admin/roles/{admin_role.id}/members", json={"user_ids": [alice.user.id]}).status_code == 403
    assert alice.post(f"/admin/roles/{admin_role.id}/members", json={"user_ids": [bob.user.id]}).status_code == 403
    assert not db.session.get(type(alice.user), alice.user.id).is_admin


# =========================================================================== 5. email archive

def test_5_email_body_and_render_follow_the_share_and_hide_from_admin(api, alice, bob):
    account = GmailAccount(email="alice@gmail.example", user_id=alice.user.id, is_active=True, token_enc="sealed")
    db.session.add(account)
    db.session.commit()
    record, _ = store_email(account, "mrender01", build_email(subject="Salary review"))
    for client in (bob, api):
        assert client.get(f"/emails/{record.id}/body").status_code == 404
        assert client.get(f"/emails/{record.id}/render").status_code == 404
    assert alice.get(f"/emails/{record.id}/render").status_code == 200
    alice.post(f"/emails/{record.id}/shares",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert bob.get(f"/emails/{record.id}/render").status_code == 200
    assert api.get(f"/emails/{record.id}/render").status_code == 404


def test_5_admin_mailbox_list_shows_status_but_no_message_content(api, alice):
    account = GmailAccount(email="alice@gmail.example", user_id=alice.user.id, is_active=True, token_enc="sealed")
    db.session.add(account)
    db.session.commit()
    store_email(account, "msecret01", build_email(subject="Top secret merger"))
    payload = str(api.get("/admin/gmail/accounts").json)
    assert "alice@gmail.example" in payload
    assert "Top secret merger" not in payload and "sealed" not in payload


# =========================================================================== 6. deletion and retention

def test_6_default_recycle_bin_window_is_30_days(alice):
    doc = alice.doc("day29.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    fresh(Document, doc).deleted_at = utcnow() - timedelta(days=29)
    db.session.commit()
    assert alice.post(f"/documents/{doc}/restore").status_code == 200


def test_6_admin_purge_is_blocked_by_legal_hold_on_the_document(api, alice):
    doc = alice.doc("held-purge.pdf", PDF, folder_id=home(alice))
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert api.put(f"/admin/legal-hold/document/{doc}", json={"hold": True}).status_code == 200
    assert api.delete(f"/documents/{doc}/purge").status_code == 403
    assert api.delete("/recycle-bin").status_code == 200
    assert fresh(Document, doc) is not None


def test_6_active_retention_policy_blocks_delete_and_purge(api, alice):
    """PDF 6: 'Legal hold or an active retention policy blocks delete and purge.'"""
    folder = alice.folder("Retained records", home(alice))
    doc = alice.doc("record.pdf", PDF, folder_id=folder)
    assert api.patch(f"/folders/{folder}/retention", json={"keep_years": "7"}).status_code == 200
    assert alice.delete(f"/documents/{doc}").status_code == 403
    assert api.delete(f"/documents/{doc}/purge").status_code == 403


# =========================================================================== 9. implementation checklist

def test_9_12_required_actions_are_audited(api, alice, bob, monkeypatch):
    from dms.gmail import auth as gmail_auth
    monkeypatch.setattr(gmail_auth, "revoke_token", lambda token: True)
    doc = alice.doc("audited.pdf", PDF, folder_id=home(alice))
    alice.get(f"/documents/{doc}")                                                       # view
    alice.get(f"/documents/{doc}/content")                                               # download
    shared(alice, "document", doc, bob, "view")                                          # share
    alice.delete(f"/permissions/document/{doc}/entries/{entry_id(alice, 'document', doc, bob)}")  # unshare
    alice.patch(f"/documents/{doc}", json={"name": "audited-2.pdf"})                     # rename
    alice.delete(f"/documents/{doc}")                                                    # delete
    alice.post(f"/documents/{doc}/restore")                                              # restore
    alice.delete(f"/documents/{doc}")
    api.delete(f"/documents/{doc}/purge")                                                # purge
    reader = db.session.query(Role).filter_by(name="Reader").one()
    api.post(f"/admin/roles/{reader.id}/members", json={"user_ids": [bob.user.id]})      # role change
    db.session.add(GmailAccount(email="alice@gmail.example", user_id=alice.user.id, is_active=True, token_enc="t"))
    db.session.commit()
    alice.post("/gmail/disconnect")                                                      # mailbox unlink

    logged = actions_logged()
    expected = {
        ("document", "viewed"), ("document", "downloaded"), ("security", "shared"), ("security", "permission_removed"),
        ("document", "renamed"), ("document", "trashed"), ("document", "restored_from_trash"),
        ("document", "purged"), ("admin", "role_member_added"), ("gmail", "disconnected"),
    }
    assert expected <= logged, sorted(expected - logged)


def test_9_13_users_cannot_change_their_own_role_as_contributor(alice):
    admin_role = db.session.query(Role).filter_by(name="Admin").one()
    r = alice.patch(f"/admin/users/{alice.user.id}", json={"action": "save", "roles": [admin_role.id]})
    assert r.status_code == 403


def test_9_13_share_target_must_exist(alice):
    doc = alice.doc("nobody.pdf", PDF, folder_id=home(alice))
    r = alice.post(f"/permissions/document/{doc}/share",
                   json={"principal_type": "user", "principal_id": 999999, "level": "view"})
    assert r.status_code in (400, 404)


# =========================================================================== 11. open items (implemented as optional)

def test_11_15_expired_share_gives_no_access(alice, bob):
    doc = alice.doc("expiring.pdf", PDF, folder_id=home(alice))
    shared(alice, "document", doc, bob, "download")
    assert bob.get(f"/documents/{doc}").status_code == 200
    entry = db.session.query(AccessEntry).filter_by(target_type="document", target_id=doc,
                                                    principal_id=bob.user.id).one()
    entry.expires_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert bob.get(f"/documents/{doc}").status_code == 404
    assert bob.get(f"/documents/{doc}/content").status_code == 404

