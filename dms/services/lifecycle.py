"""User lifecycle, ownership reassignment and legal hold (DMS Permissions Matrix v2, sections 1, 6, 7).

* Deactivate: login blocked (every request checks ``is_active``), API tokens revoked, mailbox sync
  stopped (gmail/sync skips inactive users), shares the user *received* removed. Files, folders,
  archived emails and the shares the user granted are kept, and so is their ownership.
* Reactivate: login restored; ownership returns automatically for whatever was not reassigned.
* Reassign ownership: Admin only, one item or in bulk, to an active user or a holding account.
* Legal hold: Admin only; blocks delete, purge and move for everyone (security/policy.py).
"""
from sqlalchemy import select

from ..extensions import db
from ..models import AccessEntry, ApiToken, Document, Folder, User, utcnow
from ..security.permissions import reset_resolver
from ..validation import ValidationError
from .activity import log_activity
from .folders import NotFound


# ------------------------------------------------------------------- deactivate / reactivate

def _owned_ids(user):
    folders = set(db.session.execute(select(Folder.id).where(Folder.owner_id == user.id)).scalars())
    docs = set(db.session.execute(select(Document.id).where(Document.owner_id == user.id)).scalars())
    return folders, docs


def deactivate(user):
    """Apply the side effects of deactivating ``user`` (the caller has set ``is_active = False``).
    Does not commit. Returns a summary for the audit entry."""
    user.deactivated_at = utcnow()
    own_folders, own_docs = _owned_ids(user)
    if user.home_folder_id:
        own_folders.add(user.home_folder_id)
    received = db.session.execute(select(AccessEntry).where(
        AccessEntry.principal_type == "user", AccessEntry.principal_id == user.id)).scalars().all()
    removed = 0
    for entry in received:
        mine = (entry.target_type == "folder" and entry.target_id in own_folders) or \
               (entry.target_type == "document" and entry.target_id in own_docs) or \
               (entry.target_type == "version" and _version_doc(entry.target_id) in own_docs) or \
               entry.target_type == "repository"
        if not mine:
            db.session.delete(entry)
            removed += 1
    tokens = db.session.execute(select(ApiToken).where(
        ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))).scalars().all()
    for token in tokens:
        token.revoked_at = utcnow()
    reset_resolver()
    return {"shares_removed": removed, "tokens_revoked": len(tokens)}


def _version_doc(version_id):
    from ..models import DocumentVersion
    v = db.session.get(DocumentVersion, version_id)
    return v.document_id if v else None


def reactivate(user):
    user.deactivated_at = None
    reset_resolver()


# ------------------------------------------------------------------- ownership

def _item(kind, item_id):
    model = {"folder": Folder, "document": Document}.get(kind)
    if model is None:
        raise ValidationError("Item type must be 'folder' or 'document'.")
    item = db.session.get(model, int(item_id))
    if item is None:
        raise NotFound(f"{kind.capitalize()} not found.")
    return item


def reassign(items, to_user, actor=None):
    """Make ``to_user`` the owner of every (kind, id) in ``items``. The previous owner's own full-control
    grant on the item goes away with the ownership; shares they gave to others are kept."""
    if to_user is None:
        raise NotFound("User not found.")
    changed = []
    for kind, item_id in items:
        item = _item(kind, item_id)
        old = item.owner
        if item.owner_id == to_user.id:
            continue
        if old is not None:
            db.session.query(AccessEntry).filter(
                AccessEntry.target_type == kind, AccessEntry.target_id == item.id,
                AccessEntry.principal_type == "user", AccessEntry.principal_id == old.id,
                AccessEntry.permissions.contains("manage")).delete(synchronize_session=False)
        item.owner_id = to_user.id
        name = item.path if isinstance(item, Folder) else item.name
        log_activity("admin", "ownership_reassigned",
                     f"Ownership of {kind} '{name}' reassigned from "
                     f"{old.username if old else '(nobody)'} to {to_user.username}",
                     item.id, entity_type=kind, entity_name=name,
                     changes={"owner": {"old": old.username if old else None, "new": to_user.username}},
                     details={"from_user_id": old.id if old else None, "to_user_id": to_user.id})
        changed.append({"type": kind, "id": item.id})
    db.session.commit()
    reset_resolver()
    return changed


def owned_by(user):
    """Every folder and document (recycle bin included) owned by ``user``, as (kind, id) pairs."""
    folders, docs = _owned_ids(user)
    if user.home_folder_id in folders:
        folders.discard(user.home_folder_id)  # the My Files folder itself always stays with its user
    return [("folder", fid) for fid in sorted(folders)] + [("document", did) for did in sorted(docs)]


# ------------------------------------------------------------------- legal hold

def set_legal_hold(kind, item_id, hold, reason=""):
    item = _item(kind, item_id)
    old = bool(item.legal_hold)
    item.legal_hold = bool(hold)
    name = item.path if isinstance(item, Folder) else item.name
    if old != item.legal_hold:
        log_activity("admin", "legal_hold_set" if hold else "legal_hold_released",
                     f"Legal hold {'placed on' if hold else 'released from'} {kind} '{name}'"
                     + (f": {reason}" if reason else ""),
                     item.id, entity_type=kind, entity_name=name,
                     changes={"legal_hold": {"old": old, "new": bool(hold)}},
                     details={"reason": reason} if reason else None)
    db.session.commit()
    return item


def holds():
    folders = db.session.execute(select(Folder).where(Folder.legal_hold.is_(True))).scalars().all()
    docs = db.session.execute(select(Document).where(Document.legal_hold.is_(True))).scalars().all()
    return folders, docs
