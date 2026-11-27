"""Validity and notification endpoints (``/api/v1``). Same authentication, CSRF and error format as
every other API endpoint (``routes.api.api_auth``). Access: see ``validity/access.py``."""
from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func, select

from ..extensions import db
from ..models import ActivityLog, Document, User
from ..routes.api import _body, api_auth
from ..services import documents as doc_svc
from ..services.folders import NotFound, get_folder
from ..validation import ValidationError
from . import access, clock, notifications, service
from .models import DocumentValidity, ValidityAnalysis, ValidityException, ValidityJob, ValiditySchedulerRun

bp = Blueprint("validity", __name__, url_prefix="/api/v1")


def _form():
    data = _body()
    return {key: data.get(key) for key in data}


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _date(value):
    return value.isoformat() if value else None


def _user_label(uid):
    user = db.session.get(User, uid) if uid else None
    return user.label if user else None


def _document(doc_id, perm="view"):
    doc = doc_svc.get_document(doc_id)
    access.require(perm, doc)
    return doc


def _ai_configured():
    return bool(current_app.extensions.get("validity_ai_client") or current_app.config.get("GEMINI_API_KEY"))


# --------------------------------------------------------------------------- folders

@bp.get("/folders/<int:folder_id>/validity")
@api_auth
def folder_validity(folder_id):
    folder = get_folder(folder_id)
    access.require("view", folder)
    return jsonify(**service.folder_setting_json(folder), can_edit=access.can("edit", folder))


@bp.put("/folders/<int:folder_id>/validity")
@api_auth
def set_folder_validity(folder_id):
    folder = get_folder(folder_id)
    access.require("edit", folder, "Only the folder's owner or someone with edit access can change this.")
    data = _form()
    analyse = str(data.get("analyse_existing") or "").lower() in ("1", "true", "yes")
    queued = service.set_folder_mode(folder, data.get("mode"), g.user, analyse_existing=analyse)
    db.session.commit()
    return jsonify(ok=True, queued=queued, **service.folder_setting_json(folder),
                   can_edit=access.can("edit", folder))


# --------------------------------------------------------------------------- documents

def _record_json(doc, record, today):
    if record is None:
        return None
    from .rules import effective_state
    return {
        "version_id": record.version_id, "stale": record.version_id not in (None, doc.current_version_id),
        "document_type": record.document_type, "title": record.title, "document_number": record.document_number,
        "vessel_name": record.vessel_name, "issuing_authority": record.issuing_authority,
        "issue_date": _date(record.issue_date), "expiry_date": _date(record.expiry_date),
        "expired_on_upload": service.expired_on_upload(record),
        "has_expiry": record.has_expiry, "confidence": record.confidence,
        "verification_status": record.verification_status, "review_reasons": record.review_reasons,
        "source": record.source, "verified_by": _user_label(record.verified_by_id),
        "verified_at": _iso(record.verified_at),
        "monitoring_state": effective_state(record.monitoring_state, record.paused_until, today),
        "paused_until": _date(record.paused_until) if record.monitoring_state == "paused" else None,
        "state_reason": record.state_reason, "state_changed_by": _user_label(record.state_changed_by_id),
        "state_changed_at": _iso(record.state_changed_at),
    }


def _job_json(job):
    if job is None:
        return None
    return {"id": job.id, "status": job.status, "attempts": job.attempts, "max_attempts": job.max_attempts,
            "last_error": job.last_error, "next_attempt_at": _iso(job.next_attempt_at) if job.status == "queued" else None,
            "trigger": job.trigger, "created_at": _iso(job.created_at), "updated_at": _iso(job.updated_at),
            "finished_at": _iso(job.finished_at)}


def _validity_payload(doc):
    today = clock.today()
    record = service.get_record(doc.id)
    job = db.session.execute(select(ValidityJob).where(
        ValidityJob.version_id == doc.current_version_id)).scalar_one_or_none() if doc.current_version_id else None
    labels = {v.id: v.label for v in doc.versions}
    analyses = db.session.execute(select(ValidityAnalysis).where(ValidityAnalysis.document_id == doc.id)
                                  .order_by(ValidityAnalysis.id.desc()).limit(20)).scalars().all()
    exceptions = db.session.execute(select(ValidityException).where(ValidityException.document_id == doc.id)
                                    .order_by(ValidityException.id.desc())).scalars().all()
    folder_on, source, _ = service.effective_folder_setting(doc.folder)
    return {
        "document_id": doc.id, "today": today.isoformat(), "start_days": service.start_days(),
        "module_enabled": service.module_enabled(), "ai_configured": _ai_configured(),
        "folder": {"id": doc.folder_id, "monitoring": folder_on, "source_folder_id": source},
        "record": _record_json(doc, record, today),
        "summary": service.summary(doc, record, today),
        "job": _job_json(job),
        "analyses": [{"id": a.id, "version": labels.get(a.version_id), "status": a.status, "model": a.model,
                      "verification_status": a.verification_status, "confidence": a.confidence,
                      "error": a.error, "result": a.result if a.result_json else None,
                      "created_at": _iso(a.created_at)} for a in analyses],
        "exceptions": [service.exception_json(e, today) for e in exceptions],
        "can_edit": access.can("edit", doc), "can_download": access.can("download", doc),
        "subscribed": service.is_subscribed(doc, g.user),
    }


@bp.get("/documents/<int:doc_id>/validity")
@api_auth
def document_validity(doc_id):
    return jsonify(_validity_payload(_document(doc_id)))


@bp.put("/documents/<int:doc_id>/validity")
@api_auth
def confirm_validity(doc_id):
    doc = _document(doc_id, "edit")
    service.confirm(doc, g.user, _form())
    db.session.commit()
    return jsonify(ok=True, **_validity_payload(doc))


@bp.post("/documents/<int:doc_id>/validity/analyse")
@api_auth
def analyse_validity(doc_id):
    from .jobs import requeue_current, wake_worker
    doc = _document(doc_id, "edit")
    requeue_current(doc, g.user)
    wake_worker()
    return jsonify(ok=True, **_validity_payload(doc)), 202


@bp.post("/validity/check-upload")
@api_auth
def check_upload():
    """Before an upload: has the chosen file already expired? Form fields: ``file`` and either ``folder_id``
    (new document) or ``document_id`` (new version). Nothing is stored. Answers ``{"checked": false}`` when
    the folder is not monitored, the AI is not configured or the file cannot be analysed; the upload itself
    never depends on this check."""
    from ..security import policy
    from ..validation import validate_upload
    from .jobs import check_new_file
    from .rules import parse_iso_date
    doc_id = request.form.get("document_id", type=int)
    if doc_id is not None:
        doc = doc_svc.get_document(doc_id)
        policy.require("item.version.create", doc)
        folder = doc.folder
    else:
        folder = get_folder(request.form.get("folder_id"))
        policy.require("folder.upload", folder)
    if folder is None or not service.module_enabled() or not _ai_configured() \
            or not service.effective_folder_setting(folder)[0]:
        return jsonify(checked=False)
    file = request.files.get("file")
    display_name, ext, _ = validate_upload(file)
    doc_svc._scan(file, display_name)  # nothing leaves the server before it passed the malware scan
    file.stream.seek(0)
    result = check_new_file(file.stream.read(), ext)
    if result is None:
        return jsonify(checked=False)
    expiry = parse_iso_date(result["expiry_date"] or "")
    return jsonify(checked=True, has_expiry=result["has_expiry"], expiry_date=_date(expiry),
                   expired=bool(expiry and expiry < clock.today()))


@bp.post("/documents/<int:doc_id>/validity/monitoring")
@api_auth
def set_monitoring(doc_id):
    doc = _document(doc_id, "edit")
    data = _form()
    service.set_monitoring(doc, g.user, (data.get("state") or "").strip().lower(), data.get("paused_until"),
                           data.get("reason"))
    db.session.commit()
    return jsonify(ok=True, **_validity_payload(doc))


@bp.put("/documents/<int:doc_id>/validity/subscription")
@api_auth
def set_subscription(doc_id):
    doc = _document(doc_id, "view")
    value = str(_form().get("subscribed", "")).lower() in ("1", "true", "yes", "on")
    service.set_subscription(doc, g.user, value)
    db.session.commit()
    return jsonify(ok=True, subscribed=service.is_subscribed(doc, g.user))


@bp.get("/documents/<int:doc_id>/validity/history")
@api_auth
def validity_history(doc_id):
    """Who changed what in this document's validity data (from the existing audit log)."""
    doc = _document(doc_id, "view")
    rows = db.session.execute(select(ActivityLog).where(
        ActivityLog.entity_type == "document", ActivityLog.entity_id == doc.id,
        ActivityLog.action.like("validity\\_%")).order_by(ActivityLog.id.desc()).limit(200)).scalars().all()
    return jsonify(events=[{"id": r.id, "action": r.action, "message": r.message, "user": r.user_name,
                            "status": r.status, "changes": r.details.get("changes"),
                            "created_at": _iso(r.created_at)} for r in rows])


def _exception(doc, exception_id):
    exc = db.session.get(ValidityException, exception_id)
    if exc is None or exc.document_id != doc.id:
        raise NotFound("Exception not found.")
    return exc


@bp.post("/documents/<int:doc_id>/validity/exceptions")
@api_auth
def create_exception(doc_id):
    doc = _document(doc_id, "edit")
    exc = service.create_exception(doc, g.user, _form(), request.files.get("evidence"))
    return jsonify(ok=True, exception=service.exception_json(exc), **_validity_payload(doc)), 201


@bp.patch("/documents/<int:doc_id>/validity/exceptions/<int:exception_id>")
@api_auth
def update_exception(doc_id, exception_id):
    doc = _document(doc_id, "edit")
    exc = service.update_exception(doc, _exception(doc, exception_id), g.user, _form(), request.files.get("evidence"))
    return jsonify(ok=True, exception=service.exception_json(exc), **_validity_payload(doc))


@bp.post("/documents/<int:doc_id>/validity/exceptions/<int:exception_id>/withdraw")
@api_auth
def withdraw_exception(doc_id, exception_id):
    doc = _document(doc_id, "edit")
    service.withdraw_exception(doc, _exception(doc, exception_id), g.user, _form().get("reason"))
    return jsonify(ok=True, **_validity_payload(doc))


@bp.get("/documents/<int:doc_id>/validity/exceptions/<int:exception_id>/evidence")
@api_auth
def exception_evidence(doc_id, exception_id):
    from ..routes._helpers import stream_stored
    from ..services.activity import log_activity
    doc = _document(doc_id, "download")
    exc = _exception(doc, exception_id)
    if not exc.evidence_storage_key:
        raise NotFound("This exception has no supporting evidence.")
    log_activity("document", "validity_evidence_downloaded",
                 f"Downloaded acceptance evidence '{exc.evidence_filename}' of '{doc.name}'", doc.id,
                 entity_name=doc.name, details={"exception_id": exc.id})
    db.session.commit()
    return stream_stored(exc.evidence_storage_key, exc.evidence_mime or "application/octet-stream",
                         exc.evidence_filename or "evidence", as_attachment=True, etag=exc.evidence_sha256)


# --------------------------------------------------------------------------- dashboard

STATUS_FILTERS = ("all", "expired", "expiring", "expires_today", "valid", "no_expiry", "accepted",
                  "needs_review", "requires_confirmation", "pending", "failed", "paused", "disabled")


def _matches(filter_name, summary):
    if filter_name == "all":
        return True
    if filter_name in ("expired", "expiring", "expires_today", "valid", "no_expiry"):
        if filter_name == "expired":
            return summary["status"] == "expired" and not summary["accepted"]
        return summary["status"] == filter_name
    if filter_name == "accepted":
        return summary["accepted"]
    if filter_name in ("paused", "disabled"):
        return summary["monitoring_state"] == filter_name
    if filter_name == "failed":
        return summary["verification_status"] in ("analysis_failed", "unsupported")
    return summary["verification_status"] == filter_name


@bp.get("/validity/documents")
@api_auth
def validity_documents():
    """Monitored documents the user may view (as an ordinary user), with their validity status.
    ``status`` filters (see STATUS_FILTERS); ``q`` matches name, type, number or vessel."""
    status = (request.args.get("status") or "all").lower()
    if status not in STATUS_FILTERS:
        raise ValidationError("Unknown status filter.")
    q = (request.args.get("q") or "").strip().lower()
    limit = min(500, max(1, request.args.get("limit", 100, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    res = access.resolver_for(g.user)
    rows = db.session.execute(
        select(DocumentValidity, Document).join(Document, Document.id == DocumentValidity.document_id)
        .where(Document.deleted_at.is_(None), res.visible_documents_clause())
        .order_by(DocumentValidity.expiry_date.is_(None), DocumentValidity.expiry_date, Document.name)
        .limit(5000)).all()
    today = clock.today()
    counts = {name: 0 for name in STATUS_FILTERS}
    items = []
    for record, doc in rows:
        if "view" not in res.document(doc):
            continue
        summary = service.summary(doc, record, today)
        if q and not any(q in (v or "").lower() for v in (doc.name, record.document_type, record.document_number,
                                                          record.vessel_name, record.title)):
            continue
        for name in STATUS_FILTERS:
            if _matches(name, summary):
                counts[name] += 1
        if _matches(status, summary):
            items.append({"id": doc.id, "name": doc.name, "extension": doc.extension, "folder_id": doc.folder_id,
                          "document_number": record.document_number, "title": record.title, **summary})
    return jsonify(documents=items[offset:offset + limit], total=len(items), counts=counts, today=today.isoformat(),
                   next_offset=offset + limit if offset + limit < len(items) else None)


@bp.get("/validity/status")
@api_auth
def validity_status():
    """Operational health for administrators: queue and scheduler state (no document content)."""
    from ..security import policy
    policy.require_system("admin.policy")
    jobs = dict(db.session.execute(select(ValidityJob.status, func.count()).group_by(ValidityJob.status)).all())
    runs = db.session.execute(select(ValiditySchedulerRun).order_by(ValiditySchedulerRun.run_date.desc())
                              .limit(14)).scalars().all()
    worker = current_app.extensions.get("validity_worker")
    return jsonify(
        module_enabled=service.module_enabled(), ai_configured=_ai_configured(),
        ai_model=current_app.config.get("GEMINI_MODEL"), email_configured=notifications.email_configured(),
        timezone=current_app.config.get("VALIDITY_TIMEZONE"), scheduler_time=current_app.config.get(
            "VALIDITY_SCHEDULER_TIME"), notification_start_days=service.start_days(),
        in_process_worker=bool(worker), jobs=jobs, emails=notifications.counts_by_status(),
        runs=[{"run_date": r.run_date.isoformat(), "status": r.status, "attempts": r.attempts,
               "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
               "documents_checked": r.documents_checked, "notifications_created": r.notifications_created,
               "resumed": r.resumed, "errors": r.errors, "error_text": r.error_text} for r in runs])


# --------------------------------------------------------------------------- notification centre

@bp.get("/notifications")
@api_auth
def list_notifications():
    limit = min(200, max(1, request.args.get("limit", 50, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    unread = request.args.get("unread") in ("1", "true")
    items, more = notifications.list_for(g.user, unread_only=unread, limit=limit, offset=offset)
    return jsonify(notifications=[notifications.notification_json(n) for n in items],
                   unread=notifications.unread_count(g.user), next_offset=offset + limit if more else None)


@bp.get("/notifications/unread-count")
@api_auth
def unread_notifications():
    return jsonify(unread=notifications.unread_count(g.user))


@bp.post("/notifications/<int:notification_id>/read")
@api_auth
def read_notification(notification_id):
    if not notifications.mark_read(g.user, notification_id):
        raise NotFound("Notification not found.")
    return jsonify(ok=True, unread=notifications.unread_count(g.user))


@bp.post("/notifications/read-all")
@api_auth
def read_all_notifications():
    notifications.mark_all_read(g.user)
    return jsonify(ok=True, unread=0)


@bp.get("/notifications/preferences")
@api_auth
def notification_preferences():
    return jsonify(email_mode=notifications.email_mode(g.user.id), modes=list(notifications.EMAIL_MODES),
                   email_available=notifications.email_configured(), has_email=bool(g.user.email))


@bp.put("/notifications/preferences")
@api_auth
def update_notification_preferences():
    notifications.set_email_mode(g.user, (_form().get("email_mode") or "").strip().lower())
    return jsonify(ok=True, email_mode=notifications.email_mode(g.user.id))
