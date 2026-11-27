"""``flask --app app validity-*`` commands: a dedicated worker process and cron-friendly one-shots."""
import json
import time
from datetime import date

import click


def register(app):
    from ..extensions import db

    @app.cli.command("validity-worker")
    @click.option("--interval", type=int, default=None, help="Seconds between cycles (default from config).")
    def validity_worker(interval):
        """Run the validity worker in the foreground: analysis jobs, the daily expiry run, email retries.
        Run exactly one per environment as a service, with VALIDITY_WORKER_ENABLED=false for the web app
        (several are safe, but unnecessary)."""
        from .jobs import worker_id
        from .scheduler import worker_cycle
        interval = interval or app.config["VALIDITY_WORKER_INTERVAL_SECONDS"]
        owner, cycle = worker_id(), 0
        click.echo(f"Validity worker {owner} running every {interval}s "
                   f"(daily run at {app.config['VALIDITY_SCHEDULER_TIME']} {app.config['VALIDITY_TIMEZONE']}). "
                   "Press Ctrl+C to stop.")
        while True:
            try:
                worker_cycle(owner, cycle)
            except Exception as exc:  # keep running; details are in the log
                db.session.rollback()
                click.echo(f"[{time.strftime('%H:%M:%S')}] cycle failed: {type(exc).__name__}", err=True)
            finally:
                db.session.remove()
            cycle += 1
            time.sleep(interval)

    @app.cli.command("validity-run-daily")
    @click.option("--date", "run_date", default=None,
                  help="Business date YYYY-MM-DD (UAT only; default: today in VALIDITY_TIMEZONE).")
    def validity_run_daily(run_date):
        """Run today's expiry check now (for cron). A day already completed is not run again."""
        from .scheduler import run_daily
        day = date.fromisoformat(run_date) if run_date else None
        stats = run_daily(today=day)
        click.echo(json.dumps(stats) if stats is not None else "Already completed or running elsewhere.")

    @app.cli.command("validity-process-jobs")
    @click.option("--limit", type=int, default=20)
    def validity_process_jobs(limit):
        """Process up to LIMIT due analysis jobs now."""
        from .jobs import run_due_jobs
        click.echo(f"Processed {run_due_jobs(limit=limit)} job(s).")

    @app.cli.command("validity-reconcile")
    def validity_reconcile():
        """Queue analysis for new versions in monitored folders that have no job."""
        from .jobs import reconcile
        click.echo(f"Queued {reconcile(limit=10000)} job(s).")

    @app.cli.command("validity-status")
    def validity_status():
        """Show queue, email and scheduler state."""
        from sqlalchemy import func, select
        from .models import ValidityJob, ValiditySchedulerRun
        from .notifications import counts_by_status
        jobs = dict(db.session.execute(select(ValidityJob.status, func.count()).group_by(ValidityJob.status)).all())
        runs = db.session.execute(select(ValiditySchedulerRun).order_by(ValiditySchedulerRun.run_date.desc())
                                  .limit(7)).scalars().all()
        click.echo(json.dumps({
            "ai_configured": bool(app.config.get("GEMINI_API_KEY")), "model": app.config.get("GEMINI_MODEL"),
            "jobs": jobs, "emails": counts_by_status(),
            "runs": [{"date": r.run_date.isoformat(), "status": r.status, "checked": r.documents_checked,
                      "created": r.notifications_created, "errors": r.errors} for r in runs],
        }, indent=2))

    @app.cli.command("validity-diagnose")
    @click.option("--date", "run_date", default=None, help="Business date YYYY-MM-DD (default: today).")
    def validity_diagnose(run_date):
        """For every tracked document: would it get a notice today, and if not, why not."""
        from sqlalchemy import select
        from ..models import Document
        from . import clock
        from .models import DocumentValidity
        from .rules import effective_state, notice_for
        from .service import enabled_folders, recipients
        today = date.fromisoformat(run_date) if run_date else clock.today()
        enabled = enabled_folders()
        rows = db.session.execute(select(DocumentValidity, Document).join(
            Document, Document.id == DocumentValidity.document_id).order_by(Document.id)).all()
        click.echo(f"Business date {today}; alert milestones: 30, 15, 7, 3, 2, 1, 0 days left; "
                   f"{len(rows)} tracked document(s).")
        for record, doc in rows:
            notice = notice_for(record.expiry_date, today) if record.expiry_date else None
            state = effective_state(record.monitoring_state, record.paused_until, today)
            reasons = []
            if doc.deleted_at is not None:
                reasons.append("document is in the recycle bin")
            if doc.folder_id not in enabled:
                reasons.append("folder monitoring is off")
            if state != "active":
                reasons.append(f"monitoring is {state}")
            if record.version_id != doc.current_version_id:
                reasons.append("dates belong to an older version (confirm the new one)")
            if record.verification_status != "verified":
                reasons.append(f"dates not confirmed yet (status: {record.verification_status}) -> Review & confirm")
            if not record.has_expiry or not record.expiry_date:
                reasons.append("no expiry date")
            elif notice is None:
                if record.expiry_date < today:
                    reasons.append("expiry has passed; alerts stop after day 0")
                else:
                    reasons.append("today is not a scheduled alert milestone")
            if not reasons and not recipients(doc):
                reasons.append("nobody receives its alerts (owner unfollowed or lost access)")
            verdict = f"NOTICE: {notice.phrase()}" if not reasons else "no notice: " + "; ".join(reasons)
            click.echo(f"#{doc.id} {doc.name} | expiry {record.expiry_date or '-'} | "
                       f"{record.verification_status} | {verdict}")
