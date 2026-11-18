"""One place for every access decision: ``can(user, action, item)`` (DMS Permissions Matrix v2).

Every request is evaluated in this order; the first matching rule wins and anything unmatched is
denied (section 1):

1. User inactive: deny everything.
2. Item under legal hold (on the item, a folder above it or, for a folder, anything inside it):
   block delete, purge and move for everyone, administrators included.
3. Administrator: allow, and write an audit entry when the administrator would not have been allowed
   as an ordinary user ("admin override").
4. Owner: every owner action. Restore from the recycle bin only within the retention window; a folder
   can be deleted (or moved) only when it holds none of someone else's items. Purge is Admin only.
5. Share grant on the item or a folder above it: the actions of that level (section 2).
6. Otherwise deny; endpoints answer 404 (not 403) when the user may not even see the item.

Permission keys (section 8) are the ``action`` values. ``item.copy`` is the copy half of the PDF's
``item.rename / item.move`` row: owner/Admin only, but not blocked by legal hold.
"""
from datetime import timedelta

from flask import abort, g, has_request_context
from sqlalchemy import select

from ..extensions import db
from ..models import Document, DocumentVersion, Folder, utcnow

# Actions a non-owner may perform through a share, with the permission the share must include.
SHARE_ACTIONS = {
    "item.view": "view",
    "item.download": "download",
    "item.version.create": "edit",
    "item.metadata.edit": "edit",
    "folder.create_child": "upload",   # "edit" implies "upload"
    "folder.upload": "upload",
}
# Owner (or Admin) only.
OWNER_ACTIONS = {
    "item.version.restore", "item.rename", "item.move", "item.copy", "item.delete", "item.restore",
    "item.share",
}
ADMIN_ONLY_ITEM_ACTIONS = {"item.purge"}
HOLD_BLOCKED = {"item.delete", "item.purge", "item.move"}
ITEM_ACTIONS = set(SHARE_ACTIONS) | OWNER_ACTIONS | ADMIN_ONLY_ITEM_ACTIONS

# System actions: who may perform them, independent of any item.
EVERY_USER_ACTIONS = {"email.link", "email.unlink", "email.archive"}
ADMIN_ACTIONS = {"email.manage_any", "admin.users", "admin.ownership", "admin.policy", "admin.audit"}

MESSAGES = {
    "hold": "This item is under legal hold: it cannot be deleted, moved or permanently deleted.",
    "owner": "Only the owner or an administrator can do this.",
    "admin": "Only an administrator can do this.",
    "foreign": "This folder holds items that belong to other people, so it cannot be {verb}. "
               "Ask them (or an administrator) to move their items out first.",
    "window": "The recycle bin retention period for this item has ended; ask an administrator.",
    "level": "Your access level does not allow this.",
    "inactive": "Your account is not active.",
}


# ------------------------------------------------------------------- helpers

def _folder_chain(folder):
    node, seen = folder, set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        yield node
        node = node.parent


def _subtree_ids(folder):
    """Ids of ``folder`` and every folder below it (deleted ones included)."""
    from .permissions import resolver
    memo = resolver()._memo if has_request_context() else {}
    children = memo.get("policy_children")
    if children is None:
        children = {}
        for fid, pid in db.session.execute(select(Folder.id, Folder.parent_id)).all():
            children.setdefault(pid, []).append(fid)
        memo["policy_children"] = children
    out, stack = [], [folder.id]
    while stack:
        fid = stack.pop()
        if fid in out:
            continue
        out.append(fid)
        stack.extend(children.get(fid, []))
    return out


def under_hold(item):
    """True when legal hold applies to ``item``: on the item itself, on a folder above it, or (for a
    folder) on any folder or document inside it."""
    if item is None:
        return False
    if isinstance(item, DocumentVersion):
        item = item.document
    if isinstance(item, Document):
        return bool(item.legal_hold) or any(f.legal_hold for f in _folder_chain(item.folder))
    if any(f.legal_hold for f in _folder_chain(item)):
        return True
    ids = _subtree_ids(item)
    if db.session.execute(select(Folder.id).where(Folder.id.in_(ids), Folder.legal_hold.is_(True))
                          .limit(1)).first():
        return True
    return db.session.execute(select(Document.id).where(Document.folder_id.in_(ids),
                                                        Document.legal_hold.is_(True)).limit(1)).first() is not None


def foreign_items(folder, uid):
    """Live folders and documents inside ``folder`` (itself included) that ``uid`` does not own."""
    ids = _subtree_ids(folder)
    folders = db.session.execute(select(Folder).where(
        Folder.id.in_(ids), Folder.deleted_at.is_(None),
        (Folder.owner_id.is_(None)) | (Folder.owner_id != uid))).scalars().all()
    docs = db.session.execute(select(Document).where(
        Document.folder_id.in_(ids), Document.deleted_at.is_(None),
        (Document.owner_id.is_(None)) | (Document.owner_id != uid))).scalars().all()
    return folders + docs


def within_retention(item):
    """True while a deleted item is still inside the recycle-bin retention window."""
    from ..services import settings
    if item.deleted_at is None:
        return True
    return utcnow() < item.deleted_at + timedelta(days=settings.get("recycle_bin_days"))


# ------------------------------------------------------------------- decision

def decide(res, action, item=None):
    """(allowed, reason) for ``res.user`` doing ``action`` on ``item`` (None = the top level)."""
    if not res.active:
        return False, "inactive"
    if action in EVERY_USER_ACTIONS:
        return True, None
    if action in ADMIN_ACTIONS:
        return (True, None) if res.is_admin else (False, "admin")
    if action not in ITEM_ACTIONS:
        raise ValueError(f"Unknown permission key: {action}")
    if action in HOLD_BLOCKED and under_hold(item):
        return False, "hold"
    if res.is_admin:
        return True, None
    if action in ADMIN_ONLY_ITEM_ACTIONS:
        return False, "admin"
    if item is None:
        # The top level of the library: only folder creation/upload, through a repository grant.
        if action in ("folder.create_child", "folder.upload", "item.view"):
            return (SHARE_ACTIONS[action] in res.repository()), "level"
        return False, "owner"
    if res.owns(item):
        if action == "item.restore" and not within_retention(item):
            return False, "window"
        if action in ("item.delete", "item.move") and isinstance(item, Folder) and foreign_items(item, res.uid):
            return False, "foreign"
        return True, None
    if action in OWNER_ACTIONS:
        return False, "owner"
    if action.startswith("folder.") and not isinstance(item, Folder):
        return False, "level"
    return (SHARE_ACTIONS[action] in res.perms(item)), "level"


def can(user, action, item=None, res=None):
    """True when ``user`` may perform ``action`` on ``item`` (no audit, no abort)."""
    from .permissions import Resolver, resolver
    if res is None:
        current = getattr(g, "user", None) if has_request_context() else None
        res = resolver() if (current is not None and user is not None and current.id == user.id) else Resolver(user)
    return decide(res, action, item)[0]


def actions_for(item, res=None):
    """The item actions the current user may perform on ``item`` (for the user interface)."""
    from .permissions import resolver
    res = res or resolver()
    keys = SHARE_ACTIONS.keys() | OWNER_ACTIONS | ADMIN_ONLY_ITEM_ACTIONS
    if not isinstance(item, Folder) and item is not None:
        keys = {k for k in keys if not k.startswith("folder.")}
    return sorted(k for k in keys if decide(res, k, item)[0])


def _admin_override(res, action, item):
    """Audit an administrator using access they would not have as an ordinary user (once per request)."""
    from .permissions import Resolver
    from ..services.activity import log_admin_override
    if item is None or not res.is_admin:
        return
    plain = Resolver(res.user, honour_admin=False)
    if decide(plain, action, item)[0]:
        return
    seen = g.setdefault("dms_overrides", set())
    key = (action, type(item).__name__, item.id)
    if key in seen:
        return
    seen.add(key)
    log_admin_override(action, item)


def require(action, item=None, message=None):
    """Abort unless the signed-in user may perform ``action`` on ``item``.

    404 when the user may not even see the item (its existence is not revealed), 403 otherwise.
    Refusals are audited (status ``denied``); administrator overrides are audited too.
    """
    from .permissions import resolver
    from ..services.activity import log_denied
    res = resolver()
    allowed, reason = decide(res, action, item)
    if not allowed:
        can_view = item is None or decide(res, "item.view", item)[0]
        log_denied(action, item, can_view)
        if not can_view:
            abort(404, description="Not found.")
        text = MESSAGES.get(reason, MESSAGES["level"])
        if reason == "foreign":
            text = text.format(verb="moved" if action == "item.move" else "deleted")
        abort(403, description=message or text)
    if res.is_admin:
        _admin_override(res, action, item)


def require_system(action):
    """Abort with 403 unless the signed-in user may perform the system action ``action``."""
    from .permissions import resolver
    from ..services.activity import log_denied
    res = resolver()
    allowed, reason = decide(res, action)
    if not allowed:
        log_denied(action, None, True)
        abort(403, description=MESSAGES.get(reason, MESSAGES["admin"]))
