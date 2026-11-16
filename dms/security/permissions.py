"""Role-based access control and permission resolution (FR-SEC-01, FR-SEC-02, FR-FLD-02).

Permissions are granted by AccessEntry rows on four levels:

    repository  ->  folder  ->  (sub)folder ...  ->  document  ->  version

A node inherits everything granted on its parent unless inheritance is broken on
that node (``inherit_permissions = False``); then only the entries on the node
itself (and below) apply. Grants go to users or roles. ``manage`` implies all
other permissions and any grant implies ``view``.

Privacy: administrators have every permission on the repository root itself so they can run it
(create top-level folders, manage repository permissions), but those rights do NOT flow down to any
content: not to other users' My Files and not to folders or documents in the Shared library either.
An administrator sees, downloads or edits another user's folder, document or version only when it
was explicitly shared with them (or with a role they hold) on that item or a folder above it, or
when they created it themselves - exactly like any other user without a role grant.

Item-level access: a grant made to a user personally on a folder, document or version (the Share
dialog) is that user's exact access to the item and everything below it that inherits. It replaces,
rather than adds to, what the user would otherwise get from their roles or from parent folders, so
lowering someone from "Full control" to "Can view" really leaves them view-only, even in the Shared
library where their role grants more. Grants deeper down (e.g. on a document inside a shared folder)
override again for that item. Repository-level grants are unchanged (roles and users add up).

Upload: ``upload`` lets someone add new files and subfolders to a folder without changing what is
already there (``edit`` implies it). What they add is theirs (owner grant), nothing else changes.

Expiry: a grant may carry ``expires_at``. From that moment it is ignored everywhere, as if it had been
removed, so the person falls back to whatever else they have (e.g. the parent folder's access).

Groups: departments and teams are roles (no separate groups table). A grant to a role on a department
folder gives every member that access; repository-wide grants are kept for Admin/Records Manager only
("restricted by default", see migrate._restricted_repository).
"""
from flask import g, has_request_context
from sqlalchemy import and_, false, or_, select

from ..extensions import db
from ..models import AccessEntry, Document, DocumentVersion, Folder, utcnow

CONTENT_PERMS = ("view", "upload", "download", "edit", "delete", "share", "manage")
PERM_LABELS = {
    "view": "View", "upload": "Upload", "download": "Download", "edit": "Edit", "delete": "Delete",
    "share": "Share", "manage": "Manage permissions",
}
PERM_HELP = {
    "view": "See the item, its details and in-browser preview",
    "upload": "Add new files and subfolders (without changing existing ones)",
    "download": "Download original files and ZIP exports",
    "edit": "Upload, create subfolders, new versions, rename, move, check out, edit metadata",
    "delete": "Move to the recycle bin",
    "share": "Give other users or roles view, download or edit access",
    "manage": "Change permissions and inheritance (implies all of the above)",
}
# There is no email capability: every user archives their own Gmail mailbox and only they can see
# those emails (services/emails.owned_email). A leftover "emails" value in roles.capabilities is ignored.
SYSTEM_CAPS = ("admin", "records", "audit")
CAP_LABELS = {
    "admin": "Administration (users, roles, settings; full access)",
    "records": "Records management (recycle bin, retention, check-out override)",
    "audit": "Audit (legacy; every user now sees their own activity log only)",
}

# name -> (system capabilities, repository-level content permissions, description)
PREDEFINED_ROLES = {
    "Admin": ({"admin", "records", "audit"}, set(CONTENT_PERMS),
              "Full control of the system, users, roles and settings."),
    "Records Manager": ({"records", "audit"}, set(CONTENT_PERMS),
                        "Manages the repository, recycle bin, retention and check-outs."),
    # Restricted by default: these roles have no repository-wide access. Members work only in the
    # department folders they are granted (on the folder, to the role or to a department role).
    "Contributor": (set(), set(), "Uploads and edits documents in the folders they are given access to."),
    "Reader": (set(), set(), "Views and downloads documents in the folders they are given access to."),
    "Auditor": ({"audit"}, set(), "Read-only access to the folders they are given access to."),
}
# Repository-wide permissions these roles had before "restricted by default"; the upgrade moves them
# onto the existing top-level content so nobody loses access to what they could already open.
LEGACY_REPOSITORY_PERMS = {
    "Contributor": {"view", "download", "edit", "share"},
    "Reader": {"view", "download"},
    "Auditor": {"view"},
}

ALL = frozenset(CONTENT_PERMS)


def normalize(perms):
    perms = {p for p in perms if p in CONTENT_PERMS}
    if "manage" in perms:
        return set(ALL)
    if "edit" in perms:
        perms.add("upload")
    if perms:
        perms.add("view")
    return perms


def active_clause():
    """SQL condition for grants that have not expired."""
    return or_(AccessEntry.expires_at.is_(None), AccessEntry.expires_at > utcnow())


def parse_perms(values):
    return ",".join(p for p in CONTENT_PERMS if p in set(values))


class Resolver:
    """Computes a user's effective permissions. One instance per request (cached)."""

    def __init__(self, user):
        self.user = user
        # Administrators manage the repository root only; content access always comes from grants
        # on the item or a folder above it (see _from_root).
        self.is_admin = bool(user and user.is_admin)
        self.role_ids = {r.id for r in user.roles} if user else set()
        self._entries = None
        self._folders = None
        self._memo = {}

    # -- data ----------------------------------------------------------------------
    def _load_entries(self):
        if self._entries is None:
            self._entries = {}
            if self.user is not None:
                rows = db.session.execute(select(AccessEntry).where(active_clause(), or_(
                    and_(AccessEntry.principal_type == "user", AccessEntry.principal_id == self.user.id),
                    and_(AccessEntry.principal_type == "role",
                         AccessEntry.principal_id.in_(self.role_ids or [-1]))))).scalars().all()
                for e in rows:
                    slot = self._entries.setdefault((e.target_type, e.target_id), {"user": None, "role": set()})
                    if e.principal_type == "user":
                        slot["user"] = (slot["user"] or set()) | e.permission_set
                    else:
                        slot["role"] |= e.permission_set
        return self._entries

    def _folder_map(self):
        if self._folders is None:
            rows = db.session.execute(select(Folder.id, Folder.parent_id, Folder.inherit_permissions,
                                             Folder.deleted_at)).all()
            self._folders = {r.id: (r.parent_id, r.inherit_permissions, r.deleted_at) for r in rows}
        return self._folders

    def _grant(self, target_type, target_id):
        """Everything granted on the node to the user or their roles (no inheritance)."""
        slot = self._load_entries().get((target_type, target_id))
        if slot is None:
            return set()
        return set(slot["role"]) | set(slot["user"] or ())

    def _resolve(self, target_type, target_id, inherit, inherited):
        """Permissions on one node. A non-empty personal grant on the node is the user's exact access
        there (item-level access); otherwise role grants on the node plus, when the node inherits,
        the permissions on its parent (``inherited()``)."""
        slot = self._load_entries().get((target_type, target_id))
        if slot is not None and slot["user"]:
            return normalize(slot["user"])
        perms = set(slot["role"]) if slot is not None else set()
        if inherit:
            perms |= inherited()
        return normalize(perms)

    # -- resolution ----------------------------------------------------------------------
    def repository(self):
        if self.is_admin:
            return set(ALL)
        return normalize(self._grant("repository", None))

    def _from_root(self):
        """What top-level folders and root documents inherit from the repository. Administrators get
        nothing: their repository rights are for running the system, not for reading users' content."""
        if self.is_admin:
            return set()
        return self.repository()

    def folder(self, folder):
        if folder is None:
            return self.repository()
        fid = folder if isinstance(folder, int) else folder.id
        key = ("folder", fid)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = set()  # cycle guard
        info = self._folder_map().get(fid)
        if info is None:
            return set()
        parent_id, inherit, _ = info
        result = self._resolve("folder", fid, inherit,
                               lambda: self.folder(parent_id) if parent_id else self._from_root())
        self._memo[key] = result
        return result

    def document(self, doc):
        key = ("document", doc.id)
        if key not in self._memo:
            self._memo[key] = self._resolve(
                "document", doc.id, doc.inherit_permissions,
                lambda: self.folder(doc.folder_id) if doc.folder_id else self._from_root())
        return self._memo[key]

    def version(self, version):
        return self._resolve("version", version.id, version.inherit_permissions,
                             lambda: self.document(version.document))

    def perms(self, obj):
        if obj is None:
            return self.repository()
        if isinstance(obj, Folder):
            return self.folder(obj)
        if isinstance(obj, Document):
            return self.document(obj)
        if isinstance(obj, DocumentVersion):
            return self.version(obj)
        raise TypeError(obj)

    def can(self, perm, obj=None):
        return perm in self.perms(obj)

    def has(self, capability):
        return bool(self.user) and self.user.has(capability)

    # -- security trimming -----------------------------------------------------------
    def visible_folder_ids(self):
        return {fid for fid, (_, _, deleted) in self._folder_map().items()
                if deleted is None and "view" in self.folder(fid)}

    def visible_documents_clause(self):
        """SQL condition limiting a Document query to documents the user may view."""
        if self.user is None:
            return false()
        if "clause" not in self._memo:
            self._memo["clause"] = self._build_clause()
        return self._memo["clause"]

    def _build_clause(self):
        entries = self._load_entries()
        own_entry_ids = {tid for (ttype, tid) in entries if ttype == "document"}
        broken = set(db.session.execute(select(Document.id).where(
            Document.inherit_permissions.is_(False))).scalars())
        special = own_entry_ids | broken
        special_visible = []
        if special:
            for doc in db.session.execute(select(Document).where(Document.id.in_(special))).scalars():
                if "view" in self.document(doc):
                    special_visible.append(doc.id)
        folder_ids = self.visible_folder_ids()
        in_folders = [Document.folder_id.in_(folder_ids)] if folder_ids else []
        if "view" in self._from_root():
            in_folders.append(Document.folder_id.is_(None))
        inherited = and_(Document.id.not_in(special), or_(*in_folders)) if in_folders else false()
        if special_visible:
            return or_(inherited, Document.id.in_(special_visible))
        return inherited


def resolver():
    """The current request's resolver (for the signed-in user)."""
    if has_request_context():
        if "dms_resolver" not in g:
            g.dms_resolver = Resolver(getattr(g, "user", None))
        return g.dms_resolver
    return Resolver(None)


def reset_resolver():
    if has_request_context():
        g.pop("dms_resolver", None)
