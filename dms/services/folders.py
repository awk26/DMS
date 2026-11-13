"""Folder hierarchy operations (FR-FLD-01, FR-FLD-03, FR-FLD-05)."""
import uuid

from sqlalchemy import func, select

from ..extensions import db
from ..models import Document, Folder, FolderActivity, utcnow
from ..validation import ValidationError, validate_name
from .activity import log_activity, log_folder


class NotFound(Exception):
    pass


def get_folder(folder_id, include_deleted=False):
    if folder_id in (None, "", 0, "0", "root"):
        return None
    try:
        folder = db.session.get(Folder, int(folder_id))
    except (TypeError, ValueError):
        folder = None
    if folder is None or (folder.deleted_at is not None and not include_deleted):
        raise NotFound("Folder not found.")
    return folder


def _path_label(folder):
    return folder.path if folder else "Root"


def _name_taken(name, parent_id, exclude_id=None):
    q = select(Folder.id).where(func.lower(Folder.name) == name.lower(), Folder.deleted_at.is_(None))
    q = q.where(Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id)
    if exclude_id:
        q = q.where(Folder.id != exclude_id)
    return db.session.execute(q).first() is not None


def _ensure_unique(name, parent_id, exclude_id=None):
    if _name_taken(name, parent_id, exclude_id):
        raise ValidationError(f"A folder named '{name}' already exists here.")


def _unique_name(name, parent_id):
    if not _name_taken(name, parent_id):
        return name
    for i in range(1, 1000):
        candidate = f"{name} ({i})"
        if not _name_taken(candidate, parent_id):
            return candidate
    raise ValidationError("Could not find a free folder name.")


HOME_CONTAINER_NAME = "Personal files"


def _home_container():
    """The hidden top-level folder holding every user's My Files (nobody, administrators included, sees
    another user's My Files unless it is shared with them)."""
    from . import settings
    folder = db.session.get(Folder, settings.get("home_container_folder_id") or 0)
    if folder is not None and folder.deleted_at is None:
        return folder
    folder = Folder(name=_unique_name(HOME_CONTAINER_NAME, None), inherit_permissions=False)
    db.session.add(folder)
    db.session.flush()
    settings.set_value("home_container_folder_id", folder.id)
    log_activity("folder", "created", f"Folder '{folder.name}' created for personal files", folder.id,
                 entity_name=folder.name)
    return folder


def ensure_home(user):
    """Return the user's private My Files folder, creating it on first use."""
    from ..models import AccessEntry
    from ..security.permissions import CONTENT_PERMS, parse_perms, reset_resolver
    from ..validation import _BAD_CHARS
    home = db.session.get(Folder, user.home_folder_id) if user.home_folder_id else None
    if home is not None and home.deleted_at is None:
        return home
    container = _home_container()
    name = _BAD_CHARS.sub("_", user.username).rstrip(".")[:200] or f"user-{user.id}"
    home = Folder(name=_unique_name(name, container.id), parent=container, inherit_permissions=False,
                  created_by_id=user.id)
    db.session.add(home)
    db.session.flush()
    db.session.add(AccessEntry(target_type="folder", target_id=home.id, principal_type="user",
                               principal_id=user.id, permissions=parse_perms(CONTENT_PERMS)))
    user.home_folder_id = home.id
    log_activity("folder", "created", f"My Files created for '{user.username}'", home.id, entity_name=home.name,
                 details={"owner": user.username})
    db.session.commit()
    reset_resolver()
    return home


def system_folder_ids():
    """Folders that must keep their name and place: the personal-files container and every My Files."""
    from ..models import User
    from . import settings
    ids = set(db.session.execute(select(User.home_folder_id).where(User.home_folder_id.is_not(None))).scalars())
    if settings.get("home_container_folder_id"):
        ids.add(settings.get("home_container_folder_id"))
    return ids


def _ensure_not_system(folders, action):
    protected = system_folder_ids()
    for f in folders:
        if f.id in protected:
            raise ValidationError(f"'{f.name}' is a personal files folder and cannot be {action}.")


def create_folder(name, parent=None, user=None):
    from .access import grant_owner
    name = validate_name(name, "folder")
    _ensure_unique(name, parent.id if parent else None)
    folder = Folder(name=name, parent=parent, created_by_id=user.id if user else None)
    db.session.add(folder)
    db.session.flush()
    grant_owner(folder, user)
    log_folder(folder, "created", parent=_path_label(parent))
    log_folder(parent, "subfolder_added", folder=name, folder_id=folder.id)
    log_activity("folder", "created", f"Folder '{folder.path}' created", folder.id, entity_name=folder.path,
                 details={"parent": _path_label(parent)})
    db.session.commit()
    return folder


def rename_folder(folder, new_name):
    new_name = validate_name(new_name, "folder")
    if new_name == folder.name:
        return folder
    _ensure_not_system([folder], "renamed")
    _ensure_unique(new_name, folder.parent_id, exclude_id=folder.id)
    old = folder.name
    folder.name = new_name
    log_folder(folder, "renamed", old_name=old, new_name=new_name)
    log_folder(folder.parent, "subfolder_renamed", old_name=old, new_name=new_name, folder_id=folder.id)
    log_activity("folder", "renamed", f"Folder '{old}' renamed to '{new_name}'", folder.id, entity_name=folder.path,
                 changes={"name": {"old": old, "new": new_name}})
    _reindex_subtree(folder)
    db.session.commit()
    return folder


def is_descendant(candidate, ancestor):
    """True if ``candidate`` is ``ancestor`` or lies beneath it."""
    node, seen = candidate, set()
    while node is not None and node.id not in seen:
        if node.id == ancestor.id:
            return True
        seen.add(node.id)
        node = node.parent
    return False


def move_folder(folder, new_parent):
    if new_parent is not None and is_descendant(new_parent, folder):
        raise ValidationError("A folder cannot be moved into itself or one of its subfolders.")
    if (new_parent.id if new_parent else None) == folder.parent_id:
        return folder
    _ensure_not_system([folder], "moved")
    _ensure_unique(folder.name, new_parent.id if new_parent else None, exclude_id=folder.id)
    old_parent = folder.parent
    old_path = folder.path
    folder.parent = new_parent
    db.session.flush()
    log_folder(folder, "moved", old_location=_path_label(old_parent), new_location=_path_label(new_parent))
    log_folder(old_parent, "subfolder_removed", folder=folder.name, folder_id=folder.id,
               moved_to=_path_label(new_parent))
    log_folder(new_parent, "subfolder_added", folder=folder.name, folder_id=folder.id,
               moved_from=_path_label(old_parent))
    log_activity("folder", "moved", f"Folder '{old_path}' moved to '{folder.path}'", folder.id,
                 entity_name=folder.path, changes={"location": {"old": _path_label(old_parent),
                                                                "new": _path_label(new_parent)},
                                                   "path": {"old": old_path, "new": folder.path}})
    _reindex_subtree(folder)
    db.session.commit()
    return folder


def subtree(folder, include_deleted=False):
    """[folder, *descendants] (depth-first)."""
    out, stack, seen = [], [folder], set()
    while stack:
        node = stack.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        if node.deleted_at is not None and node is not folder and not include_deleted:
            continue
        out.append(node)
        stack.extend(reversed(node.children))
    return out


def _reindex_subtree(folder):
    from . import search
    for node in subtree(folder):
        for doc in node.documents:
            if doc.deleted_at is None:
                search.index_document_meta(doc)


def copy_folder(folder, new_parent, user=None, res=None, report=None):
    """Recursively copy a folder, its subfolders and the current version of every document.

    With ``res`` (the copier's permission resolver) only what they may read is copied: subfolders
    they cannot view and documents (or current versions) they cannot download are left out, so a
    copy, which the copier then owns, never exposes restricted content. ``report["skipped"]``
    receives the number of items left out.
    """
    from .documents import copy_document
    if new_parent is not None and is_descendant(new_parent, folder):
        raise ValidationError("A folder cannot be copied into itself or one of its subfolders.")
    created_keys = []
    skipped = 0

    def readable(doc):
        if res is None:
            return True
        cv = doc.current_version
        return res.can("download", doc) and (cv is None or "download" in res.version(cv))

    def walk(src, parent):
        nonlocal skipped
        copy = Folder(name=_unique_name(src.name, parent.id if parent else None) if parent is new_parent else src.name,
                      parent=parent, created_by_id=user.id if user else None)
        db.session.add(copy)
        db.session.flush()
        for doc in sorted(src.documents, key=lambda d: d.name.lower()):
            if doc.deleted_at is None:
                if not readable(doc):
                    skipped += 1
                    continue
                new_doc = copy_document(doc, copy, user, commit=False)
                created_keys.append(new_doc.current_version.storage_key)
        for child in src.live_children:
            if res is not None and not res.can("view", child):
                skipped += 1
                continue
            walk(child, copy)
        return copy

    try:
        top = walk(folder, new_parent)
        from .access import grant_owner
        grant_owner(top, user)
        log_folder(top, "created", copied_from=folder.path)
        log_folder(new_parent, "subfolder_added", folder=top.name, folder_id=top.id)
        note = f" ({skipped} restricted item(s) not copied)" if skipped else ""
        log_activity("folder", "copied", f"Folder '{folder.path}' copied to '{top.path}'{note}", top.id,
                     entity_name=top.path, details={"source_folder_id": folder.id, "source_path": folder.path,
                                                    "skipped": skipped})
        db.session.commit()
        if report is not None:
            report["skipped"] = skipped
        return top
    except Exception:
        db.session.rollback()
        from .documents import delete_stored
        delete_stored(created_keys)
        raise


def ensure_can_remove_subtree(nodes, res, action):
    """The user needs delete permission on every folder and live document in ``nodes`` to take them
    out of where they are (delete or move). Items the user cannot see are never named in the error."""
    for node in nodes:
        if not res.can("delete", node):
            if res.can("view", node):
                raise ValidationError(f"You do not have permission to {action} the subfolder '{node.path}'.")
            raise ValidationError(f"You cannot {action} this folder: it contains items you do not have "
                                  "access to.")
        for doc in node.documents:
            if doc.deleted_at is None and not res.can("delete", doc):
                if res.can("view", doc):
                    raise ValidationError(f"You do not have permission to {action} '{doc.name}' in "
                                          f"'{node.path}'.")
                raise ValidationError(f"You cannot {action} this folder: it contains items you do not have "
                                      "access to.")


def delete_folder(folder, user=None, res=None):
    """Move a folder with everything in it to the recycle bin (restorable as one unit).

    With ``res`` (the user's permission resolver) the user needs delete permission on every
    subfolder and on every document inside, including documents with their own permissions.
    """
    from .documents import ensure_unlocked, trash_document
    nodes = subtree(folder)
    _ensure_not_system(nodes, "deleted")
    if res is not None:
        ensure_can_remove_subtree(nodes, res, "delete")
    for node in nodes:
        for doc in node.documents:
            if doc.deleted_at is None:
                ensure_unlocked(doc, user)
    batch = uuid.uuid4().hex
    now = utcnow()
    for node in nodes:
        for doc in node.documents:
            if doc.deleted_at is None:
                trash_document(doc, user, batch=batch, commit=False)
        node.deleted_at = now
        node.deleted_batch = batch
        node.deleted_by_id = user.id if user else None
    log_folder(folder.parent, "subfolder_removed", folder=folder.name, folder_id=folder.id, deleted=True)
    log_activity("folder", "deleted", f"Folder '{folder.path}' moved to the recycle bin", folder.id,
                 entity_name=folder.path, details={"subfolders": len(nodes) - 1, "batch": batch})
    db.session.commit()
    return batch


def restore_target(folder, user=None):
    """Where to restore an item whose folder is still in the recycle bin.

    The nearest live ancestor keeps it under the same permissions it had (a file from someone's My Files
    stays private). If no ancestor is live, the item goes to the restoring user's My Files - never to the
    top level of the Shared library, where it would inherit repository-wide access.
    """
    node = folder
    while node is not None and node.deleted_at is not None:
        node = node.parent
    if node is not None:
        return node
    return ensure_home(user) if user is not None else None


def restore_folder(folder, user=None):
    """Restore a deleted folder with the subfolders and documents deleted together with it."""
    from .documents import restore_document
    if folder.deleted_at is None:
        return folder
    batch = folder.deleted_batch
    if folder.parent is not None and folder.parent.deleted_at is not None:
        folder.parent = restore_target(folder.parent, user)  # parent still in the recycle bin
    folder.name = _unique_name(folder.name, folder.parent_id) if _name_taken(folder.name, folder.parent_id,
                                                                             folder.id) else folder.name
    for node in subtree(folder, include_deleted=True):
        if node is folder or node.deleted_batch == batch:
            node.deleted_at = None
            node.deleted_batch = None
            node.deleted_by_id = None
    db.session.flush()
    for doc in db.session.execute(select(Document).where(Document.deleted_batch == batch)).scalars():
        restore_document(doc, commit=False)
    log_folder(folder, "restored")
    log_activity("folder", "restored", f"Folder '{folder.path}' restored from the recycle bin", folder.id,
                 entity_name=folder.path)
    db.session.commit()
    return folder


def purge_folder(folder, reason=None):
    """Permanently delete a folder in the recycle bin with all of its contents."""
    from ..models import DocumentLink, Favourite, MetadataField
    from .access import delete_entries_for
    from .documents import delete_stored, purge_document
    if folder.deleted_at is None:
        raise ValidationError("Only folders in the recycle bin can be permanently deleted.")
    nodes = subtree(folder, include_deleted=True)
    ids = [n.id for n in nodes]
    keys = []
    for doc in db.session.execute(select(Document).where(Document.folder_id.in_(ids))).scalars().all():
        if doc.deleted_at is None:  # should not happen; keep live documents by moving them to the top level
            doc.folder_id = None
            continue
        keys += purge_document(doc, commit=False, reason=reason)
    path = folder.path
    db.session.query(DocumentLink).filter(DocumentLink.folder_id.in_(ids)).delete(synchronize_session=False)
    db.session.query(Favourite).filter(Favourite.target_type == "folder", Favourite.target_id.in_(ids)
                                       ).delete(synchronize_session=False)
    db.session.query(MetadataField).filter(MetadataField.folder_id.in_(ids)).delete(synchronize_session=False)
    db.session.query(FolderActivity).filter(FolderActivity.folder_id.in_(ids)).update(
        {"folder_id": None}, synchronize_session=False)
    delete_entries_for("folder", ids)
    for node in reversed(nodes):  # children first
        db.session.delete(node)
        db.session.flush()
    log_activity("folder", "purged", f"Folder '{path}' permanently deleted" + (f" ({reason})" if reason else ""),
                 ids[0], entity_name=path, details={"subfolders": len(ids) - 1, "reason": reason})
    db.session.commit()
    delete_stored(keys)


def folder_tree(visible_ids=None):
    """Live folders as a depth-first list of (folder, depth).

    With ``visible_ids`` the tree is security-trimmed: hidden folders are skipped and their
    visible descendants are attached to the nearest visible ancestor.
    """
    folders = db.session.execute(select(Folder).where(Folder.deleted_at.is_(None))
                                 .order_by(func.lower(Folder.name))).scalars().all()
    by_id = {f.id: f for f in folders}

    def visible_parent(f):
        if visible_ids is None:
            return f.parent_id
        p = f.parent_id
        while p is not None and p not in visible_ids:
            node = by_id.get(p)
            p = node.parent_id if node else None
        return p

    by_parent = {}
    for f in folders:
        if visible_ids is not None and f.id not in visible_ids:
            continue
        by_parent.setdefault(visible_parent(f), []).append(f)
    out = []

    def walk(parent_id, depth, seen):
        for f in by_parent.get(parent_id, []):
            if f.id in seen:
                continue
            seen.add(f.id)
            out.append((f, depth))
            walk(f.id, depth + 1, seen)

    walk(None, 0, set())
    return out


def folder_counts(visible_clause=None):
    """Map folder_id -> number of active (visible) documents directly inside it."""
    stmt = select(Document.folder_id, func.count(Document.id)).where(Document.deleted_at.is_(None))
    if visible_clause is not None:
        stmt = stmt.where(visible_clause)
    rows = db.session.execute(stmt.group_by(Document.folder_id)).all()
    return {fid: n for fid, n in rows}
