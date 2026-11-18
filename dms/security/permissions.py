"""Role + ownership + per-item sharing (DMS Permissions Matrix v2, FR-SEC-01, FR-SEC-02, FR-FLD-02).

Effective permissions on a folder, document or version are worked out in this order:

1. Inactive user: nothing.
2. Administrator: everything, on every item. The policy layer (security/policy.py) writes an audit
   entry when an administrator uses access they would not have as an ordinary user, and enforces
   legal hold (which blocks delete, purge and move even for administrators).
3. Owner (``owner_id``: whoever created the folder or uploaded the file, until an administrator
   reassigns it): everything.
4. Share grants (AccessEntry rows) to the user or a role they hold, on the item or a folder above it
   (inherited, including items added later). A non-owner never gets more than the share levels allow:
   view, download, upload (legacy "add files") and edit. Delete, share and manage are owner/Admin
   only, so a legacy "Full control" grant to someone else now works as Edit.
5. Items someone else put into your folder: as folder owner you get View and Download on them
   (Permissions Matrix section 4), not edit, rename or delete.

Item-level access: a grant made to a user personally on a folder, document or version is that user's
exact access to the item and everything below it that inherits (it replaces role grants and inherited
access). Expiry: a grant with ``expires_at`` in the past is ignored everywhere.

Groups: departments and teams are roles (no separate groups table); a share to a role gives every
member that access. Repository-wide grants (target ``repository``) are used for creating top-level
folders.
"""
from flask import g, has_request_context
from sqlalchemy import and_, false, or_, select, true

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
    "records": "Records management (check-out override; retention and legal hold are Admin only)",
    "audit": "Audit (legacy; every user now sees their own activity log only)",
}

# name -> (system capabilities, repository-level content permissions, description)
PREDEFINED_ROLES = {
    "Admin": ({"admin", "records", "audit"}, set(CONTENT_PERMS),
              "Full control of the system, users, roles and settings."),
    "Records Manager": ({"records", "audit"}, set(CONTENT_PERMS),
                        "Creates top-level folders and releases other users' check-outs."),
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
# The most a non-owner can get from shares (Permissions Matrix section 2).
SHARE_PERMS = frozenset({"view", "download", "upload", "edit"})
# Folder owner's automatic access to items other people put into their folder (section 4).
FOLDER_OWNER_PERMS = frozenset({"view", "download"})


def normalize(perms):
    perms = {p for p in perms if p in CONTENT_PERMS}
    if "manage" in perms:
        return set(ALL)
    if "edit" in perms:
        perms.add("upload")
    if perms:
        perms.add("view")
    return perms


def cap(perms):
    """What share grants can give someone who neither owns the item nor is an administrator."""
    return normalize(perms) & SHARE_PERMS


def active_clause():
    """SQL condition for grants that have not expired."""
    return or_(AccessEntry.expires_at.is_(None), AccessEntry.expires_at > utcnow())


def parse_perms(values):
    return ",".join(p for p in CONTENT_PERMS if p in set(values))


class Resolver:
    """Computes a user's effective permissions. One instance per request (cached).

    ``honour_admin=False`` resolves an administrator like an ordinary user; the policy layer uses it to
    tell when an administrator is overriding (and must be audited)."""

    def __init__(self, user, honour_admin=True):
        self.user = user
        self.active = bool(user is not None and user.is_active)
        self.uid = user.id if user is not None else None
        self.is_admin = bool(self.active and honour_admin and user.is_admin)
        self.role_ids = {r.id for r in user.roles} if user else set()
        self._entries = None
        self._folders = None
        self._memo = {}

    # -- data ----------------------------------------------------------------------
    def _load_entries(self):
        if self._entries is None:
            self._entries = {}
            if self.active:
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
                                             Folder.deleted_at, Folder.owner_id)).all()
            self._folders = {r.id: (r.parent_id, r.inherit_permissions, r.deleted_at, r.owner_id) for r in rows}
        return self._folders

    def _grant(self, target_type, target_id):
        """Everything granted on the node to the user or their roles (no inheritance)."""
        slot = self._load_entries().get((target_type, target_id))
        if slot is None:
            return set()
        return set(slot["role"]) | set(slot["user"] or ())

    def _resolve(self, target_type, target_id, inherit, inherited):
        """Share permissions on one node. A non-empty personal grant on the node is the user's exact
        access there (item-level access); otherwise role grants on the node plus, when the node
        inherits, what comes from its parent (``inherited()``)."""
        slot = self._load_entries().get((target_type, target_id))
        if slot is not None and slot["user"]:
            return cap(slot["user"])
        perms = set(slot["role"]) if slot is not None else set()
        if inherit:
            perms |= inherited()
        return cap(perms)

    # -- ownership -------------------------------------------------------------------
    def owns_folder(self, folder_id):
        info = self._folder_map().get(folder_id)
        return bool(self.uid is not None and info is not None and info[3] == self.uid)

    def owns(self, obj):
        """True when the user owns ``obj`` (a Folder, Document or DocumentVersion)."""
        if self.uid is None or obj is None:
            return False
        if isinstance(obj, DocumentVersion):
            obj = obj.document
        return obj.owner_id == self.uid

    def _inherit_from_folder(self, folder_id):
        """What an item inside ``folder_id`` inherits. Inside your own folder you get View and Download
        on what other people put there (section 4); the item's own owner is handled before this."""
        if folder_id is None:
            return self._from_root()
        if self.owns_folder(folder_id):
            return set(FOLDER_OWNER_PERMS)
        return self.folder(folder_id)

    # -- resolution ----------------------------------------------------------------------
    def repository(self):
        if not self.active:
            return set()
        if self.is_admin:
            return set(ALL)
        return normalize(self._grant("repository", None))

    def _from_root(self):
        """What top-level folders and root documents inherit from the repository."""
        return self.repository()

    def folder(self, folder):
        if folder is None:
            return self.repository()
        if not self.active:
            return set()
        fid = folder if isinstance(folder, int) else folder.id
        key = ("folder", fid)
        if key in self._memo:
            return self._memo[key]
        info = self._folder_map().get(fid)
        if info is None:
            return set()
        if self.is_admin or info[3] == self.uid:
            self._memo[key] = set(ALL)
            return self._memo[key]
        self._memo[key] = set()  # cycle guard
        parent_id, inherit = info[0], info[1]
        result = self._resolve("folder", fid, inherit, lambda: self._inherit_from_folder(parent_id))
        self._memo[key] = result
        return result

    def document(self, doc):
        if not self.active:
            return set()
        if self.is_admin or self.owns(doc):
            return set(ALL)
        key = ("document", doc.id)
        if key not in self._memo:
            self._memo[key] = self._resolve(
                "document", doc.id, doc.inherit_permissions,
                lambda: self._inherit_from_folder(doc.folder_id))
        return self._memo[key]

    def version(self, version):
        if not self.active:
            return set()
        if self.is_admin or self.owns(version.document):
            return set(ALL)
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
        return self.active and self.user.has(capability)

    # -- security trimming -----------------------------------------------------------
    def visible_folder_ids(self):
        return {fid for fid, info in self._folder_map().items()
                if info[2] is None and "view" in self.folder(fid)}

    def visible_documents_clause(self):
        """SQL condition limiting a Document query to documents the user may view."""
        if not self.active:
            return false()
        if self.is_admin:
            return true()
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
        clauses = [Document.owner_id == self.uid]
        if in_folders:
            scope = or_(*in_folders)
            clauses.append(and_(Document.id.not_in(special), scope) if special else scope)
        if special_visible:
            clauses.append(Document.id.in_(special_visible))
        return or_(*clauses)


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
