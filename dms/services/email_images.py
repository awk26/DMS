"""Images that archived emails link to instead of embedding (``<img src="https://...">``).

Most newsletters (Mailchimp and similar) keep their logos and pictures on the sender's servers, so the
.eml holds only their addresses. The preview never loads anything from the internet, so those images are
downloaded once by the server, stored (encrypted) with the email and shown from the DMS. The archived
.eml itself is never changed; ``eml_with_images`` builds a separate copy with the images embedded.

Downloads are restricted: http(s) only, public addresses only (every redirect is checked again), size,
count and time limits, and only real PNG/JPEG/GIF/WebP/BMP data is kept (never SVG or anything else).
"""
import hashlib
import html
import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from email import policy
from urllib.parse import urljoin, urlsplit

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import EmailImage, utcnow
from ..storage import get_storage

log = logging.getLogger(__name__)

_IMG_TAG_RE = re.compile(r"<(img|table|td|th|tr|body|div)\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\s(src|background)\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"""url\(\s*(["']?)([^"')]+?)\1\s*\)""", re.IGNORECASE)
_MAX_REDIRECTS = 3
_WORKERS = 6


def _remote(value):
    """The absolute http(s) URL in an attribute/CSS value, or None."""
    url = html.unescape(value or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url if url.lower().startswith(("http://", "https://")) and len(url) <= 2000 else None


def transform(text, fn):
    """Rewrite every remote image URL in an HTML body: ``fn(url)`` returns the replacement or None.

    Covers <img src>, background attributes and CSS url(...) in style attributes and <style> blocks."""
    def tag_repl(m):
        tag = m.group(0)
        is_img = m.group(1).lower() == "img"

        def attr_repl(a):
            if a.group(2).lower() == "src" and not is_img:
                return a.group(0)
            url = _remote(a.group(3) if a.group(3) is not None else a.group(4) if a.group(4) is not None
                          else a.group(5))
            new = fn(url) if url else None
            return f'{a.group(1)}"{html.escape(new, quote=True)}"' if new else a.group(0)
        return _ATTR_RE.sub(attr_repl, tag)

    def css_repl(m):
        url = _remote(m.group(2))
        new = fn(url) if url else None
        return f"url('{new}')" if new else m.group(0)

    return _CSS_URL_RE.sub(css_repl, _IMG_TAG_RE.sub(tag_repl, text))


def image_urls(html_texts):
    """Remote image URLs in the bodies, in order, without duplicates."""
    found = []

    def collect(url):
        if url not in found:
            found.append(url)
        return None
    for text in html_texts:
        transform(text, collect)
    return found


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- downloading

class Refused(Exception):
    pass


def _check_public(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("not an http(s) address")
    if parts.username or parts.password:
        raise Refused("credentials in the address")
    if parts.port not in (None, 80, 443):
        raise Refused("non-standard port")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise Refused("host not found") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise Refused("address is not public")


def download(url, max_bytes, timeout):
    """(bytes, image type) for a public image URL. Raises Refused or requests exceptions."""
    import requests
    from .emails import sniff_image_type
    for _ in range(_MAX_REDIRECTS + 1):
        _check_public(url)
        with requests.get(url, stream=True, allow_redirects=False, timeout=(5, timeout),
                          headers={"User-Agent": "DMS-Email-Archive/1.0", "Accept": "image/*"}) as resp:
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise Refused("redirect without a location")
                url = urljoin(url, location)
                continue
            if resp.status_code != 200:
                raise Refused(f"HTTP {resp.status_code}")
            data = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise Refused("image too large")
        kind = sniff_image_type(bytes(data))
        if kind is None:
            raise Refused("not a PNG, JPEG, GIF, WebP or BMP image")
        return bytes(data), kind
    raise Refused("too many redirects")


def _attempt(url, max_bytes, timeout):
    try:
        data, kind = download(url, max_bytes, timeout)
        return url, data, kind, None
    except Refused as exc:
        return url, None, None, str(exc)
    except Exception as exc:  # network errors, timeouts
        return url, None, None, f"{type(exc).__name__}: {exc}"[:300]


def fetch_images(record, retry_failed=False):
    """Download and store the images ``record``'s body links to. Returns the number newly stored.

    Runs at most once per email (``images_fetched_at``) unless ``retry_failed``. Commits."""
    from .emails import _part_text, body_parts, load_message
    cfg = current_app.config
    if not cfg.get("EMAIL_REMOTE_IMAGES") or record.scan_status == "infected":
        return 0
    if record.images_fetched_at is not None and not retry_failed:
        return 0
    try:
        msg = load_message(record)
    except FileNotFoundError:
        return 0
    urls = image_urls([_part_text(p) for p in body_parts(msg) if p.get_content_type() == "text/html"])
    urls = urls[: cfg.get("EMAIL_IMAGE_MAX_COUNT", 60)]
    have = {row.url_hash: row for row in db.session.execute(
        select(EmailImage).where(EmailImage.email_id == record.id)).scalars()}
    todo = [u for u in urls if url_hash(u) not in have or (retry_failed and have[url_hash(u)].status == "failed")]
    max_bytes = cfg.get("EMAIL_IMAGE_MAX_MB", 5) * 1024 * 1024
    timeout = cfg.get("EMAIL_IMAGE_TIMEOUT_SECONDS", 10)
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:  # network only; storage/DB stay on this thread
        results = list(pool.map(lambda u: _attempt(u, max_bytes, timeout), todo))
    storage = get_storage()
    stored = 0
    for url, data, kind, error in results:
        h = url_hash(url)
        row = have.get(h) or EmailImage(email_id=record.id, url=url, url_hash=h, status="failed")
        if data is not None:
            key = f"emailimg/{record.id}/{h}"
            size, _ = storage.save_bytes(key, data, overwrite=True)
            row.status, row.storage_key, row.content_type, row.size, row.error = "stored", key, kind, size, None
            stored += 1
        else:
            row.status, row.error = "failed", (error or "")[:300]
            log.info("Email #%s: image not downloaded (%s): %s", record.id, error, url[:200])
        row.fetched_at = utcnow()
        db.session.add(row)
    record.images_fetched_at = utcnow()
    try:
        db.session.commit()
    except IntegrityError:  # another request stored the same images at the same moment
        db.session.rollback()
    return stored


def stored_images(record):
    """{url: EmailImage} for the images of ``record`` that are stored."""
    rows = db.session.execute(select(EmailImage).where(
        EmailImage.email_id == record.id, EmailImage.status == "stored")).scalars()
    return {row.url: row for row in rows}


# --------------------------------------------------------------------------- .eml copy with images

def eml_with_images(record):
    """A copy of the archived message with the stored linked images embedded (cid:), as bytes.

    The archived original is not modified; this copy is built on request."""
    from .emails import _part_text, body_parts, load_message
    msg = load_message(record)
    images = stored_images(record)
    storage = get_storage()
    if images:
        for part in body_parts(msg):
            if part.get_content_type() != "text/html":
                continue
            used = {}

            def to_cid(url):
                row = images.get(url)
                if row is None:
                    return None
                cid = f"dms-img-{row.id}@archive.local"
                used[row.id] = (row, cid)
                return f"cid:{cid}"
            new_html = transform(_part_text(part), to_cid)
            if not used:
                continue
            part.set_content(new_html, subtype="html")
            for row, cid in used.values():
                maintype, subtype = row.content_type.split("/", 1)
                part.add_related(storage.read_bytes(row.storage_key), maintype=maintype, subtype=subtype,
                                 cid=f"<{cid}>")
    del msg["X-DMS-Export"]
    msg["X-DMS-Export"] = (f"Copy with linked images embedded. The archived original is unchanged "
                           f"(SHA-256 {record.sha256}).")
    return msg.as_bytes(policy=policy.SMTP)
