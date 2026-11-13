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
    """Give the creator their own full-control grant on what they created.

    Without this a Contributor could upload into a shared folder but never delete or share
    their own file. The grant is written even when the creator already inherits full control from
    the parent, so they keep it when the item is moved or the parent's access changes (nobody,
    administrators included, has access that does not come from a grant). Does not commit.
    """
    if user is None or obj is None:
        return
    ttype, tid = target_key(obj)
    if any(e.principal_type == "user" and e.principal_id == user.id for e in entries_for(obj)):
        return
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


def _ordered(perms):
    perms = set(perms or ())
    return [p for p in CONTENT_PERMS if p in perms] + sorted(p for p in perms if p not in CONTENT_PERMS)


def _audit_permission(obj, action, message, principal_type, principal_id, principal_name, old, new, **extra):
    ttype, tid = target_key(obj)
    log_activity("security", action, message, tid, entity_type=ttype,
                 entity_name=target_label(obj),
                 changes={"permissions": {"old": _ordered(old), "new": _ordered(new)}},
                 details={"principal_type": principal_type, "principal_id": principal_id,
                          "principal": principal_name, **extra})


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


def set_entry(obj, principal_type, principal_id, perms, actor=None, audit_action=None, level=None):
    """Create or replace the grant for one principal on obj. Empty perms removes the grant.

    ``audit_action`` overrides the audit action name (e.g. "shared" from the Share dialog).
    """
    p = _resolve_principal(principal_type, principal_id)
    ttype, tid = target_key(obj)
    perms = normalize(perms)
    q = select(AccessEntry).where(AccessEntry.target_type == ttype, AccessEntry.principal_type == principal_type,
                                  AccessEntry.principal_id == p.id)
    q = q.where(AccessEntry.target_id.is_(None) if tid is None else AccessEntry.target_id == tid)
    entry = db.session.execute(q).scalar_one_or_none()
    name = p.label if isinstance(p, User) else f"role {p.name}"
    old_perms = entry.permission_set if entry is not None else set()
    extra = {"level": level} if level else {}
    if not perms:
        if entry is not None:
            db.session.delete(entry)
            _audit_permission(obj, "permission_removed", f"{target_label(obj)}: removed access for {name}",
                              principal_type, p.id, name, old_perms, set(), **extra)
    else:
        if entry is None:
            entry = AccessEntry(target_type=ttype, target_id=tid, principal_type=principal_type,
                                principal_id=p.id, created_by_id=actor.id if actor else None)
            db.session.add(entry)
        entry.permissions = parse_perms(perms)
        _audit_permission(obj, audit_action or "permission_set",
                          f"{target_label(obj)}: {name} granted {', '.join(p_ for p_ in CONTENT_PERMS if p_ in perms)}",
                          principal_type, p.id, name, old_perms, perms, **extra)
    db.session.commit()
    reset_resolver()


def remove_entry(obj, entry_id):
    entry = db.session.get(AccessEntry, entry_id)
    ttype, tid = target_key(obj)
    if entry is None or entry.target_type != ttype or entry.target_id != tid:
        raise NotFound("Permission entry not found.")
    name = principal_label(entry)
    old_perms = entry.permission_set
    db.session.delete(entry)
    _audit_permission(obj, "permission_removed", f"{target_label(obj)}: removed access for {name}",
                      entry.principal_type, entry.principal_id, name, old_perms, set())
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
        set_entry(obj, "user", user_id, perms, actor, audit_action="shared", level=level)
    elif level == "full":
        raise ValidationError("Only people with full control can give full control.")
    else:
        share(obj, actor_perms, "user", user_id, perms, actor, level=level)


def share(obj, actor_perms, principal_type, principal_id, perms, actor, level=None):
    """Share = grant view/download/edit, limited to what the sharer has."""
    perms = {p for p in perms if p in SHAREABLE}
    if not perms:
        raise ValidationError("Choose at least one permission to share.")
    if not perms <= actor_perms:
        raise ValidationError("You can only share permissions you have yourself.")
    existing = set()
    for e in entries_for(obj):
        if e.principal_type == principal_type and str(e.principal_id) == str(principal_id):
            existing = e.permission_set
    if principal_type == "user":
        # A personal grant is the user's exact access to the item (see security/permissions.py), so
        # someone who may only share must never lower what the person already has: keep their
        # current access and add the shared permissions to it. Only managers can restrict access.
        from ..security.permissions import Resolver
        person = _resolve_principal("user", principal_id)
        current = Resolver(person).perms(obj)
        if perms <= current:
            return  # nothing to add
        existing |= current
    set_entry(obj, principal_type, principal_id, existing | perms, actor, audit_action="shared", level=level)


def set_inheritance(obj, inherit, copy_inherited=True, actor=None):
    """Break (inherit=False) or restore inheritance on a folder, document or version.

    When breaking, the currently inherited grants are copied onto the node so nobody loses
    access by surprise; they can then be edited individually.
    """
    if obj is None:
        raise ValidationError("The repository has nothing to inherit from.")
    if bool(obj.inherit_permissions) == bool(inherit):
        return
    copied = []
    if not inherit and actor is not None:
        # Whoever stops inheritance keeps their own current access, so they cannot lock themselves out.
        from ..security.permissions import Resolver
        mine = Resolver(actor).perms(obj)
        ttype, tid = target_key(obj)
        has_own = any(e.principal_type == "user" and e.principal_id == actor.id for e in entries_for(obj))
        if mine and not has_own:
            db.session.add(AccessEntry(target_type=ttype, target_id=tid, principal_type="user",
                                       principal_id=actor.id, permissions=parse_perms(mine),
                                       created_by_id=actor.id))
            db.session.flush()
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
            copied.append({"principal_type": key[0], "principal_id": key[1], "permissions": _ordered(perms)})
    old_inherit = bool(obj.inherit_permissions)
    obj.inherit_permissions = bool(inherit)
    log_activity("security", "inheritance_changed",
                 f"{target_label(obj)}: permission inheritance {'restored' if inherit else 'stopped'}",
                 target_key(obj)[1], entity_type=target_key(obj)[0], entity_name=target_label(obj),
                 changes={"inherit_permissions": {"old": old_inherit, "new": bool(inherit)}},
                 details={"copied_grants": copied} if copied else None)
    db.session.commit()
    reset_resolver()


def delete_entries_for(ttype, ids):
    if not ids:
        return
    db.session.query(AccessEntry).filter(AccessEntry.target_type == ttype, AccessEntry.target_id.in_(list(ids))
                                         ).delete(synchronize_session=False)
