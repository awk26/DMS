"""Durable AI-analysis queue (``document_validity_jobs``).

Enqueueing
    * After every commit that created document versions (any path: upload, new version, check-in,
      restore, copy, email-attachment import), a session hook queues the new *current* versions of
      documents in monitored folders. It runs after the upload has committed, on its own connection and
      inside try/except, so an upload can never fail because of this module or of Gemini.
    * ``reconcile`` (run by the worker) re-queues anything the hook missed (e.g. a process crash right
      after the commit), limited to versions created after monitoring was switched on.
    * Users can (re)queue the current version by hand; enabling a folder can queue existing documents.
    One job per version (unique ``version_id``): the same version is never analysed twice by accident.

Processing
    ``claim_next`` claims one due job with an atomic conditional UPDATE and a lease; a crashed worker's
    job is taken over when its lease expires. Transient failures (timeout, rate limit, network, invalid
    JSON) are retried with exponential backoff up to VALIDITY_JOB_MAX_ATTEMPTS; unusable files fail at
    once. The worker never holds a database transaction open while waiting for the AI service.
"""
import io
import logging
import os
import socket
import threading
from datetime import timedelta

from flask import current_app, has_app_context
from sqlalchemy import and_, event, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from ..extensions import db
from ..models import Document, DocumentVersion, Folder, utcnow
from ..services.activity import log_activity
from .models import DocumentValidity, FolderValiditySetting, ValidityAnalysis, ValidityJob
from .rules import InvalidResponse, backoff_seconds, validate_extraction

log = logging.getLogger(__name__)

GEMINI_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
                 "heic": "image/heic", "heif": "image/heif"}
CONVERT_IMAGES = {"gif", "bmp", "tif", "tiff"}
TEXT_LIMIT = 200_000


class UnsupportedDocument(Exception):
    """The file cannot be analysed (type, corruption, encryption, size). Not retried."""


def worker_id():
    return f"{socket.gethostname()[:40]}:{os.getpid()}:{threading.get_ident() % 100000}"


def _cfg(name, default):
    return current_app.config.get(name, default) if has_app_context() else default


# --------------------------------------------------------------------------- enqueueing

def _resolve(folder_id, smap, parents):
    from .service import resolve_folder
    return resolve_folder(folder_id, smap, parents)


def enqueue_versions(version_ids, trigger="upload", requested_by=None, conn=None):
    """Queue analysis jobs for ``version_ids`` that are the current version of a live document in a
    monitored folder whose document-level monitoring is not disabled. Idempotent. Returns the number
    of new jobs. ``conn``: a Core connection (the session hook uses its own); None = the ORM session."""
    ids = [int(v) for v in version_ids if v]
    if not ids or not _cfg("VALIDITY_MONITORING_ENABLED", True):
        return 0
    execute = conn.execute if conn is not None else db.session.execute
    smap = {r.folder_id: r for r in execute(select(
        FolderValiditySetting.folder_id, FolderValiditySetting.enabled, FolderValiditySetting.enabled_at))}
    if not any(r.enabled for r in smap.values()):
        return 0
    parents = {r.id: (r.parent_id, r.deleted_at) for r in
               execute(select(Folder.id, Folder.parent_id, Folder.deleted_at))}
    rows = execute(select(DocumentVersion.id, DocumentVersion.document_id, DocumentVersion.created_by_id,
                          Document.folder_id, Document.current_version_id, Document.deleted_at)
                   .join(Document, Document.id == DocumentVersion.document_id)
                   .where(DocumentVersion.id.in_(ids))).all()
    now, created = utcnow(), 0
    # DATETIME columns round to whole seconds: "due now" is stored a second early so it is never in the future.
    due = now - timedelta(seconds=1)
    max_attempts = max(1, int(_cfg("VALIDITY_JOB_MAX_ATTEMPTS", 5)))
    for r in rows:
        if r.deleted_at is not None or r.current_version_id != r.id or r.folder_id is None:
            continue
        if not _resolve(r.folder_id, smap, parents)[0]:
            continue
        state = execute(select(DocumentValidity.monitoring_state).where(
            DocumentValidity.document_id == r.document_id)).scalar()
        if state == "disabled":
            continue
        execute(mysql_insert(DocumentValidity.__table__).values(
            document_id=r.document_id, verification_status="pending", monitoring_state="active",
            review_reasons_json="[]", created_at=now, updated_at=now).prefix_with("IGNORE"))
        result = execute(mysql_insert(ValidityJob.__table__).values(
            document_id=r.document_id, version_id=r.id, status="queued", attempts=0, max_attempts=max_attempts,
            next_attempt_at=due, trigger=trigger,
            requested_by_id=requested_by.id if requested_by is not None else r.created_by_id,
            created_at=now, updated_at=now).prefix_with("IGNORE"))
        # INSERT IGNORE: 0 rows for an existing job (ON DUPLICATE KEY UPDATE would report 1 under the
        # CLIENT_FOUND_ROWS flag SQLAlchemy sets for PyMySQL).
        created += 1 if result.rowcount == 1 else 0
    return created


def requeue_current(doc, user):
    """Queue (or re-run) the analysis of ``doc``'s current version on the user's request. Commits."""
    from .service import effective_folder_setting
    from ..validation import ValidationError
    if not effective_folder_setting(doc.folder)[0]:
        raise ValidationError("Validity monitoring is not enabled for this document's folder.")
    if doc.current_version_id is None:
        raise ValidationError("This document has no current version.")
    job = db.session.execute(select(ValidityJob).where(
        ValidityJob.version_id == doc.current_version_id)).scalar_one_or_none()
    if job is not None and job.status == "running" and job.locked_until and job.locked_until > utcnow():
        raise ValidationError("The analysis of this version is already running.")
    if job is None:
        job = ValidityJob(document_id=doc.id, version_id=doc.current_version_id, trigger="manual",
                          max_attempts=max(1, int(_cfg("VALIDITY_JOB_MAX_ATTEMPTS", 5))))
        db.session.add(job)
    job.status, job.attempts, job.next_attempt_at = "queued", 0, utcnow() - timedelta(seconds=1)
    job.locked_by = job.locked_until = job.finished_at = job.last_error = None
    job.trigger, job.requested_by_id = "manual", user.id
    from .service import ensure_record
    record = ensure_record(doc)
    if record.monitoring_state == "disabled":
        raise ValidationError("Expiry monitoring is disabled for this document; resume it first.")
    log_activity("document", "validity_analysis_requested", f"AI validity analysis requested for '{doc.name}'",
                 doc.id, entity_name=doc.name, details={"version_id": doc.current_version_id})
    db.session.commit()
    return job


def enqueue_folder_documents(folder, user):
    """Queue current versions of live documents in ``folder`` and below that have no job yet (used when
    monitoring is switched on with "also analyse existing documents"). Does not commit."""
    from ..services.folders import subtree
    ids = [f.id for f in subtree(folder)]
    rows = db.session.execute(select(Document.current_version_id).where(
        Document.folder_id.in_(ids), Document.deleted_at.is_(None), Document.current_version_id.is_not(None),
        ~select(ValidityJob.id).where(ValidityJob.version_id == Document.current_version_id).exists())).scalars()
    return enqueue_versions(list(rows), trigger="enable", requested_by=user)


def reconcile(limit=200):
    """Queue current versions the upload hook missed. Only versions created after monitoring was
    switched on for their folder are considered (enabling never silently analyses old documents)."""
    from .service import enabled_folders
    enabled = enabled_folders()
    if not enabled:
        return 0
    rows = db.session.execute(
        select(DocumentVersion.id, DocumentVersion.created_at, Document.folder_id)
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .where(Document.deleted_at.is_(None), Document.folder_id.in_(list(enabled)),
               ~select(ValidityJob.id).where(ValidityJob.version_id == DocumentVersion.id).exists())
        .order_by(DocumentVersion.id.desc()).limit(limit)).all()
    due = [r.id for r in rows if enabled.get(r.folder_id) is None or r.created_at >= enabled[r.folder_id]]
    if not due:
        return 0
    n = enqueue_versions(due, trigger="sweep")
    db.session.commit()
    return n


# ----------------------------------------------------------------- session hook (after upload commits)

_KEY = "validity_new_versions"


def _after_flush(session, _ctx):
    new = [obj.id for obj in session.new if isinstance(obj, DocumentVersion) and obj.id is not None]
    if new:
        session.info.setdefault(_KEY, set()).update(new)


def _after_commit(session):
    ids = session.info.pop(_KEY, None)
    if not ids or not has_app_context() or not current_app.config.get("VALIDITY_MONITORING_ENABLED", True):
        return
    try:
        with db.engine.begin() as conn:
            n = enqueue_versions(ids, trigger="upload", conn=conn)
        if n:
            wake_worker()
    except Exception:  # never let this module affect the upload that just committed
        log.exception("Could not queue validity analysis; the reconcile sweep will retry")


def _after_rollback(session):
    session.info.pop(_KEY, None)


_hooks_installed = False


def install_hooks():
    global _hooks_installed
    if _hooks_installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _hooks_installed = True


def wake_worker():
    sched = current_app.extensions.get("validity_worker") if has_app_context() else None
    if sched is not None:
        sched.trigger()


# --------------------------------------------------------------------------- claiming

def _due_clause(now):
    return or_(and_(ValidityJob.status == "queued", ValidityJob.next_attempt_at <= now),
               and_(ValidityJob.status == "running", ValidityJob.locked_until < now))


def claim_next(owner, lease_seconds=None):
    """Atomically claim one due job (or a job whose worker's lease expired). Returns its id or None."""
    lease = timedelta(seconds=lease_seconds or int(_cfg("VALIDITY_JOB_LEASE_SECONDS", 600)))
    now = utcnow()
    with db.engine.begin() as conn:
        ids = conn.execute(select(ValidityJob.id).where(_due_clause(now))
                           .order_by(ValidityJob.next_attempt_at, ValidityJob.id).limit(10)).scalars().all()
    for job_id in ids:
        with db.engine.begin() as conn:
            claimed = conn.execute(update(ValidityJob).where(ValidityJob.id == job_id, _due_clause(now)).values(
                status="running", locked_by=owner, locked_until=now + lease,
                attempts=ValidityJob.attempts + 1, updated_at=now)).rowcount
        if claimed == 1:
            return job_id
    return None


# --------------------------------------------------------------------------- processing

def prepare_input(doc, version):
    """What to send to the AI: ``{"data", "mime_type"}`` for PDFs and images, ``{"text"}`` for other
    formats DMS can read (Office, OpenDocument, text). Raises UnsupportedDocument."""
    from ..services.text_extract import extract_bytes
    from ..storage import get_storage
    max_bytes = max(1, int(_cfg("GEMINI_MAX_INLINE_MB", 18))) * 1024 * 1024
    try:
        data = get_storage().read_bytes(version.storage_key)
    except FileNotFoundError:
        raise UnsupportedDocument("The stored file is missing.") from None
    if not data:
        raise UnsupportedDocument("The file is empty.")
    ext = (doc.extension or "").lower()
    if ext == "pdf":
        if not data.lstrip()[:5] == b"%PDF-":
            raise UnsupportedDocument("The file is not a valid PDF.")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise UnsupportedDocument("The PDF is password-protected.")
            if len(reader.pages) == 0:
                raise UnsupportedDocument("The PDF has no pages.")
        except UnsupportedDocument:
            raise
        except Exception:
            raise UnsupportedDocument("The PDF is damaged and cannot be read.") from None
        if len(data) <= max_bytes:
            return {"data": data, "mime_type": "application/pdf"}
        text = extract_bytes(data, "pdf")
        if text.strip():
            return {"text": text[:TEXT_LIMIT]}
        raise UnsupportedDocument("The PDF is too large to analyse.")
    if ext in GEMINI_IMAGES or ext in CONVERT_IMAGES:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            if ext in CONVERT_IMAGES:
                with Image.open(io.BytesIO(data)) as img:
                    out = io.BytesIO()
                    img.convert("RGB").save(out, "PNG")
                    data, mime = out.getvalue(), "image/png"
            else:
                mime = GEMINI_IMAGES[ext]
        except Exception:
            raise UnsupportedDocument("The image is damaged or in an unsupported format.") from None
        if len(data) > max_bytes:
            raise UnsupportedDocument("The image is too large to analyse.")
        return {"data": data, "mime_type": mime}
    text = extract_bytes(data, ext)
    if text.strip():
        return {"text": text[:TEXT_LIMIT]}
    raise UnsupportedDocument(f"Validity analysis does not support .{ext or '?'} files without readable text.")


def _requester_can_view(job, doc, version):
    """Never send a document the requesting user may not access (checked as an ordinary user)."""
    from ..models import User
    from .access import can
    uid = job.requested_by_id or version.created_by_id or doc.owner_id
    user = db.session.get(User, uid) if uid else None
    return user is not None and user.is_active and can("view", doc, user)


def _finish(job_id, owner, values):
    """Update the job only while this worker still holds it (fencing against a lost lease)."""
    return db.session.execute(update(ValidityJob).where(
        ValidityJob.id == job_id, ValidityJob.locked_by == owner, ValidityJob.status == "running"
    ).values(updated_at=utcnow(), **values)).rowcount == 1


def _apply_result(doc, version, analysis, result, status, reasons):
    from .service import ensure_record
    record = ensure_record(doc)
    if doc.current_version_id != version.id:
        return  # superseded meanwhile: keep the analysis in history only
    record.version_id = version.id
    record.document_type = result.get("document_type")
    record.title = result.get("document_title")
    record.document_number = result.get("document_number")
    record.vessel_name = result.get("vessel_name")
    record.issuing_authority = result.get("issuing_authority")
    from .rules import parse_iso_date
    record.issue_date = parse_iso_date(result.get("issue_date") or "")
    record.expiry_date = parse_iso_date(result.get("expiry_date") or "")
    record.has_expiry = result.get("has_expiry")
    record.confidence = result.get("confidence")
    record.verification_status = status
    import json
    record.review_reasons_json = json.dumps(reasons)
    record.source = "ai"
    record.analysis_id = analysis.id
    record.verified_by_id = None
    record.verified_at = None


def _set_record_status(doc, version, status):
    from .service import ensure_record
    record = ensure_record(doc)
    if doc.current_version_id == version.id:
        record.version_id = version.id
        record.verification_status = status
        record.verified_by_id = None
        record.verified_at = None


def process_job(job_id, owner):
    """Run one claimed job. Returns its final status for this attempt."""
    import json
    job = db.session.get(ValidityJob, job_id)
    if job is None or job.locked_by != owner:
        return "lost"
    doc = db.session.get(Document, job.document_id)
    version = db.session.get(DocumentVersion, job.version_id)
    from .service import effective_folder_setting, get_record
    cancel = None
    if doc is None or version is None or doc.deleted_at is not None:
        cancel = "The document was deleted."
    elif doc.current_version_id != version.id:
        cancel = "A newer version replaced this one."
    elif not effective_folder_setting(doc.folder)[0]:
        cancel = "Validity monitoring is no longer enabled for the folder."
    elif (get_record(doc.id) or DocumentValidity(monitoring_state="active")).monitoring_state == "disabled":
        cancel = "Monitoring is disabled for this document."
    elif version.scan_status != "clean":
        cancel = "The file did not pass the malware scan."
    elif not _requester_can_view(job, doc, version):
        cancel = "The requesting user no longer has access to the document."
    if cancel:
        _finish(job_id, owner, {"status": "cancelled", "last_error": cancel, "finished_at": utcnow(),
                                "locked_until": None})
        db.session.commit()
        return "cancelled"

    def record_analysis(status, result=None, verification=None, error=None, model=None):
        analysis = ValidityAnalysis(document_id=doc.id, version_id=version.id, job_id=job_id, sha256=version.sha256,
                                    status=status, model=model, verification_status=verification,
                                    result_json=json.dumps(result) if result is not None else None,
                                    confidence=(result or {}).get("confidence"), error=(error or "")[:500] or None)
        db.session.add(analysis)
        db.session.flush()
        return analysis

    # Same content analysed before for this document (e.g. a restored version): reuse, no AI call.
    # Not for a manual request: the user explicitly asked for a fresh analysis.
    previous = None if job.trigger == "manual" else db.session.execute(select(ValidityAnalysis).where(
        ValidityAnalysis.document_id == doc.id, ValidityAnalysis.sha256 == version.sha256,
        ValidityAnalysis.status.in_(("succeeded", "reused"))).order_by(ValidityAnalysis.id.desc()).limit(1)
    ).scalar_one_or_none()
    if previous is not None:
        result, status, reasons = validate_extraction(previous.result)
        if not _finish(job_id, owner, {"status": "succeeded", "finished_at": utcnow(), "locked_until": None,
                                       "last_error": None}):
            db.session.rollback()
            return "lost"
        analysis = record_analysis("reused", result, status, model=previous.model)
        _apply_result(doc, version, analysis, result, status, reasons)
        db.session.commit()
        log.info("Validity job %s: document %s reused an earlier analysis -> %s", job_id, doc.id, status)
        return "succeeded"

    from .gemini import PermanentAIError, TransientAIError, ai_client
    client = ai_client()
    model = getattr(client, "model_name", None) if client else None
    try:
        payload = prepare_input(doc, version)
    except UnsupportedDocument as exc:
        if _finish(job_id, owner, {"status": "failed", "last_error": str(exc), "finished_at": utcnow(),
                                   "locked_until": None}):
            record_analysis("unsupported", error=str(exc), model=model)
            _set_record_status(doc, version, "unsupported")
            log_activity("document", "validity_analysis_failed",
                         f"AI validity analysis of '{doc.name}' not possible: {exc}", doc.id, entity_name=doc.name,
                         status="failed")
        db.session.commit()
        return "failed"
    if client is None:
        # Not configured: put the job back without using up an attempt.
        _finish(job_id, owner, {"status": "queued", "attempts": max(0, job.attempts - 1), "locked_until": None,
                                "next_attempt_at": utcnow() + timedelta(minutes=10)})
        db.session.commit()
        return "queued"
    attempts, max_attempts = job.attempts, job.max_attempts
    db.session.commit()  # end the read transaction: no transaction stays open during the AI call

    error, transient, retry_after = None, True, None
    try:
        raw = client.analyse(**payload)
        result, status, reasons = validate_extraction(
            raw, float(_cfg("VALIDITY_CONFIDENCE_THRESHOLD", 0.75)))
    except InvalidResponse as exc:
        error, kind = f"Invalid AI response: {exc}", "invalid_response"
    except TransientAIError as exc:
        error, kind, retry_after = str(exc), "failed", exc.retry_after
    except PermanentAIError as exc:
        error, kind, transient = str(exc), "failed", False
    except Exception as exc:  # unexpected client failure: treat as transient
        log.exception("Unexpected error from the AI client for job %s", job_id)
        error, kind = f"Unexpected error ({type(exc).__name__}).", "failed"

    doc = db.session.get(Document, job.document_id)
    version = db.session.get(DocumentVersion, job.version_id)
    if doc is None or version is None:
        _finish(job_id, owner, {"status": "cancelled", "last_error": "The document was deleted.",
                                "finished_at": utcnow(), "locked_until": None})
        db.session.commit()
        return "cancelled"
    if error is None:
        if not _finish(job_id, owner, {"status": "succeeded", "finished_at": utcnow(), "locked_until": None,
                                       "last_error": None}):
            db.session.rollback()
            return "lost"
        analysis = record_analysis("succeeded", result, status, model=model)
        _apply_result(doc, version, analysis, result, status, reasons)
        log_activity("document", "validity_analysed",
                     f"AI validity analysis of '{doc.name}' finished: "
                     + ("please confirm the extracted dates" if status == "requires_confirmation"
                        else "manual review needed"),
                     doc.id, entity_name=doc.name, details={"version_id": version.id, "model": model,
                                                            "verification_status": status})
        db.session.commit()
        log.info("Validity job %s: document %s analysed by %s -> %s", job_id, doc.id, model, status)
        return "succeeded"

    final = not transient or attempts >= max_attempts
    if final:
        values = {"status": "failed", "last_error": error[:500], "finished_at": utcnow(), "locked_until": None}
    else:
        delay = retry_after or backoff_seconds(attempts, int(_cfg("VALIDITY_RETRY_BASE_SECONDS", 60)))
        values = {"status": "queued", "last_error": error[:500], "locked_until": None,
                  "next_attempt_at": utcnow() + timedelta(seconds=delay)}
    if not _finish(job_id, owner, values):
        db.session.rollback()
        return "lost"
    record_analysis(kind, error=error, model=model)
    if final:
        _set_record_status(doc, version, "analysis_failed")
        log_activity("document", "validity_analysis_failed",
                     f"AI validity analysis of '{doc.name}' failed after {attempts} attempt(s): {error}",
                     doc.id, entity_name=doc.name, status="failed")
    db.session.commit()
    log.info("Validity job %s attempt %s/%s failed (%s)%s", job_id, attempts, max_attempts, kind,
             "" if final else ", will retry")
    return "failed" if final else "retry"


def run_due_jobs(owner=None, limit=5):
    """Claim and process up to ``limit`` due jobs. Skips everything while the AI is not configured."""
    from .gemini import ai_client
    if ai_client() is None:
        return 0
    owner = owner or worker_id()
    done = 0
    for _ in range(limit):
        job_id = claim_next(owner)
        if job_id is None:
            break
        try:
            process_job(job_id, owner)
        except Exception:
            log.exception("Validity job %s crashed; it will be retried after its lease expires", job_id)
            db.session.rollback()
        done += 1
    return done
