import io
import re

from flask import Blueprint, Response, abort, render_template, request, send_file, url_for
from sqlalchemy import func, or_, select

from ..extensions import db
from ..models import ArchivedEmail, EmailAttachment, PendingImport
from ..services.emails import SAFE_INLINE_IMAGE_TYPES, attachment_bytes, render_preview_html
from ..storage import get_storage

bp = Blueprint("emails", __name__, url_prefix="/emails")
PAGE_SIZE = 25
STATES = ("archived", "inbox", "trashed", "spam", "deleted")

# The email body is untrusted: no scripts, no remote content, no forms, sandboxed.
BODY_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; font-src data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
            "sandbox allow-popups allow-popups-to-escape-sandbox")


@bp.before_request
def _restrict():
    from flask import g
    user = getattr(g, "user", None)
    if user is None or not user.has("emails"):
        abort(403, description="You do not have access to the email archive.")


def _get(email_id):
    record = db.session.get(ArchivedEmail, email_id)
    if record is None:
        abort(404, description="Email not found.")
    return record


@bp.get("/")
def index():
    q = (request.args.get("q") or "").strip()
    state = request.args.get("state") or ""
    has_att = request.args.get("attachments") == "1"
    sort = request.args.get("sort") if request.args.get("sort") in ("received", "imported", "subject", "sender") \
        else "imported"
    page = max(1, request.args.get("page", 1, type=int))
    stmt = select(ArchivedEmail)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(ArchivedEmail.subject.ilike(like), ArchivedEmail.sender.ilike(like),
                              ArchivedEmail.recipients.ilike(like), ArchivedEmail.snippet.ilike(like)))
    if state in STATES:
        stmt = stmt.where(ArchivedEmail.gmail_state == state)
    if has_att:
        stmt = stmt.where(ArchivedEmail.attachment_count > 0)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    col = {"received": ArchivedEmail.received_at, "imported": ArchivedEmail.imported_at,
           "subject": func.lower(ArchivedEmail.subject), "sender": func.lower(ArchivedEmail.sender)}[sort]
    stmt = stmt.order_by(col.asc() if sort in ("subject", "sender") else col.desc(), ArchivedEmail.id.desc())
    emails = db.session.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).scalars().all()
    queue = db.session.execute(select(PendingImport).order_by(PendingImport.id)).scalars().all()
    return render_template("emails/index.html", emails=emails, total=total, page=page,
                           pages=max(1, -(-total // PAGE_SIZE)), q=q, state=state, has_att=has_att,
                           sort=sort, states=STATES, queue=queue)


@bp.get("/<int:email_id>")
def detail(email_id):
    record = _get(email_id)
    integrity_ok = get_storage().exists(record.storage_key)
    return render_template("emails/detail.html", email=record, file_ok=integrity_ok)


@bp.get("/<int:email_id>/body")
def body(email_id):
    record = _get(email_id)
    try:
        html_doc = render_preview_html(
            record, lambda idx: url_for("emails.inline_part", email_id=record.id, part_index=idx))
    except FileNotFoundError:
        abort(404, description="The original message file is missing.")
    resp = Response(html_doc, mimetype="text/html")
    resp.headers["Content-Security-Policy"] = BODY_CSP
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _safe_filename(name):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name or "").strip(" .")
    return name[:200] or "attachment"


@bp.get("/<int:email_id>/eml")
def download_eml(email_id):
    """The original message exactly as received from Gmail."""
    record = _get(email_id)
    storage = get_storage()
    if not storage.exists(record.storage_key):
        abort(404, description="The original message file is missing.")
    subject = _safe_filename(record.subject)[:80] or "message"
    from ._helpers import stream_stored
    return stream_stored(record.storage_key, "message/rfc822", f"{subject} [{record.gmail_message_id}].eml",
                         as_attachment=True, etag=record.sha256)


@bp.get("/<int:email_id>/attachments/<int:part_index>")
def download_attachment(email_id, part_index):
    record = _get(email_id)
    if record.scan_status == "infected":
        abort(403, description="This email was flagged as containing malware; its attachments cannot be downloaded.")
    att = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index)).scalar_one_or_none()
    if att is None:
        abort(404, description="Attachment not found.")
    data = attachment_bytes(record, part_index)
    if data is None:
        abort(404, description="Attachment not found in the original message.")
    return send_file(io.BytesIO(data), mimetype="application/octet-stream", as_attachment=True,
                     download_name=_safe_filename(att.filename), max_age=0)


@bp.get("/<int:email_id>/inline/<int:part_index>")
def inline_part(email_id, part_index):
    """Inline images (cid:) referenced by the email body. Safe raster formats only."""
    record = _get(email_id)
    att = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index)).scalar_one_or_none()
    if att is None or att.content_type not in SAFE_INLINE_IMAGE_TYPES:
        abort(404)
    data = attachment_bytes(record, part_index)
    if data is None:
        abort(404)
    resp = send_file(io.BytesIO(data), mimetype=att.content_type, max_age=3600)
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp
