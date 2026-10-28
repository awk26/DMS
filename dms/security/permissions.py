"""Role-based access control and permission resolution (FR-SEC-01, FR-SEC-02, FR-FLD-02).

Permissions are granted by AccessEntry rows on four levels:

    repository  ->  folder  ->  (sub)folder ...  ->  document  ->  version

A node inherits everything granted on its parent unless inheritance is broken on
that node (``inherit_permissions = False``); then only the entries on the node
itself (and below) apply. Grants go to users or roles. Administrators always
have every permission. ``manage`` implies all other permissions and any grant
implies ``view``.
"""
from flask import g, has_request_context
from sqlalchemy import and_, false, or_, select, true

from ..extensions import db
from ..models import AccessEntry, Document, DocumentVersion, Folder

CONTENT_PERMS = ("view", "download", "edit", "delete", "share", "manage")
PERM_LABELS = {
    "view": "View", "download": "Download", "edit": "Edit", "delete": "Delete",
    "share": "Share", "manage": "Manage permissions",
}
PERM_HELP = {
    "view": "See the item, its details and in-browser preview",
    "download": "Download original files and ZIP exports",
    "edit": "Upload, create subfolders, new versions, rename, move, check out, edit metadata",
    "delete": "Move to the recycle bin",
    "share": "Give other users or roles view, download or edit access",
    "manage": "Change permissions and inheritance (implies all of the above)",
}
SYSTEM_CAPS = ("admin", "records", "audit", "emails")
CAP_LABELS = {
    "admin": "Administration (users, roles, settings; full access)",
    "records": "Records management (recycle bin, retention, check-out override)",
    "audit": "Audit (activity and security logs)",
    "emails": "Email archive access",
}

# name -> (system capabilities, repository-level content permissions, description)
PREDEFINED_ROLES = {
    "Admin": ({"admin", "records", "audit", "emails"}, set(CONTENT_PERMS),
              "Full control of the system, users, roles and settings."),
    "Records Manager": ({"records", "audit", "emails"}, set(CONTENT_PERMS),
                        "Manages the repository, recycle bin, retention and check-outs."),
    "Contributor": (set(), {"view", "download", "edit", "share"},
                    "Uploads and edits documents and creates folders."),
    "Reader": (set(), {"view", "download"}, "Views and downloads documents."),
    "Auditor": ({"audit", "emails"}, {"view"}, "Read-only access to content and the audit trail."),
}

ALL = frozenset(CONTENT_PERMS)


def normalize(perms):
    perms = {p for p in perms if p in CONTENT_PERMS}
    if "manage" in perms:
        return set(ALL)
    if perms:
        perms.add("view")
    return perms


def parse_perms(values):
    return ",".join(p for p in CONTENT_PERMS if p in set(values))


class Resolver:
    """Computes a user's effective permissions. One instance per request (cached)."""

    def __init__(self, user):
        self.user = user
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
                rows = db.session.execute(select(AccessEntry).where(or_(
                    and_(AccessEntry.principal_type == "user", AccessEntry.principal_id == self.user.id),
                    and_(AccessEntry.principal_type == "role",
                         AccessEntry.principal_id.in_(self.role_ids or [-1]))))).scalars().all()
                for e in rows:
                    self._entries.setdefault((e.target_type, e.target_id), set()).update(e.permission_set)
        return self._entries

    def _folder_map(self):
        if self._folders is None:
            rows = db.session.execute(select(Folder.id, Folder.parent_id, Folder.inherit_permissions,
                                             Folder.deleted_at)).all()
            self._folders = {r.id: (r.parent_id, r.inherit_permissions, r.deleted_at) for r in rows}
        return self._folders

    def _grant(self, target_type, target_id):
        return set(self._load_entries().get((target_type, target_id), ()))

    # -- resolution ----------------------------------------------------------------------
    def repository(self):
        if self.is_admin:
            return set(ALL)
        return normalize(self._grant("repository", None))

    def folder(self, folder):
        if folder is None:
            return self.repository()
        fid = folder if isinstance(folder, int) else folder.id
        if self.is_admin:
            return set(ALL)
        key = ("folder", fid)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = set()  # cycle guard
        info = self._folder_map().get(fid)
        if info is None:
            return set()
        parent_id, inherit, _ = info
        perms = self._grant("folder", fid)
        if inherit:
            perms |= self.folder(parent_id) if parent_id else self.repository()
        result = normalize(perms)
        self._memo[key] = result
        return result

    def document(self, doc):
        if self.is_admin:
            return set(ALL)
        key = ("document", doc.id)
        if key not in self._memo:
            perms = self._grant("document", doc.id)
            if doc.inherit_permissions:
                perms |= self.folder(doc.folder_id) if doc.folder_id else self.repository()
            self._memo[key] = normalize(perms)
        return self._memo[key]

    def version(self, version):
        if self.is_admin:
            return set(ALL)
        perms = self._grant("version", version.id)
        if version.inherit_permissions:
            perms |= self.document(version.document)
        return normalize(perms)

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
        if self.is_admin:
            return true()
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
        if "view" in self.repository():
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
