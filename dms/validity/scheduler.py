"""Daily expiry check and the background worker.

Daily run (``run_daily``)
    Once per business day, at or after VALIDITY_SCHEDULER_TIME (in VALIDITY_TIMEZONE). The day is
    claimed atomically through the unique ``validity_scheduler_runs.run_date`` row, so with several
    app instances or workers only one runs it; a run whose worker crashed is taken over once its lease
    expires. Notices are unique per (user, document, day) in the database as well, so even a repeated
    run never duplicates them. Only *today's* notice is created: days the server was down, or a
    document was paused, never produce a backlog.

    A document gets a notice only when all of these hold:
      1-2. its folder's effective setting (nearest explicit setting above) enables monitoring;
      3.   its own monitoring is active (an ended pause is resumed first, audited);
      4.   it is not deleted;
      5.   the validity data belongs to its current version;
      6.   the data is verified by a person and has an expiry date;
      7.   no acceptance exception applies (only checked once it has expired);
      8.   today falls in the notice period (from VALIDITY_NOTIFICATION_START_DAYS before expiry).
    Recipients are re-checked for view access every day.

Worker (``ValidityWorker`` thread, or ``flask validity-worker`` as a separate process)
    Every VALIDITY_WORKER_INTERVAL_SECONDS: process due analysis jobs, retry emails, start the daily run
    when it is due, and periodically reconcile missed jobs.
"""
import logging
import threading
from datetime import timedelta

from flask import current_app
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Document, utcnow
from . import clock
from .jobs import reconcile, run_due_jobs, worker_id
from .models import DocumentValidity, ValiditySchedulerRun
from .notifications import deliver_pending, notify, wants_email
from .rules import ALERT_MILESTONES, notice_for

log = logging.getLogger(__name__)
MAX_RUN_ATTEMPTS = 5


def _lease():
    return timedelta(seconds=int(current_app.config.get("VALIDITY_RUN_LEASE_SECONDS", 1800)))


def claim_run(run_date, owner):
    """True if this worker now owns ``run_date``'s run (new, or taken over after a crash/failure)."""
    now = utcnow()
    try:
        with db.engine.begin() as conn:
            conn.execute(insert(ValiditySchedulerRun).values(
                run_date=run_date, status="running", worker_id=owner, lease_until=now + _lease(), attempts=1,
                started_at=now, documents_checked=0, notifications_created=0, resumed=0, errors=0))
        return True
    except IntegrityError:
        pass
    with db.engine.begin() as conn:
        taken = conn.execute(update(ValiditySchedulerRun).where(
            ValiditySchedulerRun.run_date == run_date, ValiditySchedulerRun.status.in_(("running", "failed")),
            ValiditySchedulerRun.lease_until < now, ValiditySchedulerRun.attempts < MAX_RUN_ATTEMPTS,
        ).values(status="running", worker_id=owner, lease_until=now + _lease(),
                 attempts=ValiditySchedulerRun.attempts + 1, started_at=now, error_text=None)).rowcount
    return taken == 1


def _finish_run(run_date, owner, values):
    with db.engine.begin() as conn:
        conn.execute(update(ValiditySchedulerRun).where(
            ValiditySchedulerRun.run_date == run_date, ValiditySchedulerRun.worker_id == owner).values(**values))


def _label(record, doc):
    return record.document_type or record.title or doc.name


def check_documents(today):
    """The daily check itself (assumes the day is claimed). Returns statistics."""
    from .service import auto_resume, enabled_folders, recipients
    stats = {"documents_checked": 0, "notifications_created": 0, "resumed": 0, "errors": 0, "suppressed": 0}

    # 3. Resume pauses whose last day has passed before checking today's alert milestones.
    for record, doc in db.session.execute(
            select(DocumentValidity, Document).join(Document, Document.id == DocumentValidity.document_id)
            .where(DocumentValidity.monitoring_state == "paused", DocumentValidity.paused_until < today)).all():
        if auto_resume(record, doc, today):
            stats["resumed"] += 1
    db.session.commit()

    enabled = enabled_folders()                                   # 1-2 (deleted folders excluded)
    if not enabled:
        return stats
    ids = db.session.execute(
        select(DocumentValidity.id).join(Document, Document.id == DocumentValidity.document_id).where(
            Document.deleted_at.is_(None),                         # 3
            Document.folder_id.in_(list(enabled)),                 # 1
            DocumentValidity.monitoring_state == "active",         # 2
            DocumentValidity.version_id == Document.current_version_id,  # 4
            DocumentValidity.verification_status == "verified",    # 5
            DocumentValidity.has_expiry.is_(True), DocumentValidity.expiry_date.is_not(None),
            DocumentValidity.expiry_date >= today,
            DocumentValidity.expiry_date <= today + timedelta(days=max(ALERT_MILESTONES)),
        ).order_by(DocumentValidity.id)).scalars().all()

    resolvers = {}  # one permission resolver per recipient for the whole run
    for record_id in ids:
        try:
            record = db.session.get(DocumentValidity, record_id)
            doc = db.session.get(Document, record.document_id)
            stats["documents_checked"] += 1
            notice = notice_for(record.expiry_date, today)
            if notice is None:
                continue
            label = _label(record, doc)
            details = {"document_id": doc.id, "document_name": doc.name, "document_type": record.document_type,
                       "vessel_name": record.vessel_name, "expiry_date": record.expiry_date.isoformat(),
                       "days_left": notice.days_left, "status": notice.kind}
            parts = [doc.name]
            if record.vessel_name:
                parts.append(f"Vessel: {record.vessel_name}")
            parts.append(f"Expiry date: {record.expiry_date.isoformat()}")
            parts.append({"countdown": "Status: expiring", "expires_today": "Status: expires today",
                          "expired": "Status: expired"}[notice.kind])
            for user in recipients(doc, resolvers):
                created = notify(
                    user.id, category="validity", kind=notice.kind, severity=notice.severity,
                    title=notice.message(label), message=" · ".join(parts), link=f"/documents/{doc.id}#validity",
                    entity_type="document", entity_id=doc.id, details=details,
                    dedupe_key=f"validity:{doc.id}:{today.isoformat()}", email=wants_email(user, notice))
                stats["notifications_created"] += 1 if created else 0
            db.session.commit()
        except Exception:
            db.session.rollback()
            stats["errors"] += 1
            log.exception("Validity check failed for record %s", record_id)
    return stats


def run_daily(today=None, owner=None):
    """Claim and run the daily check for ``today`` (default: the business date). Returns the statistics,
    or None when another worker has it or it is already done."""
    today = today or clock.today()
    owner = owner or worker_id()
    if not claim_run(today, owner):
        return None
    try:
        stats = check_documents(today)
    except Exception as exc:
        db.session.rollback()
        log.exception("Daily validity run for %s failed", today)
        _finish_run(today, owner, {"status": "failed", "finished_at": utcnow(),
                                   "lease_until": utcnow() + timedelta(minutes=15),
                                   "error_text": f"{type(exc).__name__}: {exc}"[:1000]})
        return None
    _finish_run(today, owner, {"status": "completed", "finished_at": utcnow(), "lease_until": None,
                               "documents_checked": stats["documents_checked"],
                               "notifications_created": stats["notifications_created"],
                               "resumed": stats["resumed"], "errors": stats["errors"]})
    log.info("Daily validity run %s: %s", today, stats)
    try:
        stats["emails_sent"] = deliver_pending()
    except Exception:
        db.session.rollback()
        log.exception("Sending validity emails failed; they will be retried")
    return stats


def daily_due(now_local=None):
    """True when today's run should start now (time reached and not completed yet)."""
    now_local = now_local or clock.now_local()
    if now_local.time() < clock.scheduled_time():
        return False
    status = db.session.execute(select(ValiditySchedulerRun.status).where(
        ValiditySchedulerRun.run_date == now_local.date())).scalar()
    db.session.commit()
    return status != "completed"


def worker_cycle(owner, cycle=0):
    """One pass of the background worker."""
    if not current_app.config.get("VALIDITY_MONITORING_ENABLED", True):
        return
    run_due_jobs(owner)
    if cycle % 10 == 0:
        reconcile()
    if daily_due():
        run_daily(owner=owner)
    deliver_pending()


class ValidityWorker:
    """In-process worker thread (one per process). Safe with several processes: jobs and daily runs are
    claimed in the database. For production prefer one dedicated ``flask validity-worker`` process and
    VALIDITY_WORKER_ENABLED=false in the web processes."""

    def __init__(self, app):
        self.app = app
        self.interval = max(5, int(app.config.get("VALIDITY_WORKER_INTERVAL_SECONDS", 30)))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="validity-worker", daemon=True)

    def start(self):
        self._thread.start()
        log.info("Validity worker started (every %ss, daily run at %s %s)", self.interval,
                 self.app.config.get("VALIDITY_SCHEDULER_TIME"), self.app.config.get("VALIDITY_TIMEZONE"))

    def stop(self):
        self._stop.set()
        self._wake.set()

    def trigger(self):
        self._wake.set()

    def _loop(self):
        self._stop.wait(10)
        owner, cycle = worker_id(), 0
        while not self._stop.is_set():
            with self.app.app_context():
                try:
                    worker_cycle(owner, cycle)
                except Exception:
                    log.exception("Validity worker cycle failed")
                    db.session.rollback()
                finally:
                    db.session.remove()
            cycle += 1
            self._wake.wait(self.interval)
            self._wake.clear()
