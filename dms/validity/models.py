"""Tables of the validity module. All are new; no existing table or column is changed.

Foreign keys to documents, versions and folders cascade on delete, so permanently deleting
(purging) a document or folder through the existing recycle bin keeps working unchanged. Moving a
document to the recycle bin deletes nothing here: the scheduler simply skips deleted documents.
Changes are audited in the existing ``activity_log`` (append-only), not in a separate table.
"""
import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from ..models import utcnow


def _json(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class FolderValiditySetting(db.Model):
    """Explicit validity-monitoring setting of a folder. No row = the folder inherits from its parent
    (the top level defaults to disabled). See ``service.effective_folder_setting``."""
    __tablename__ = "folder_validity_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=False,
                                           unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # When monitoring was last switched on here: the reconcile sweep only queues versions created after it.
    enabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class DocumentValidity(db.Model):
    """Current validity information of one document (one row per document).

    ``version_id`` is the version the values belong to. A new version resets the row to ``pending``
    (the previous values stay in the analysis history and the audit log) until it is analysed and
    confirmed again, so a renewed certificate never inherits the old expiry date.
    """
    __tablename__ = "document_validity"
    __table_args__ = (
        Index("ix_document_validity_expiry", "expiry_date"),
        Index("ix_document_validity_status", "verification_status", "monitoring_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
                                             unique=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"))
    # Extracted / confirmed values
    document_type: Mapped[Optional[str]] = mapped_column(String(200))
    title: Mapped[Optional[str]] = mapped_column(String(500))
    document_number: Mapped[Optional[str]] = mapped_column(String(200))
    vessel_name: Mapped[Optional[str]] = mapped_column(String(200))
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(300))
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    has_expiry: Mapped[Optional[bool]] = mapped_column(Boolean)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    # pending | requires_confirmation | needs_review | verified | analysis_failed | unsupported
    verification_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    review_reasons_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(10))           # ai | manual
    analysis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document_validity_analyses.id", ondelete="SET NULL", use_alter=True,
                   name="fk_document_validity_analysis"))
    verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Document-level monitoring control: active | paused | disabled
    monitoring_state: Mapped[str] = mapped_column(String(10), default="active", nullable=False)
    paused_until: Mapped[Optional[date]] = mapped_column(Date)          # last paused day (inclusive)
    state_reason: Mapped[Optional[str]] = mapped_column(String(500))
    state_changed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    state_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    @property
    def review_reasons(self):
        try:
            value = json.loads(self.review_reasons_json or "[]")
        except ValueError:
            return []
        return value if isinstance(value, list) else []


class ValidityAnalysis(db.Model):
    """One AI analysis attempt result for one version (history is never overwritten)."""
    __tablename__ = "document_validity_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
                                             index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"),
                                                      index=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # succeeded | invalid_response | failed | unsupported | reused
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(80))
    # Validated, normalised extraction (never the raw model output or document text)
    result_json: Mapped[Optional[str]] = mapped_column(Text)
    verification_status: Mapped[Optional[str]] = mapped_column(String(30))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    error: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    @property
    def result(self):
        return _json(self.result_json)


class ValidityJob(db.Model):
    """Durable analysis queue. One job per version (unique), so a version is never analysed twice.

    Claiming is an atomic conditional UPDATE (status + lease), so several workers can run safely and
    a job whose worker crashed is taken over once its lease has expired.
    """
    __tablename__ = "document_validity_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_validity_job_version"),
        Index("ix_validity_jobs_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
                                             index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    locked_by: Mapped[Optional[str]] = mapped_column(String(80))
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(String(500))
    trigger: Mapped[str] = mapped_column(String(20), default="upload", nullable=False)  # upload|manual|sweep|enable
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ValidityException(db.Model):
    """"Expired but still accepted by the port/authority". Never changes the expiry date; it only
    suppresses expired-document notifications while it applies. Withdrawn rows are kept."""
    __tablename__ = "document_validity_exceptions"
    __table_args__ = (Index("ix_validity_exceptions_doc", "document_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"))
    scope: Mapped[str] = mapped_column(String(10), nullable=False)      # port | all
    port_name: Mapped[Optional[str]] = mapped_column(String(200))
    authority: Mapped[Optional[str]] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reference_number: Mapped[Optional[str]] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date)              # NULL = indefinite (justified)
    indefinite_justification: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)  # active | withdrawn
    # Supporting evidence: stored encrypted with the existing storage, served only to users who may
    # download the document.
    evidence_storage_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    evidence_filename: Mapped[Optional[str]] = mapped_column(String(255))
    evidence_mime: Mapped[Optional[str]] = mapped_column(String(127))
    evidence_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    evidence_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    withdrawn_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    withdrawn_reason: Mapped[Optional[str]] = mapped_column(String(500))


class ValiditySubscription(db.Model):
    """Who receives a document's expiry alerts. The owner receives them unless they opt out
    (subscribed=False); anyone who may view the document can opt in."""
    __tablename__ = "document_validity_subscriptions"
    __table_args__ = (UniqueConstraint("document_id", "user_id", name="uq_validity_subscription"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ValiditySchedulerRun(db.Model):
    """One row per business day: claimed atomically, so only one instance runs a day's check."""
    __tablename__ = "validity_scheduler_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)     # running | completed | failed
    worker_id: Mapped[Optional[str]] = mapped_column(String(80))
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    documents_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notifications_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resumed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_text: Mapped[Optional[str]] = mapped_column(String(1000))


# --------------------------------------------------------------------------- notification centre

class Notification(db.Model):
    """In-app notification (generic: ``category`` names the module). ``dedupe_key`` is unique per
    user, which makes producers idempotent at the database level (e.g. one expiry notice per
    document, user and day, however many scheduler instances run)."""
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),
        Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),
        Index("ix_notifications_email", "email_status", "email_next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)    # validity | ...
    kind: Mapped[str] = mapped_column(String(30), nullable=False)        # countdown | expires_today | expired
    severity: Mapped[str] = mapped_column(String(10), default="info", nullable=False)  # info | warning | danger
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(300))             # in-app path, e.g. /documents/5
    entity_type: Mapped[Optional[str]] = mapped_column(String(20))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    details_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Email delivery: none (not emailed) | pending | sent | failed | skipped
    email_status: Mapped[str] = mapped_column(String(10), default="none", nullable=False)
    email_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    email_error: Mapped[Optional[str]] = mapped_column(String(300))

    @property
    def details(self):
        return _json(self.details_json)


class NotificationPreference(db.Model):
    """Per-user email preference. In-app notifications are always daily."""
    __tablename__ = "notification_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # off | milestones (one digest on milestone days) | daily (one digest every day with alerts)
    email_mode: Mapped[str] = mapped_column(String(12), default="milestones", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
