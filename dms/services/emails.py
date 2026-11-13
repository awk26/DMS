"""Archived email storage, metadata extraction and safe preview rendering.

The original raw MIME bytes from Gmail are written to the ``.eml`` file exactly
as received. Everything in this module that parses a message works on a copy
and never writes back to the stored file.
"""
import html
import logging
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

import nh3
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import sp
from ..extensions import db
from ..models import ArchivedEmail, EmailAttachment, utcnow
from ..storage import get_storage
from .activity import log_activity

log = logging.getLogger(__name__)

MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
_WS = re.compile(r"\s+")
_CID_RE = re.compile(r"""(src|background)\s*=\s*(["']?)cid:([^"'\s>]+)\2""", re.IGNORECASE)
# cid: references inside CSS (style attributes and <style> blocks): url(cid:logo) / url('cid:logo')
_CSS_CID_RE = re.compile(r"""url\(\s*(["']?)cid:([^"')\s]+)\1\s*\)""", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"@import\b[^;]*;?", re.IGNORECASE)
_DATA_IMAGE_RE = re.compile(r"^data:image/(png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+/=\s]+$", re.IGNORECASE)

ALLOWED_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "big", "blockquote", "br", "caption",
    "center", "cite", "code", "col", "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "figcaption",
    "figure", "font", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "img", "ins", "kbd",
    "li", "main", "mark", "nav", "ol", "p", "pre", "q", "s", "samp", "section", "small", "span", "strike",
    "strong", "style", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul",
    "var", "wbr",
}
# Removed together with their content (never shown as text in the preview).
CLEAN_CONTENT_TAGS = {"script", "title", "noscript", "template", "iframe", "object", "embed", "svg", "math"}
_COMMON_ATTRS = {"style", "align", "valign", "width", "height", "bgcolor", "color", "dir", "title", "class", "lang"}
ALLOWED_ATTRS = {
    "*": _COMMON_ATTRS,
    "a": {"href", "name"},
    "img": {"src", "alt", "border", "hspace", "vspace"},
    "font": {"face", "size"},
    "table": {"border", "cellpadding", "cellspacing", "background", "summary"},
    "td": {"colspan", "rowspan", "nowrap", "background"},
    "th": {"colspan", "rowspan", "nowrap", "background"},
    "col": {"span"},
    "ol": {"start", "type"},
}

SAFE_INLINE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


def sniff_image_type(data):
    """The image type of ``data`` from its leading bytes (None if it is not a safe image type).

    Mail clients often label embedded logos as image/jpg or application/octet-stream."""
    head = bytes(data[:16]) if data else b""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:2] == b"BM":
        return "image/bmp"
    return None


def _attribute_filter(tag, attr, value):
    """Allow data: URLs only for base64 images in img src / background; drop them everywhere else."""
    if value.strip().lower().startswith("data:"):
        if attr in ("src", "background") and _DATA_IMAGE_RE.match(value.strip()):
            return value.strip()
        return None
    return value


def state_from_labels(label_ids):
    labels = set(label_ids or [])
    if "TRASH" in labels:
        return "trashed"
    if "SPAM" in labels:
        return "spam"
    if "INBOX" in labels:
        return "inbox"
    return "archived"


def parse_message(raw_bytes):
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def _header(msg, name):
    try:
        value = msg.get(name)
        return _WS.sub(" ", str(value)).strip() if value is not None else ""
    except Exception:  # malformed header: fall back to the raw value
        raw = msg.get_all(name, failobj=[""])
        return _WS.sub(" ", str(raw[0])).strip()


def _part_text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _part_bytes(part):
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()
        if isinstance(inner, list) and inner:
            return inner[0].as_bytes()
        return b""
    return part.get_payload(decode=True) or b""


part_bytes = _part_bytes


def iter_attachments(msg):
    """Yield (part_index, part, info) for attachment-like and inline-resource parts."""
    for index, part in enumerate(msg.walk()):
        ctype = part.get_content_type()
        if part.is_multipart() and ctype != "message/rfc822":  # a forwarded email is an attachment
            continue
        disposition = (part.get_content_disposition() or "").lower()
        try:
            filename = part.get_filename()
        except Exception:
            filename = None
        content_id = (part.get("Content-ID") or "").strip().strip("<>") or None
        is_body_text = ctype in ("text/plain", "text/html") and not filename and disposition != "attachment"
        if is_body_text:
            continue
        if not (filename or disposition in ("attachment", "inline") or content_id or ctype == "message/rfc822"):
            continue
        if not filename:
            ext = {"message/rfc822": ".eml"}.get(ctype, "")
            sub = ctype.split("/")[-1] if "/" in ctype else "bin"
            filename = f"attachment-{index}{ext or '.' + re.sub(r'[^a-z0-9]', '', sub)[:8]}"
        yield index, part, {
            "part_index": index,
            "filename": filename[:255],
            "content_type": ctype[:127],
            "size": len(_part_bytes(part)),
            "content_id": content_id[:255] if content_id else None,
            "is_inline": disposition == "inline" or (bool(content_id) and disposition != "attachment"),
        }


def extract_metadata(raw_bytes):
    """Derive searchable metadata from raw MIME bytes (tolerant of malformed messages)."""
    meta = {"subject": "", "sender": "", "recipients": "", "cc": "", "message_id_header": None,
            "date": None, "snippet": "", "attachments": []}
    try:
        msg = parse_message(raw_bytes)
    except Exception:
        log.warning("Could not parse message for metadata", exc_info=True)
        return meta
    meta["subject"] = _header(msg, "Subject")[:998]
    meta["sender"] = _header(msg, "From")[:998]
    meta["recipients"] = _header(msg, "To")
    meta["cc"] = _header(msg, "Cc")
    meta["message_id_header"] = _header(msg, "Message-ID")[:998] or None
    try:
        dt = parsedate_to_datetime(_header(msg, "Date"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        meta["date"] = dt
    except Exception:
        pass
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            text = _part_text(body)
            if body.get_content_type() == "text/html":
                text = nh3.clean(text, tags=set())
                text = html.unescape(text)
            meta["snippet"] = _WS.sub(" ", text).strip()[:300]
    except Exception:
        pass
    try:
        meta["attachments"] = [info for _, _, info in iter_attachments(msg)]
    except Exception:
        log.warning("Could not enumerate attachments", exc_info=True)
    return meta


def _scan_email(raw_bytes):
    """Malware-scan the raw message (attachments included). The archived copy is always kept;
    an infected message is flagged and its attachments cannot be downloaded or imported."""
    from ..security.malware import scan_bytes
    try:
        return scan_bytes(raw_bytes)
    except Exception as exc:  # scanning must never lose an archived email
        log.error("Email malware scan failed: %s", exc)
        from ..security.malware import ScanResult
        return ScanResult("error", "unknown", str(exc)[:200])


def email_text(raw_bytes, max_attachment_mb=20):
    """Searchable text of a message: headers, body and the text of its attachments."""
    from .text_extract import EXTRACTABLE, extract_bytes
    from ..validation import extension_of
    msg = parse_message(raw_bytes)
    parts = [_header(msg, h) for h in ("Subject", "From", "To", "Cc")]
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        body = None
    if body is not None:
        text = _part_text(body)
        if body.get_content_type() == "text/html":
            text = html.unescape(nh3.clean(text, tags=set()))
        parts.append(text)
    for _, part, info in iter_attachments(msg):
        if info["is_inline"] or info["size"] > max_attachment_mb * 1024 * 1024:
            continue
        ext = extension_of(info["filename"])
        parts.append(info["filename"])
        if ext in EXTRACTABLE and ext != "eml":
            parts.append(extract_bytes(_part_bytes(part), ext))
    return "\n".join(p for p in parts if p)


def email_storage_key(account_id, gmail_message_id):
    if not MESSAGE_ID_RE.match(gmail_message_id or ""):
        raise ValueError("Invalid Gmail message id")
    return f"emails/{int(account_id)}/{gmail_message_id}.eml"


def store_email(account, gmail_message_id, raw_bytes, *, thread_id=None, label_ids=None,
                internal_date_ms=None, gmail_snippet=None, source="sync"):
    """Persist an original email. Returns (ArchivedEmail, created).

    The EML file is written (atomically, fsync'd) before the database row is
    committed, so a committed row always points at a complete file. If the
    commit fails, the file is removed; if the process dies in between, the
    leftover file is simply overwritten by the next attempt.
    """
    existing = db.session.execute(select(ArchivedEmail).where(
        ArchivedEmail.account_id == account.id, ArchivedEmail.gmail_message_id == gmail_message_id)
    ).scalar_one_or_none()
    if existing:
        return existing, False
    if not raw_bytes:
        raise ValueError("Empty raw message")

    storage = get_storage()
    key = email_storage_key(account.id, gmail_message_id)
    size, sha = storage.save_bytes(key, raw_bytes, overwrite=True)
    if size != len(raw_bytes):
        storage.delete(key)
        raise IOError("Short write while saving EML")

    meta = extract_metadata(raw_bytes)
    received = meta["date"]
    if internal_date_ms:
        try:
            received = datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        record = ArchivedEmail(
            account_id=account.id, gmail_message_id=gmail_message_id, gmail_thread_id=thread_id,
            message_id_header=meta["message_id_header"], subject=meta["subject"], sender=meta["sender"],
            recipients=meta["recipients"], cc=meta["cc"],
            snippet=(meta["snippet"] or html.unescape(gmail_snippet or ""))[:500],
            received_at=received, storage_key=key, size=size, sha256=sha,
            attachment_count=sum(1 for a in meta["attachments"] if not a["is_inline"]),
            source=source, gmail_state=state_from_labels(label_ids) if label_ids is not None else "archived",
            label_ids=",".join(label_ids or []),
        )
        record.attachments = [EmailAttachment(**a) for a in meta["attachments"]]
        scan = _scan_email(raw_bytes)
        record.scan_status, record.scan_detail = scan.status, (scan.detail or None)
        db.session.add(record)
        db.session.flush()
        from .search import index_email
        index_email(record, raw_bytes)
        # The activity log is visible to auditors, so it names the mailbox but never the email's content.
        log_activity("email", "archived", f"Archived an email from Gmail account {account.email}", record.id)
        if scan.status == "infected":
            log_activity("security", "malware_flagged",
                         f"An archived email from {account.email} contains malware "
                         f"({scan.detail}); attachment downloads are blocked", record.id)
        db.session.commit()
        return record, True
    except IntegrityError:
        db.session.rollback()
        existing = db.session.execute(select(ArchivedEmail).where(
            ArchivedEmail.account_id == account.id, ArchivedEmail.gmail_message_id == gmail_message_id)
        ).scalar_one_or_none()
        if existing:
            return existing, False  # another worker stored it; the file at `key` is theirs
        storage.delete(key)
        raise
    except Exception:
        db.session.rollback()
        storage.delete(key)
        raise


# ------------------------------------------------------------------- ownership (privacy)

def owned_email(user, email_id):
    """The archived email if it came from ``user``'s own mailbox, else None.

    Nobody, administrators included, can open another user's archived emails.
    """
    if user is None or not email_id:
        return None
    row = sp.call_one("sp_email_get_owned", user.id, int(email_id), flush=False, expire=False)
    return db.session.get(ArchivedEmail, row["id"]) if row else None


def list_owned(user, q="", state="", with_attachments=False, sort="imported", limit=25, offset=0):
    """(emails, total) from the user's own mailboxes, in the requested order."""
    rows = sp.call("sp_email_list", user.id, q, state, int(bool(with_attachments)), sort, limit, offset,
                   flush=False, expire=False)
    total = rows[0]["total"] if rows else 0
    ids = [r["id"] for r in rows if r["id"] is not None]
    by_id = {e.id: e for e in db.session.execute(
        select(ArchivedEmail).where(ArchivedEmail.id.in_(ids))).scalars()} if ids else {}
    return [by_id[i] for i in ids if i in by_id], total


def update_state(record, label_ids=None, deleted=False):
    new_state = "deleted" if deleted else state_from_labels(label_ids)
    if label_ids is not None:
        record.label_ids = ",".join(label_ids)
    if new_state != record.gmail_state:
        old = record.gmail_state
        record.gmail_state = new_state
        record.state_updated_at = utcnow()
        log_activity("email", "state_changed", f"Archived email #{record.id}: {old} → {new_state} in Gmail",
                     record.id)
        return True
    return False


# ------------------------------------------------------------------- preview / download

def load_message(record):
    return parse_message(get_storage().read_bytes(record.storage_key))


def find_part(msg, part_index):
    for index, part in enumerate(msg.walk()):
        if index == part_index and (not part.is_multipart() or part.get_content_type() == "message/rfc822"):
            return part
    return None


def attachment_bytes(record, part_index):
    part = find_part(load_message(record), part_index)
    return _part_bytes(part) if part is not None else None


def _is_body_leaf(part):
    """A text part that is message content rather than an attached file."""
    if part.get_content_type() not in ("text/plain", "text/html"):
        return False
    if (part.get_content_disposition() or "").lower() == "attachment":
        return False
    try:
        return not part.get_filename()
    except Exception:
        return True


def body_parts(part):
    """The text parts that make up the visible message, in display order.

    multipart/alternative -> the richest version (HTML before plain text); multipart/related -> its
    root part; multipart/mixed and others -> every body part in order (some mail clients split one
    message into several HTML parts). Attached files, including forwarded messages, are never
    part of the body.
    """
    ctype = part.get_content_type()
    if ctype == "message/rfc822":
        return []
    if not part.is_multipart():
        return [part] if _is_body_leaf(part) else []
    try:
        subs = list(part.iter_parts())
    except Exception:
        return []
    subtype = part.get_content_subtype()
    if subtype == "alternative":
        options = [found for found in (body_parts(s) for s in subs) if found]
        for found in reversed(options):
            if any(p.get_content_type() == "text/html" for p in found):
                return found
        return options[-1] if options else []
    if subtype == "related":
        return body_parts(subs[0]) if subs else []
    out = []
    for sub in subs:
        out.extend(body_parts(sub))
    return out


def _referenced_cids(html_texts):
    cids = set()
    for text in html_texts:
        cids.update(m.group(3).lower() for m in _CID_RE.finditer(text))
        cids.update(m.group(2).lower() for m in _CSS_CID_RE.finditer(text))
    return cids


def inline_image_type(part, info=None):
    """The image type to serve an inline part as, or None if it is not a safe image."""
    sniffed = sniff_image_type(_part_bytes(part))
    if sniffed:
        return sniffed
    ctype = (info or {}).get("content_type") or part.get_content_type()
    return ctype if ctype in SAFE_INLINE_IMAGE_TYPES else None


def _inline_map(msg, html_texts):
    """{content-id: part index} for images the HTML body actually shows (cid: references).

    Only these count as inline images; every other part, including images that are attached
    but not shown in the body, is an attachment."""
    wanted = _referenced_cids(html_texts)
    found = {}
    if not wanted:
        return found
    for index, part, info in iter_attachments(msg):
        cid = (info["content_id"] or "").lower()
        if cid and cid in wanted and cid not in found and inline_image_type(part, info):
            found[cid] = index
    return found


def _html_texts(parts):
    return [_part_text(p) for p in parts if p.get_content_type() == "text/html"]


def inline_part_indexes(msg):
    """Part indexes of the images shown inside the message body."""
    return set(_inline_map(msg, _html_texts(body_parts(msg))).values())


def _sanitise_html(raw_html, cid_map, inline_url, remote_map=None):
    def repl(m):
        idx = cid_map.get(m.group(3).lower())
        return f'{m.group(1)}="{inline_url(idx)}"' if idx is not None else f'{m.group(1)}=""'

    def css_repl(m):
        idx = cid_map.get(m.group(2).lower())
        return f"url('{inline_url(idx)}')" if idx is not None else "none"

    raw_html = _CID_RE.sub(repl, raw_html)
    raw_html = _CSS_CID_RE.sub(css_repl, raw_html)
    raw_html = _CSS_IMPORT_RE.sub("", raw_html)  # no external stylesheets
    if remote_map:  # linked images that were downloaded: shown from the DMS, never from the internet
        from .email_images import transform
        raw_html = transform(raw_html, remote_map.get)
    return nh3.clean(
        raw_html, tags=ALLOWED_TAGS, clean_content_tags=CLEAN_CONTENT_TAGS, attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto", "data"}, attribute_filter=_attribute_filter,
        link_rel="noopener noreferrer nofollow", strip_comments=True,
    )


def render_preview_html(record, inline_url, image_url=None):
    """Return a sanitised, self-contained HTML document for the email body.

    The complete body is shown (text, formatting, styles and the images embedded in it).
    ``inline_url(part_index)`` builds the URL used for ``cid:`` inline images. Attached files are
    never rendered in the body; the API lists them separately with name, type and size only.
    """
    msg = load_message(record)
    parts = body_parts(msg)
    cid_map = _inline_map(msg, _html_texts(parts))
    remote_map = None
    if image_url is not None:
        from .email_images import stored_images
        remote_map = {url: image_url(row.id) for url, row in stored_images(record).items()}

    chunks = []
    for part in parts:
        if part.get_content_type() == "text/html":
            chunks.append(_sanitise_html(_part_text(part), cid_map, inline_url, remote_map))
        else:
            chunks.append(f'<pre class="plain">{html.escape(_part_text(part))}</pre>')
    chunks = [c for c in chunks if c.strip()]
    if len(chunks) > 1:
        body_html = "".join(f'<div class="mail-part">{c}</div>' for c in chunks)
    elif chunks:
        body_html = chunks[0]
    else:
        body_html = '<p class="empty">This message has no readable text body.</p>'

    return (
        "<!doctype html><html><head><meta charset='utf-8'><base target='_blank'>"
        "<style>body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;"
        "line-height:1.5;color:#1f2937;margin:16px;word-wrap:break-word}img{max-width:100%;height:auto}"
        "pre.plain{white-space:pre-wrap;font-family:inherit;margin:0}.empty{color:#6b7280}"
        ".mail-part+.mail-part{margin-top:12px}</style>"
        f"</head><body>{body_html}</body></html>"
    )
