"""Database models."""
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


def _csv(value):
    return [p for p in (value or "").split(",") if p]


# --------------------------------------------------------------------------- users, roles, access

user_roles = Table(
    "user_roles", db.Model.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(db.Model):
    """A named set of system capabilities. Content permissions for a role are AccessEntry rows."""
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Comma-separated system capabilities: admin, records, audit, emails
    capabilities: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")

    @property
    def capability_set(self):
        return set(_csv(self.capabilities))


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)  # stored lower-case
    email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    auth_source: Mapped[str] = mapped_column(String(20), default="local", nullable=False)  # local|ldap|oidc|saml
    external_id: Mapped[Optional[str]] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(500))  # encrypted with the storage key ring
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_recovery_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # hashed codes
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Personal "My Files" folder: private to the user until they share something from it.
    home_folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL",
                                                                     use_alter=True, name="fk_users_home_folder"))

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, back_populates="users", order_by="Role.name")

    @property
    def label(self):
        return self.display_name or self.username

    @property
    def capabilities(self):
        caps = set()
        for role in self.roles:
            caps |= role.capability_set
        return caps

    @property
    def is_admin(self):
        return "admin" in self.capabilities

    def has(self, capability):
        caps = self.capabilities
        return "admin" in caps or capability in caps


class AccessEntry(db.Model):
    """Grants a set of permissions on a target to a user or a role.

    target_type: repository (target_id NULL) | folder | document | version
    principal_type: user | role
    """
    __tablename__ = "access_entries"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", "principal_type", "principal_id", name="uq_access_entry"),
        Index("ix_access_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[Optional[int]] = mapped_column(Integer)
    principal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    principal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    permissions: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def permission_set(self):
        return set(_csv(self.permissions))


class ApiToken(db.Model):
    """Personal access token for the REST API (desktop sync client, mobile app, integrations)."""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class UserDevice(db.Model):
    """Browsers/devices a user has signed in from (device-based access policy)."""
    __tablename__ = "user_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_hash", name="uq_user_device"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), default="other", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="approved", nullable=False)  # approved|pending|blocked
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    user: Mapped[User] = relationship()


class SystemSetting(db.Model):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------- folders

class Folder(TimestampMixin, db.Model):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id"), index=True)
    inherit_permissions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    deleted_batch: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    deleted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Version-retention override for documents in this folder (NULL = inherit / system setting)
    version_keep_last: Mapped[Optional[int]] = mapped_column(Integer)
    version_keep_years: Mapped[Optional[int]] = mapped_column(Integer)

    parent: Mapped[Optional["Folder"]] = relationship(remote_side="Folder.id", back_populates="children")
    children: Mapped[list["Folder"]] = relationship(back_populates="parent", order_by="Folder.name")
    documents: Mapped[list["Document"]] = relationship(back_populates="folder")
    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def live_children(self):
        return [c for c in self.children if c.deleted_at is None]

    def ancestors(self):
        """Folders from the root down to (excluding) this one."""
        chain, node, seen = [], self.parent, set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    @property
    def path(self):
        return " / ".join([f.name for f in self.ancestors()] + [self.name])


class FolderActivity(db.Model):
    """Structural history of a folder: renames, moves and membership changes."""
    __tablename__ = "folder_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), index=True)
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)  # snapshot at event time
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)

    @property
    def details(self):
        try:
            return json.loads(self.details_json or "{}")
        except ValueError:
            return {}


# --------------------------------------------------------------------------- metadata

class DocumentType(db.Model):
    __tablename__ = "document_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class Vocabulary(db.Model):
    """A controlled list of values used by choice fields (and optionally tags)."""
    __tablename__ = "vocabularies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    terms: Mapped[list["VocabularyTerm"]] = relationship(
        back_populates="vocabulary", cascade="all, delete-orphan", order_by="VocabularyTerm.value")


class VocabularyTerm(db.Model):
    __tablename__ = "vocabulary_terms"
    __table_args__ = (UniqueConstraint("vocabulary_id", "value", name="uq_vocabulary_term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vocabulary_id: Mapped[int] = mapped_column(ForeignKey("vocabularies.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    vocabulary: Mapped[Vocabulary] = relationship(back_populates="terms")


class MetadataField(db.Model):
    """A custom field. Applies to every document when neither scope is set, to a document type,
    to a folder (and its subfolders), or to both (type within folder)."""
    __tablename__ = "metadata_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text|number|date|choice|boolean
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    help_text: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vocabulary_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vocabularies.id", ondelete="SET NULL"))
    document_type_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_types.id", ondelete="CASCADE"))
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))

    vocabulary: Mapped[Optional[Vocabulary]] = relationship()
    document_type: Mapped[Optional[DocumentType]] = relationship()
    folder: Mapped[Optional[Folder]] = relationship()


class MetadataValue(db.Model):
    __tablename__ = "metadata_values"
    __table_args__ = (UniqueConstraint("document_id", "field_id", name="uq_metadata_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("metadata_fields.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)

    field: Mapped[MetadataField] = relationship()


class Tag(db.Model):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)


document_tags = Table(
    "document_tags", db.Model.metadata,
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


# --------------------------------------------------------------------------- documents

class Document(TimestampMixin, db.Model):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id"), index=True)
    current_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_current_version"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    deleted_batch: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    deleted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    document_type_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_types.id", ondelete="SET NULL"))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    inherit_permissions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    checked_out_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    checked_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Optimistic locking: every ORM UPDATE of a document bumps this and only applies if nobody else
    # changed the row since it was read (otherwise StaleDataError -> HTTP 409). Raw SQL / stored
    # procedures that update documents bypass it, so document writes go through the ORM.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": row_version}

    folder: Mapped[Optional[Folder]] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", foreign_keys="DocumentVersion.document_id",
        order_by="DocumentVersion.version_number.desc()", cascade="all, delete-orphan")
    current_version: Mapped[Optional["DocumentVersion"]] = relationship(
        foreign_keys=[current_version_id], post_update=True)
    document_type: Mapped[Optional[DocumentType]] = relationship()
    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])
    checked_out_by: Mapped[Optional[User]] = relationship(foreign_keys=[checked_out_by_id])
    tags: Mapped[list[Tag]] = relationship(secondary=document_tags, order_by="Tag.name")
    metadata_values: Mapped[list[MetadataValue]] = relationship(cascade="all, delete-orphan")
    links: Mapped[list["DocumentLink"]] = relationship(back_populates="document", cascade="all, delete-orphan")

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def is_checked_out(self):
        return self.checked_out_by_id is not None


class DocumentVersion(db.Model):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number", name="uq_document_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    # Sequential internal number (used in URLs); major/minor is the user-facing label.
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    major: Mapped[Optional[int]] = mapped_column(Integer)
    minor: Mapped[Optional[int]] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(500))
    restored_from: Mapped[Optional[int]] = mapped_column(Integer)  # version number restored
    # Client-generated token; a retried request with the same key returns the same version.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    inherit_permissions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    scan_status: Mapped[str] = mapped_column(String(20), default="clean", nullable=False, server_default="clean")
    scan_engine: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    document: Mapped[Document] = relationship(back_populates="versions", foreign_keys=[document_id])
    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])

    @property
    def label(self):
        if self.major is None:
            return f"{self.version_number}.0"
        return f"{self.major}.{self.minor or 0}"

    @property
    def is_major(self):
        return (self.minor or 0) == 0


class DocumentLink(db.Model):
    """A shortcut: the document also appears in another folder."""
    __tablename__ = "document_links"
    __table_args__ = (UniqueConstraint("document_id", "folder_id", name="uq_document_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    document: Mapped[Document] = relationship(back_populates="links")
    folder: Mapped[Optional[Folder]] = relationship()


class Favourite(db.Model):
    __tablename__ = "favourites"
    __table_args__ = (UniqueConstraint("user_id", "target_type", "target_id", name="uq_favourite"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)  # folder|document
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RecentItem(db.Model):
    """Documents a user recently opened."""
    __tablename__ = "recent_items"
    __table_args__ = (UniqueConstraint("user_id", "document_id", name="uq_recent_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)

    document: Mapped[Document] = relationship()


# --------------------------------------------------------------------------- search

class SearchEntry(db.Model):
    """Extracted, searchable text for a document or an archived email (MySQL FULLTEXT index ft_search)."""
    __tablename__ = "search_entries"
    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_search_entry"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # document|email
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    meta: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------- activity

class ActivityLog(db.Model):
    """Audit log. Append-only: database triggers (database/procedures.sql, installed on start-up)
    reject UPDATE and DELETE, and the ORM refuses to flush changes to existing rows."""
    __tablename__ = "activity_log"
    __table_args__ = (Index("ix_activity_log_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # document|folder|email|gmail|security|admin
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    # What entity_id refers to: document|folder|version|repository|user|role|email|setting (NULL on old rows)
    entity_type: Mapped[Optional[str]] = mapped_column(String(20))
    entity_name: Mapped[Optional[str]] = mapped_column(String(500))  # snapshot at event time
    # JSON: {"changes": {field: {"old": .., "new": ..}}, ...other context}
    details_json: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(200))
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)

    @property
    def details(self):
        try:
            value = json.loads(self.details_json or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- gmail

class GmailAccount(db.Model):
    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # DMS user who connected the mailbox; only they can see its archived emails.
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # OAuth token JSON, encrypted with the master key (security.mfa.seal).
    token_enc: Mapped[Optional[str]] = mapped_column(Text)
    history_id: Mapped[Optional[str]] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_full_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_sync_status: Mapped[str] = mapped_column(String(30), default="never", nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000))
    # Cross-process sync lease: a sync may run only while it holds an unexpired lease.
    lock_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    legacy_imported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ArchivedEmail(db.Model):
    __tablename__ = "archived_emails"
    __table_args__ = (
        UniqueConstraint("account_id", "gmail_message_id", name="uq_account_message"),
        Index("ix_archived_emails_received", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False, index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    message_id_header: Mapped[Optional[str]] = mapped_column(String(998))
    subject: Mapped[str] = mapped_column(String(998), default="", nullable=False)
    sender: Mapped[str] = mapped_column(String(998), default="", nullable=False)
    recipients: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cc: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snippet: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="sync", nullable=False)  # sync|backfill|legacy
    # Current state of the message in Gmail: archived|inbox|trashed|spam|deleted
    gmail_state: Mapped[str] = mapped_column(String(20), default="archived", nullable=False, index=True)
    label_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Malware scan of the raw message: clean|infected|error|unscanned
    scan_status: Mapped[str] = mapped_column(String(20), default="unscanned", nullable=False,
                                             server_default="unscanned")
    scan_detail: Mapped[Optional[str]] = mapped_column(String(300))
    # When the images the body links to were downloaded (NULL = not yet tried). See EmailImage.
    images_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    account: Mapped[GmailAccount] = relationship()
    attachments: Mapped[list["EmailAttachment"]] = relationship(
        back_populates="email", cascade="all, delete-orphan", order_by="EmailAttachment.part_index")


class EmailAttachment(db.Model):
    __tablename__ = "email_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("archived_emails.id"), nullable=False, index=True)
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)  # index in message.walk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_id: Mapped[Optional[str]] = mapped_column(String(255))
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    email: Mapped[ArchivedEmail] = relationship(back_populates="attachments")


class EmailImage(db.Model):
    """A copy of an image an archived email links to (``<img src="https://...">``), downloaded once and
    stored encrypted with the email so the preview never loads it from the internet. The .eml is unchanged."""
    __tablename__ = "email_images"
    __table_args__ = (UniqueConstraint("email_id", "url_hash", name="uq_email_image"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("archived_emails.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # sha256 of url
    status: Mapped[str] = mapped_column(String(10), nullable=False)      # stored | failed
    storage_key: Mapped[Optional[str]] = mapped_column(String(255))
    content_type: Mapped[Optional[str]] = mapped_column(String(40))
    size: Mapped[Optional[int]] = mapped_column(BigInteger)
    error: Mapped[Optional[str]] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PendingImport(db.Model):
    """Durable queue of Gmail messages detected as archived but not yet imported."""
    __tablename__ = "pending_imports"
    __table_args__ = (UniqueConstraint("account_id", "gmail_message_id", name="uq_pending_message"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False, index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="sync", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
