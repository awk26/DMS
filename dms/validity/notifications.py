"""In-app notification centre and email digests.

The DMS had no notification framework, so this one is generic (``category`` names the producing
module) and can be reused. Producers call ``notify``; uniqueness of (user, dedupe_key) is enforced by
the database, so a notice is created at most once however many scheduler instances run.

Email (optional, SMTP_HOST): each user chooses ``off``, ``milestones`` (default: one digest on days
with a milestone notice: 30/15/7/3/1/0 days left, the first overdue day, then weekly) or ``daily``
(one digest every day with notices). Never more than one email per user per run; failed sends are
retried with backoff. Access is re-checked when listing and when sending, so a user who lost access
to a document no longer sees or receives anything about it.
"""
import json
import logging
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..extensions import db
from ..models import Document, User, utcnow
from .models import Notification, NotificationPreference
from .rules import backoff_seconds

log = logging.getLogger(__name__)

EMAIL_MODES = ("off", "milestones", "daily")
MAX_EMAIL_ATTEMPTS = 5


# --------------------------------------------------------------------------- producing

def notify(user_id, *, category, kind, title, message, dedupe_key, link=None, severity="info",
           entity_type=None, entity_id=None, details=None, email=False):
    """Create a notification once (idempotent on (user, dedupe_key)). Returns True if it was new."""
    now = utcnow()
    stmt = mysql_insert(Notification.__table__).values(
        user_id=user_id, category=category, kind=kind, severity=severity, title=title[:300],
        message=message[:1000], link=link, entity_type=entity_type, entity_id=entity_id,
        details_json=json.dumps(details, default=str) if details else None, dedupe_key=dedupe_key[:120],
        created_at=now, email_status="pending" if email else "none", email_attempts=0,
        email_next_attempt_at=now - timedelta(seconds=1) if email else None,  # due now (DATETIME rounds)
    ).prefix_with("IGNORE")  # 0 rows when (user, dedupe_key) already exists
    return db.session.execute(stmt).rowcount == 1


def email_configured():
    return bool(current_app.extensions.get("validity_mailer") or current_app.config.get("SMTP_HOST"))


def email_mode(user_id):
    pref = db.session.get(NotificationPreference, user_id)
    mode = pref.email_mode if pref is not None else current_app.config.get("VALIDITY_EMAIL_DEFAULT_MODE",
                                                                          "milestones")
    return mode if mode in EMAIL_MODES else "milestones"


def wants_email(user, notice):
    if not email_configured() or not user.email:
        return False
    mode = email_mode(user.id)
    return mode == "daily" or (mode == "milestones" and notice.email_milestone)


# --------------------------------------------------------------------------- reading

def _visible(user, items):
    """Drop notifications about documents the user may no longer view (checked as an ordinary user)."""
    from .access import can
    out, cache = [], {}
    for n in items:
        if n.entity_type == "document" and n.entity_id:
            if n.entity_id not in cache:
                doc = db.session.get(Document, n.entity_id)
                cache[n.entity_id] = bool(doc is not None and doc.deleted_at is None and can("view", doc, user))
            if not cache[n.entity_id]:
                continue
        out.append(n)
    return out


def notification_json(n):
    return {"id": n.id, "category": n.category, "kind": n.kind, "severity": n.severity, "title": n.title,
            "message": n.message, "link": n.link, "entity_type": n.entity_type, "entity_id": n.entity_id,
            "details": n.details, "read": n.read_at is not None, "created_at": n.created_at.isoformat() + "Z"}


def list_for(user, unread_only=False, limit=50, offset=0):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.session.execute(stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
                              .limit(limit + 1).offset(offset)).scalars().all()
    more = len(rows) > limit
    return _visible(user, rows[:limit]), more


def unread_count(user, cap=200):
    rows = db.session.execute(select(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))
        .order_by(Notification.created_at.desc()).limit(cap)).scalars().all()
    return len(_visible(user, rows))


def mark_read(user, notification_id):
    n = db.session.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        return False
    if n.read_at is None:
        n.read_at = utcnow()
    db.session.commit()
    return True


def mark_all_read(user):
    db.session.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                       .values(read_at=utcnow()))
    db.session.commit()


def set_email_mode(user, mode):
    from ..validation import ValidationError
    if mode not in EMAIL_MODES:
        raise ValidationError("Choose off, milestones or daily.")
    pref = db.session.get(NotificationPreference, user.id)
    if pref is None:
        pref = NotificationPreference(user_id=user.id, email_mode=mode)
        db.session.add(pref)
    pref.email_mode = mode
    db.session.commit()


# --------------------------------------------------------------------------- email delivery

def _send(to, subject, body):
    mailer = current_app.extensions.get("validity_mailer")
    if mailer is not None:
        return mailer(to, subject, body)
    cfg = current_app.config
    msg = EmailMessage()
    msg["From"] = cfg.get("SMTP_FROM") or cfg.get("SMTP_USERNAME") or "dms@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("SMTP_PORT") or (465 if cfg.get("SMTP_SSL") else 587))
    timeout = int(cfg.get("SMTP_TIMEOUT_SECONDS") or 30)
    context = ssl.create_default_context()
    if cfg.get("SMTP_SSL"):
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=timeout)
    with server:
        if not cfg.get("SMTP_SSL") and cfg.get("SMTP_STARTTLS", True):
            server.starttls(context=context)
        if cfg.get("SMTP_USERNAME"):
            server.login(cfg["SMTP_USERNAME"], cfg.get("SMTP_PASSWORD") or "")
        server.send_message(msg)


def _digest(user, items):
    base = (current_app.config.get("VALIDITY_APP_URL") or current_app.config.get("ANGULAR_FRONTEND_URL") or "").rstrip("/")
    app_name = current_app.config.get("APP_NAME", "DMS")
    lines = [f"Hello {user.label},", "", f"Document validity alerts from {app_name}:", ""]
    for n in items:
        lines.append(f"- {n.title}")
        if base and n.link:
            lines.append(f"  {base}{n.link}")
    lines += ["", "You receive these emails because you follow these documents' expiry alerts.",
              "Change how often you get them under Notifications > Email preferences" + (f": {base}/notifications"
                                                                                       if base else ".")]
    count = len(items)
    subject = f"[{app_name}] {count} document validity alert{'s' if count != 1 else ''}"
    return subject, "\n".join(lines)


def deliver_pending(now=None, limit_users=500):
    """Send one digest per user covering their due notifications. Returns the number of emails sent."""
    if not email_configured():
        return 0
    now = now or utcnow()
    due = (Notification.email_status.in_(("pending", "failed")),
           Notification.email_attempts < MAX_EMAIL_ATTEMPTS, Notification.email_next_attempt_at <= now)
    user_ids = db.session.execute(select(Notification.user_id).where(*due).group_by(Notification.user_id)
                                  .limit(limit_users)).scalars().all()
    sent = 0
    for uid in user_ids:
        user = db.session.get(User, uid)
        items = db.session.execute(select(Notification).where(Notification.user_id == uid, *due)
                                   .order_by(Notification.created_at)).scalars().all()
        visible = {n.id for n in _visible(user, items)} if user is not None and user.is_active else set()
        for n in items:
            if n.id not in visible:
                n.email_status = "skipped"
        keep = [n for n in items if n.id in visible]
        if not keep or not user.email:
            for n in keep:
                n.email_status = "skipped"
            db.session.commit()
            continue
        subject, body = _digest(user, keep)
        try:
            _send(user.email, subject, body)
        except Exception as exc:
            log.warning("Validity email to user %s failed: %s", uid, type(exc).__name__)
            for n in keep:
                n.email_attempts += 1
                n.email_status = "failed"
                n.email_error = f"{type(exc).__name__}"[:300]
                n.email_next_attempt_at = now + timedelta(seconds=backoff_seconds(n.email_attempts, 300))
            db.session.commit()
            continue
        for n in keep:
            n.email_attempts += 1
            n.email_status = "sent"
            n.email_sent_at = now
            n.email_error = None
        db.session.commit()
        sent += 1
    return sent


def counts_by_status():
    return dict(db.session.execute(select(Notification.email_status, func.count()).group_by(
        Notification.email_status)).all())
