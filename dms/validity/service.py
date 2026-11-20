"""Folder settings, document validity records, monitoring controls and acceptance exceptions.

Folder inheritance
    A folder either has an explicit setting (enabled / disabled) or inherits from its parent; the
    nearest explicit setting above wins and the top level defaults to disabled. So enabling a folder
    covers every subfolder that has no setting of its own, and a subfolder can opt out (or in)
    explicitly at any depth. Documents at the library root (no folder) are never monitored.
    This is independent of permission inheritance: it never grants or removes access.

Document records
    One ``document_validity`` row per document. AI results arrive as ``requires_confirmation`` or
    ``needs_review``; only a person (with edit access) makes them ``verified``, which is what makes the
    document eligible for expiry notices. Values can also be entered by hand without any AI.

Everything that changes is audited in the existing activity log (``validity_*`` actions, with
old/new values in ``details.changes``).
"""
from datetime import timedelta

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import Folder, User, utcnow
from ..services.activity import diff, log_activity
from ..validation import ValidationError
from . import clock
from .models import DocumentValidity, FolderValiditySetting, ValidityAnalysis, ValidityException, ValiditySubscription
from .rules import (
    EXCEPTION_SCOPES, MONITORING_STATES, effective_state, exception_applies, notice_for, parse_iso_date,
    validity_status,
)

MODES = ("inherit", "enabled", "disabled")
TEXT_FIELDS = {"document_type": 200, "title": 500, "document_number": 200, "vessel_name": 200,
               "issuing_authority": 300}


def start_days():
    return max(1, int(current_app.config.get("VALIDITY_NOTIFICATION_START_DAYS", 30)))


def module_enabled():
    return bool(current_app.config.get("VALIDITY_MONITORING_ENABLED", True))


# --------------------------------------------------------------------------- folder settings

def parse_mode(value):
    """'inherit' | 'enabled' | 'disabled' from a request value (booleans accepted)."""
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    text = str(value if value is not None else "").strip().lower()
    if text in ("", "inherit", "default"):
        return "inherit"
    if text in ("1", "true", "yes", "on", "enabled", "enable"):
        return "enabled"
    if text in ("0", "false", "no", "off", "disabled", "disable"):
        return "disabled"
    raise ValidationError("Validity monitoring must be 'inherit', 'enabled' or 'disabled'.")


def settings_map():
    """{folder_id: FolderValiditySetting} for every folder with an explicit setting."""
    return {s.folder_id: s for s in db.session.execute(select(FolderValiditySetting)).scalars()}


def folder_parents():
    """{folder_id: (parent_id, deleted_at)} for every folder."""
    return {r.id: (r.parent_id, r.deleted_at) for r in
            db.session.execute(select(Folder.id, Folder.parent_id, Folder.deleted_at)).all()}


def resolve_folder(folder_id, smap, parents):
    """(enabled, source_folder_id, enabled_at) for a folder id; source None = default (disabled)."""
    node, seen = folder_id, set()
    while node is not None and node not in seen:
        seen.add(node)
        setting = smap.get(node)
        if setting is not None:
            return bool(setting.enabled), node, setting.enabled_at
        node = parents.get(node, (None, None))[0]
    return False, None, None


def effective_folder_setting(folder):
    if folder is None:
        return False, None, None
    return resolve_folder(folder.id, settings_map(), folder_parents())


def enabled_folders():
    """{folder_id: enabled_at of the setting it inherits} for every live folder where monitoring is on."""
    smap = settings_map()
    if not any(s.enabled for s in smap.values()):
        return {}
    parents = folder_parents()
    out = {}
    for fid, (_, deleted_at) in parents.items():
        if deleted_at is not None:
            continue
        enabled, _, enabled_at = resolve_folder(fid, smap, parents)
        if enabled:
            out[fid] = enabled_at
    return out


def folder_setting_json(folder):
    setting = db.session.execute(select(FolderValiditySetting).where(
        FolderValiditySetting.folder_id == folder.id)).scalar_one_or_none()
    enabled, source, _ = effective_folder_setting(folder)
    source_folder = db.session.get(Folder, source) if source and source != folder.id else None
    return {
        "folder_id": folder.id,
        "mode": "inherit" if setting is None else ("enabled" if setting.enabled else "disabled"),
        "effective": enabled,
        "inherited_from": {"id": source_folder.id, "name": source_folder.name} if source_folder else None,
        "module_enabled": module_enabled(),
    }


def set_folder_mode(folder, mode, user, analyse_existing=False):
    """Set a folder's explicit setting ('inherit' removes it). Existing validity data is never deleted
    when monitoring is switched off; notices and new analyses simply stop. Does not commit."""
    mode = parse_mode(mode)
    setting = db.session.execute(select(FolderValiditySetting).where(
        FolderValiditySetting.folder_id == folder.id)).scalar_one_or_none()
    old_mode = "inherit" if setting is None else ("enabled" if setting.enabled else "disabled")
    old_effective = effective_folder_setting(folder)[0]
    if mode == "inherit":
        if setting is not None:
            db.session.delete(setting)
    else:
        if setting is None:
            setting = FolderValiditySetting(folder_id=folder.id, enabled=mode == "enabled")
            db.session.add(setting)
        if mode == "enabled" and (not setting.enabled or setting.enabled_at is None or not old_effective):
            setting.enabled_at = utcnow()
        setting.enabled = mode == "enabled"
        setting.updated_by_id = user.id if user else None
    db.session.flush()
    new_effective = effective_folder_setting(folder)[0]
    if old_mode != mode or old_effective != new_effective:
        log_activity("folder", "validity_monitoring_changed",
                     f"Document validity monitoring for '{folder.path}': {mode}"
                     + ("" if mode != "inherit" else f" (now {'on' if new_effective else 'off'})"),
                     folder.id, entity_name=folder.path,
                     changes=diff({"mode": old_mode, "effective": old_effective},
                                  {"mode": mode, "effective": new_effective}))
    queued = 0
    if new_effective and analyse_existing:
        from .jobs import enqueue_folder_documents
        queued = enqueue_folder_documents(folder, user)
    return queued


# --------------------------------------------------------------------------- document records

def get_record(doc_id):
    return db.session.execute(select(DocumentValidity).where(
        DocumentValidity.document_id == doc_id)).scalar_one_or_none()


def ensure_record(doc):
    record = get_record(doc.id)
    if record is None:
        record = DocumentValidity(document_id=doc.id, verification_status="pending", monitoring_state="active")
        db.session.add(record)
        db.session.flush()
    return record


def _snapshot(record):
    return {
        "document_type": record.document_type, "title": record.title,
        "document_number": record.document_number, "vessel_name": record.vessel_name,
        "issuing_authority": record.issuing_authority,
        "issue_date": record.issue_date.isoformat() if record.issue_date else None,
        "expiry_date": record.expiry_date.isoformat() if record.expiry_date else None,
        "has_expiry": record.has_expiry, "verification_status": record.verification_status,
    }


def _audit(doc, action, message, changes=None, details=None):
    log_activity("document", action, message, doc.id, entity_name=doc.name, changes=changes or None,
                 details=details)


def _text_value(data, field):
    value = data.get(field)
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > TEXT_FIELDS[field]:
        raise ValidationError(f"'{field.replace('_', ' ')}' is too long (at most {TEXT_FIELDS[field]} characters).")
    return value or None


def _date_value(data, field):
    raw = data.get(field)
    if raw in (None, ""):
        return None
    parsed = parse_iso_date(str(raw))
    if parsed is None:
        raise ValidationError(f"Enter the {field.replace('_', ' ')} as a valid date (YYYY-MM-DD).")
    return parsed


def _bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def confirm(doc, user, data):
    """Confirm or correct the validity information of the current version; makes it ``verified``.

    ``data``: document_type, title, document_number, vessel_name, issuing_authority, issue_date,
    expiry_date (YYYY-MM-DD), has_expiry. Does not commit.
    """
    if doc.current_version_id is None:
        raise ValidationError("This document has no current version.")
    record = ensure_record(doc)
    before = _snapshot(record)
    values = {f: _text_value(data, f) if f in data else getattr(record, f) for f in TEXT_FIELDS}
    issue = _date_value(data, "issue_date") if "issue_date" in data else record.issue_date
    expiry = _date_value(data, "expiry_date") if "expiry_date" in data else record.expiry_date
    has_expiry = _bool_value(data["has_expiry"]) if "has_expiry" in data else \
        (record.has_expiry if record.has_expiry is not None else expiry is not None)
    if has_expiry and expiry is None:
        raise ValidationError("Enter the expiry date, or mark the document as having no expiry date.")
    if not has_expiry:
        expiry = None
    if issue and expiry and expiry <= issue:
        raise ValidationError("The expiry date must be after the issue date.")
    for field, value in values.items():
        setattr(record, field, value)
    record.issue_date, record.expiry_date, record.has_expiry = issue, expiry, has_expiry
    record.version_id = doc.current_version_id
    ai = db.session.get(ValidityAnalysis, record.analysis_id) if record.analysis_id else None
    ai_values = ai.result if ai is not None and ai.version_id == record.version_id else {}
    corrected = not ai_values or any(
        (ai_values.get("document_title") if f == "title" else ai_values.get(f)) != after
        for f, after in (("document_type", values["document_type"]), ("title", values["title"]),
                         ("document_number", values["document_number"]), ("vessel_name", values["vessel_name"]),
                         ("issuing_authority", values["issuing_authority"]),
                         ("issue_date", issue.isoformat() if issue else None),
                         ("expiry_date", expiry.isoformat() if expiry else None),
                         ("has_expiry", has_expiry)))
    record.source = "manual" if corrected else "ai"
    record.verification_status = "verified"
    record.review_reasons_json = "[]"
    record.verified_by_id = user.id
    record.verified_at = utcnow()
    db.session.flush()
    after = _snapshot(record)
    _audit(doc, "validity_confirmed" if not corrected else "validity_corrected",
           f"Validity of '{doc.name}' {'confirmed' if not corrected else 'corrected and confirmed'}"
           + (f": expires {expiry.isoformat()}" if expiry else ": no expiry date"),
           changes=diff(before, after), details={"version_id": record.version_id, "source": record.source})
    return record


def set_monitoring(doc, user, state, paused_until=None, reason=None):
    """Document-level control: active (resume), paused (until a date, then resumes by itself) or
    disabled (until resumed). Nothing is deleted. Does not commit."""
    if state not in MONITORING_STATES:
        raise ValidationError("Choose active, paused or disabled.")
    record = ensure_record(doc)
    today = clock.today()
    until = None
    if state == "paused":
        until = parse_iso_date(str(paused_until or ""))
        if until is None:
            raise ValidationError("Enter the date until which monitoring is paused (YYYY-MM-DD).")
        if until < today:
            raise ValidationError("The pause end date cannot be in the past.")
        if until > today + timedelta(days=3660):
            raise ValidationError("A pause can last at most 10 years; disable monitoring instead.")
    reason = (str(reason or "").strip() or None)
    if reason and len(reason) > 500:
        raise ValidationError("The reason is too long (at most 500 characters).")
    old = {"monitoring_state": record.monitoring_state,
           "paused_until": record.paused_until.isoformat() if record.paused_until else None}
    record.monitoring_state = state
    record.paused_until = until
    record.state_reason = reason
    record.state_changed_by_id = user.id if user else None
    record.state_changed_at = utcnow()
    db.session.flush()
    new = {"monitoring_state": state, "paused_until": until.isoformat() if until else None}
    words = {"active": "resumed", "paused": f"paused until {until}" if until else "paused", "disabled": "disabled"}
    _audit(doc, "validity_monitoring_" + ("resumed" if state == "active" else state),
           f"Expiry monitoring of '{doc.name}' {words[state]}", changes=diff(old, new),
           details={"reason": reason} if reason else None)
    if state == "active" and old["monitoring_state"] == "disabled" and record.version_id != doc.current_version_id:
        # Monitoring was off while a new version arrived: analyse it now.
        from .jobs import enqueue_versions
        enqueue_versions([doc.current_version_id], trigger="manual", requested_by=user, conn=None)
    return record


def auto_resume(record, doc, today):
    """End a pause whose last day has passed (audited as a system action). Returns True if resumed."""
    if effective_state(record.monitoring_state, record.paused_until, today) == record.monitoring_state:
        return False
    old_until = record.paused_until
    record.monitoring_state = "active"
    record.paused_until = None
    record.state_changed_by_id = None
    record.state_changed_at = utcnow()
    _audit(doc, "validity_monitoring_resumed",
           f"Expiry monitoring of '{doc.name}' resumed automatically (pause ended {old_until})",
           changes={"monitoring_state": {"old": "paused", "new": "active"}}, details={"automatic": True})
    return True


# --------------------------------------------------------------------------- subscriptions

def is_subscribed(doc, user):
    row = db.session.execute(select(ValiditySubscription).where(
        ValiditySubscription.document_id == doc.id, ValiditySubscription.user_id == user.id)).scalar_one_or_none()
    if row is not None:
        return bool(row.subscribed)
    return doc.owner_id == user.id


def set_subscription(doc, user, subscribed):
    row = db.session.execute(select(ValiditySubscription).where(
        ValiditySubscription.document_id == doc.id, ValiditySubscription.user_id == user.id)).scalar_one_or_none()
    if row is None:
        row = ValiditySubscription(document_id=doc.id, user_id=user.id, subscribed=bool(subscribed))
        db.session.add(row)
    row.subscribed = bool(subscribed)
    db.session.flush()


def recipients(doc, resolvers=None):
    """Users who receive this document's alerts *and* may still view it (checked every day, so losing
    access stops the alerts). Administrators get nothing extra. ``resolvers``: optional per-user cache."""
    from .access import resolver_for

    def can(perm, item, user):
        if resolvers is None:
            return perm in resolver_for(user).perms(item)
        if user.id not in resolvers:
            resolvers[user.id] = resolver_for(user)
        return perm in resolvers[user.id].perms(item)

    rows = {r.user_id: r.subscribed for r in db.session.execute(select(ValiditySubscription).where(
        ValiditySubscription.document_id == doc.id)).scalars()}
    ids = {uid for uid, on in rows.items() if on}
    if doc.owner_id is not None and rows.get(doc.owner_id, True):
        ids.add(doc.owner_id)
    out = []
    for uid in sorted(ids):
        user = db.session.get(User, uid)
        if user is not None and user.is_active and can("view", doc, user):
            out.append(user)
    return out


# --------------------------------------------------------------------------- exceptions

def _exception_values(data, existing=None):
    def text(name, limit, required=False):
        value = data.get(name) if name in data else (getattr(existing, name) if existing else None)
        value = str(value).strip() if value is not None else ""
        if required and not value:
            raise ValidationError(f"Enter the {name.replace('_', ' ')}.")
        if len(value) > limit:
            raise ValidationError(f"'{name.replace('_', ' ')}' is too long (at most {limit} characters).")
        return value or None

    scope = (data.get("scope") if "scope" in data else (existing.scope if existing else "")) or ""
    scope = str(scope).strip().lower()
    if scope not in EXCEPTION_SCOPES:
        raise ValidationError("Choose the acceptance scope: a specific port, or all applicable ports.")
    reason = text("reason", 5000, required=True)
    if len(reason) < 5:
        raise ValidationError("Describe the reason for the acceptance.")
    port_name = text("port_name", 200)
    if scope == "port" and not port_name:
        raise ValidationError("Enter the port that accepts the document.")
    start = _date_value(data, "start_date") if data.get("start_date") else \
        (existing.start_date if existing else clock.today())
    if "end_date" in data:
        end = _date_value(data, "end_date")
    else:
        end = existing.end_date if existing else None
    justification = text("indefinite_justification", 2000)
    if end is None and (not justification or len(justification) < 10):
        raise ValidationError("Enter an end or review date, or explain why the acceptance has no end date.")
    if end is not None and end < start:
        raise ValidationError("The end/review date cannot be before the start date.")
    return {"scope": scope, "port_name": port_name if scope == "port" else None,
            "authority": text("authority", 300), "reason": reason,
            "reference_number": text("reference_number", 200), "start_date": start, "end_date": end,
            "indefinite_justification": justification if end is None else None}


def exception_json(exc, today=None):
    today = today or clock.today()
    users = {u.id: u.label for u in db.session.execute(select(User).where(User.id.in_(
        [i for i in (exc.created_by_id, exc.updated_by_id, exc.withdrawn_by_id) if i]))).scalars()}
    state = exc.status
    if state == "active":
        if exc.start_date > today:
            state = "scheduled"
        elif exc.end_date is not None and exc.end_date < today:
            state = "ended"
    return {
        "id": exc.id, "scope": exc.scope, "port_name": exc.port_name, "authority": exc.authority,
        "reason": exc.reason, "reference_number": exc.reference_number,
        "start_date": exc.start_date.isoformat(), "end_date": exc.end_date.isoformat() if exc.end_date else None,
        "indefinite_justification": exc.indefinite_justification, "status": exc.status, "state": state,
        "applies_today": exception_applies(exc.status, exc.start_date, exc.end_date, today),
        "evidence": {"filename": exc.evidence_filename, "size": exc.evidence_size,
                     "mime_type": exc.evidence_mime} if exc.evidence_storage_key else None,
        "created_by": users.get(exc.created_by_id), "created_at": exc.created_at.isoformat() + "Z",
        "updated_by": users.get(exc.updated_by_id), "updated_at": exc.updated_at.isoformat() + "Z",
        "withdrawn_by": users.get(exc.withdrawn_by_id),
        "withdrawn_at": exc.withdrawn_at.isoformat() + "Z" if exc.withdrawn_at else None,
        "withdrawn_reason": exc.withdrawn_reason,
    }


def _store_evidence(file_storage):
    from ..services.documents import _scan, _store
    from ..validation import validate_name, validate_upload
    display_name, _ext, mime = validate_upload(file_storage)
    validate_name(display_name, "document")
    _scan(file_storage, display_name)
    key, size, sha = _store(file_storage.stream)
    return {"evidence_storage_key": key, "evidence_filename": display_name, "evidence_mime": mime,
            "evidence_size": size, "evidence_sha256": sha}


def _exception_audit_values(exc):
    return {"scope": exc.scope, "port_name": exc.port_name, "authority": exc.authority, "reason": exc.reason,
            "reference_number": exc.reference_number,
            "start_date": exc.start_date.isoformat() if exc.start_date else None,
            "end_date": exc.end_date.isoformat() if exc.end_date else None,
            "indefinite_justification": exc.indefinite_justification, "status": exc.status,
            "evidence": exc.evidence_filename}


def create_exception(doc, user, data, evidence=None):
    """Record "expired but still accepted". The expiry date and expired status stay as they are.
    Commits (the evidence file is removed again if the database write fails)."""
    record = get_record(doc.id)
    if record is None or record.verification_status != "verified" or not record.expiry_date:
        raise ValidationError("Confirm the document's expiry date before recording an acceptance exception.")
    values = _exception_values(data)
    stored = _store_evidence(evidence) if evidence is not None and evidence.filename else {}
    try:
        exc = ValidityException(document_id=doc.id, version_id=record.version_id, status="active",
                                created_by_id=user.id, updated_by_id=user.id, **values, **stored)
        db.session.add(exc)
        db.session.flush()
        where = f"port {exc.port_name}" if exc.scope == "port" else "all applicable ports"
        _audit(doc, "validity_exception_created",
               f"Acceptance exception recorded for '{doc.name}' ({where}"
               + (f", until {exc.end_date}" if exc.end_date else ", no end date") + ")",
               changes=diff({}, _exception_audit_values(exc)), details={"exception_id": exc.id})
        db.session.commit()
    except Exception:
        db.session.rollback()
        if stored:
            from ..storage import get_storage
            get_storage().delete(stored["evidence_storage_key"])
        raise
    return exc


def update_exception(doc, exc, user, data, evidence=None):
    if exc.status != "active":
        raise ValidationError("A withdrawn exception cannot be changed; record a new one instead.")
    before = _exception_audit_values(exc)
    values = _exception_values(data, existing=exc)
    stored = _store_evidence(evidence) if evidence is not None and evidence.filename else {}
    old_key = exc.evidence_storage_key if stored else None
    try:
        for key, value in {**values, **stored}.items():
            setattr(exc, key, value)
        exc.updated_by_id = user.id
        db.session.flush()
        _audit(doc, "validity_exception_updated", f"Acceptance exception for '{doc.name}' updated",
               changes=diff(before, _exception_audit_values(exc)), details={"exception_id": exc.id})
        db.session.commit()
    except Exception:
        db.session.rollback()
        if stored:
            from ..storage import get_storage
            get_storage().delete(stored["evidence_storage_key"])
        raise
    if old_key:
        # Superseded evidence stays on record: the audit names it, so it is kept, not deleted.
        current_app.logger.info("Exception %s evidence replaced (previous file kept)", exc.id)
    return exc


def withdraw_exception(doc, exc, user, reason=None):
    if exc.status == "withdrawn":
        return exc
    reason = (str(reason or "").strip() or None)
    if reason and len(reason) > 500:
        raise ValidationError("The reason is too long (at most 500 characters).")
    exc.status = "withdrawn"
    exc.withdrawn_by_id = user.id
    exc.withdrawn_at = utcnow()
    exc.withdrawn_reason = reason
    exc.updated_by_id = user.id
    _audit(doc, "validity_exception_withdrawn", f"Acceptance exception for '{doc.name}' withdrawn",
           changes={"status": {"old": "active", "new": "withdrawn"}},
           details={"exception_id": exc.id, "reason": reason})
    db.session.commit()
    return exc


def applicable_exceptions(doc_id, today):
    rows = db.session.execute(select(ValidityException).where(
        ValidityException.document_id == doc_id, ValidityException.status == "active")).scalars().all()
    return [e for e in rows if exception_applies(e.status, e.start_date, e.end_date, today)]


# --------------------------------------------------------------------------- summary for the UI

def summary(doc, record, today=None):
    """Validity summary of a document: what the UI shows in lists and on the document page."""
    today = today or clock.today()
    folder_on, _, _ = effective_folder_setting(doc.folder)
    if record is None:
        return {"tracked": False, "folder_monitoring": folder_on, "status": None, "monitoring_state": None}
    state = effective_state(record.monitoring_state, record.paused_until, today)
    stale = record.version_id is not None and record.version_id != doc.current_version_id
    verified = record.verification_status == "verified" and not stale
    status = validity_status(record.expiry_date, record.has_expiry, today, start_days()) \
        if verified or record.expiry_date else None
    left = (record.expiry_date - today).days if record.expiry_date else None
    accepted = bool(left is not None and left < 0 and applicable_exceptions(doc.id, today))
    notice = notice_for(record.expiry_date, today, start_days()) if verified and record.has_expiry else None
    active_alerts = bool(folder_on and state == "active" and notice is not None
                         and not (accepted and notice.days_left < 0) and doc.deleted_at is None)
    return {
        "tracked": True, "folder_monitoring": folder_on, "monitoring_state": state,
        "paused_until": record.paused_until.isoformat() if record.paused_until and state == "paused" else None,
        "verification_status": record.verification_status, "stale": stale,
        "status": status, "accepted": accepted, "days_left": left,
        "expiry_date": record.expiry_date.isoformat() if record.expiry_date else None,
        "has_expiry": record.has_expiry, "document_type": record.document_type,
        "vessel_name": record.vessel_name, "alerts_active": active_alerts,
    }
