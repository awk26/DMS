"""Managing permissions on the repository, folders, documents and versions."""
from sqlalchemy import select

from ..extensions import db
from ..models import AccessEntry, Document, DocumentVersion, Folder, Role, User
from ..security.permissions import CONTENT_PERMS, normalize, parse_perms, reset_resolver
from ..validation import ValidationError
from .activity import log_activity
from .folders import NotFound

TARGET_TYPES = ("repository", "folder", "document", "version")
SHAREABLE = ("view", "download", "edit")
# Simple access levels offered by the Share dialog, lowest first.
SHARE_LEVELS = {
    "view": ("Can view", {"view"}),
    "download": ("Can view & download", {"view", "download"}),
    "edit": ("Can edit", {"view", "download", "edit"}),
    "full": ("Full control", {"manage"}),
}


def level_of(perms):
    """The Share-dialog level that best describes a permission set (None if it grants nothing)."""
    perms = normalize(perms)
    for key in ("full", "edit", "download", "view"):
        if normalize(SHARE_LEVELS[key][1]) <= perms:
            return key
    return None


def grant_owner(obj, user):
    """Give the creator full control of what they created, unless they already have it there.

    Without this a Contributor could upload into a shared folder but never delete or share
    their own file. Does not commit.
    """
    from ..security.permissions import Resolver
    if user is None or obj is None or Resolver(user).can("manage", obj):
        return
    ttype, tid = target_key(obj)
    db.session.add(AccessEntry(target_type=ttype, target_id=tid, principal_type="user", principal_id=user.id,
                               permissions=parse_perms(CONTENT_PERMS), created_by_id=user.id))
    db.session.flush()
    reset_resolver()


def target_key(obj):
    if obj is None:
        return "repository", None
    if isinstance(obj, Folder):
        return "folder", obj.id
    if isinstance(obj, Document):
        return "document", obj.id
    if isinstance(obj, DocumentVersion):
        return "version", obj.id
    raise TypeError(obj)


def target_label(obj):
    if obj is None:
        return "Repository"
    if isinstance(obj, Folder):
        return f"Folder '{obj.path}'"
    if isinstance(obj, Document):
        return f"Document '{obj.name}'"
    return f"Version {obj.label} of '{obj.document.name}'"


def parent_of(obj):
    """The node permissions are inherited from, or the sentinel 'top' for the repository."""
    if obj is None:
        return "top"
    if isinstance(obj, Folder):
        return obj.parent
    if isinstance(obj, Document):
        return obj.folder
    return obj.document


def entries_for(obj):
    ttype, tid = target_key(obj)
    q = select(AccessEntry).where(AccessEntry.target_type == ttype)
    q = q.where(AccessEntry.target_id.is_(None) if tid is None else AccessEntry.target_id == tid)
    return db.session.execute(q.order_by(AccessEntry.principal_type, AccessEntry.id)).scalars().all()


def principal(entry):
    model = User if entry.principal_type == "user" else Role
    return db.session.get(model, entry.principal_id)


def principal_label(entry):
    p = principal(entry)
    if p is None:
        return "(removed)"
    return p.label if isinstance(p, User) else f"{p.name} (role)"


def inheritance_chain(obj):
    """[(node, entries)] from obj up to where inheritance stops (for the permissions page)."""
    chain, node = [], obj
    while True:
        chain.append((node, entries_for(node)))
        if node is None:
            break
        if not node.inherit_permissions:
            break
        node = parent_of(node)
    return chain


def _resolve_principal(principal_type, principal_id):
    if principal_type not in ("user", "role"):
        raise ValidationError("Choose a user or a role.")
    model = User if principal_type == "user" else Role
    try:
        p = db.session.get(model, int(principal_id))
    except (TypeError, ValueError):
        p = None
    if p is None:
        raise NotFound("User or role not found.")
    return p


def set_entry(obj, principal_type, principal_id, perms, actor=None):
    """Create or replace the grant for one principal on obj. Empty perms removes the grant."""
    p = _resolve_principal(principal_type, principal_id)
    ttype, tid = target_key(obj)
    perms = normalize(perms)
    q = select(AccessEntry).where(AccessEntry.target_type == ttype, AccessEntry.principal_type == principal_type,
                                  AccessEntry.principal_id == p.id)
    q = q.where(AccessEntry.target_id.is_(None) if tid is None else AccessEntry.target_id == tid)
    entry = db.session.execute(q).scalar_one_or_none()
    name = p.label if isinstance(p, User) else f"role {p.name}"
    if not perms:
        if entry is not None:
            db.session.delete(entry)
            log_activity("security", "permission_removed", f"{target_label(obj)}: removed access for {name}", tid)
    else:
        if entry is None:
            entry = AccessEntry(target_type=ttype, target_id=tid, principal_type=principal_type,
                                principal_id=p.id, created_by_id=actor.id if actor else None)
            db.session.add(entry)
        entry.permissions = parse_perms(perms)
        log_activity("security", "permission_set",
                     f"{target_label(obj)}: {name} granted {', '.join(p_ for p_ in CONTENT_PERMS if p_ in perms)}", tid)
    db.session.commit()
    reset_resolver()


def remove_entry(obj, entry_id):
    entry = db.session.get(AccessEntry, entry_id)
    ttype, tid = target_key(obj)
    if entry is None or entry.target_type != ttype or entry.target_id != tid:
        raise NotFound("Permission entry not found.")
    name = principal_label(entry)
    db.session.delete(entry)
    log_activity("security", "permission_removed", f"{target_label(obj)}: removed access for {name}", tid)
    db.session.commit()
    reset_resolver()


def share_level(obj, actor_perms, user_id, level, actor):
    """Give one user a Share-dialog access level on obj.

    Users who manage the item set the level exactly (it can also be lowered). Users who
    may only share can add view/download/edit up to their own access.
    """
    if level not in SHARE_LEVELS:
        raise ValidationError("Choose what the person can do.")
    if actor is not None and int(user_id) == actor.id:
        raise ValidationError("You cannot change your own access.")
    perms = SHARE_LEVELS[level][1]
    if "manage" in actor_perms:
        set_entry(obj, "user", user_id, perms, actor)
    elif level == "full":
        raise ValidationError("Only people with full control can give full control.")
    else:
        share(obj, actor_perms, "user", user_id, perms, actor)


def share(obj, actor_perms, principal_type, principal_id, perms, actor):
    """Share = grant view/download/edit, limited to what the sharer has."""
    perms = {p for p in perms if p in SHAREABLE}
    if not perms:
        raise ValidationError("Choose at least one permission to share.")
    if not perms <= actor_perms:
        raise ValidationError("You can only share permissions you have yourself.")
    existing = set()
    ttype, tid = target_key(obj)
    for e in entries_for(obj):
        if e.principal_type == principal_type and str(e.principal_id) == str(principal_id):
            existing = e.permission_set
    set_entry(obj, principal_type, principal_id, existing | perms, actor)


def set_inheritance(obj, inherit, copy_inherited=True, actor=None):
    """Break (inherit=False) or restore inheritance on a folder, document or version.

    When breaking, the currently inherited grants are copied onto the node so nobody loses
    access by surprise; they can then be edited individually.
    """
    if obj is None:
        raise ValidationError("The repository has nothing to inherit from.")
    if bool(obj.inherit_permissions) == bool(inherit):
        return
    if not inherit and copy_inherited:
        own = {(e.principal_type, e.principal_id): e for e in entries_for(obj)}
        merged = {}
        node = parent_of(obj)
        while True:
            node_obj = None if node == "top" else node
            for e in entries_for(node_obj):
                merged.setdefault((e.principal_type, e.principal_id), set()).update(e.permission_set)
            if node == "top" or node is None or not node.inherit_permissions:
                break
            node = parent_of(node)
        ttype, tid = target_key(obj)
        for key, perms in merged.items():
            if key in own:
                own[key].permissions = parse_perms(own[key].permission_set | perms)
            else:
                db.session.add(AccessEntry(target_type=ttype, target_id=tid, principal_type=key[0],
                                           principal_id=key[1], permissions=parse_perms(perms),
                                           created_by_id=actor.id if actor else None))
    obj.inherit_permissions = bool(inherit)
    log_activity("security", "inheritance_changed",
                 f"{target_label(obj)}: permission inheritance {'restored' if inherit else 'stopped'}",
                 target_key(obj)[1])
    db.session.commit()
    reset_resolver()


def delete_entries_for(ttype, ids):
    if not ids:
        return
    db.session.query(AccessEntry).filter(AccessEntry.target_type == ttype, AccessEntry.target_id.in_(list(ids))
                                         ).delete(synchronize_session=False)
