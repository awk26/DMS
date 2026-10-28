"""In-browser previews for Office documents (FR-DOC-03).

When LibreOffice is installed (LIBREOFFICE_PATH or a standard location) Office files are
converted to PDF once per content hash and the encrypted rendition is cached in storage.
Otherwise a sanitised HTML rendering is produced directly (Word via mammoth, spreadsheets
via openpyxl, presentations and OpenDocument files as text), so no native application is
needed on the viewer's machine.
"""
import glob
import html
import io
import logging
import os
import shutil
import subprocess
import tempfile

import nh3
from flask import current_app

from ..storage import StorageError, get_storage
from .text_extract import extract_bytes

log = logging.getLogger(__name__)

MAX_PREVIEW_MB = 40
_TAGS = {"a", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "div", "em", "h1", "h2", "h3", "h4",
         "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "s", "section", "small", "span", "strong", "sub",
         "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul"}
_ATTRS = {"*": {"class"}, "img": {"src", "alt"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
          "a": {"href"}}
STYLE = ("body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;line-height:1.55;"
         "color:#1f2937;margin:24px;max-width:900px}img{max-width:100%;height:auto}"
         "table{border-collapse:collapse;font-size:13px;margin:8px 0 24px}td,th{border:1px solid #d1d5db;"
         "padding:3px 6px;vertical-align:top;white-space:pre-wrap}th{background:#f3f4f6}"
         "h2.sheet,h2.slide{font-size:15px;border-bottom:1px solid #e5e7eb;padding-bottom:4px;margin-top:28px}"
         "pre{white-space:pre-wrap;font-family:inherit}.note{color:#6b7280;font-size:12px}")


def libreoffice_path():
    configured = current_app.config.get("LIBREOFFICE_PATH")
    if configured:
        return configured if os.path.isfile(configured) else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for pattern in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                    "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        for path in glob.glob(pattern):
            if os.path.isfile(path):
                return path
    return None


def _rendition_key(version):
    return f"renditions/{version.sha256}.pdf"


def pdf_rendition(version):
    """Storage key of a cached PDF rendition, converting with LibreOffice if needed (else None)."""
    storage = get_storage()
    key = _rendition_key(version)
    if storage.exists(key):
        return key
    exe = libreoffice_path()
    if not exe or version.size > MAX_PREVIEW_MB * 1024 * 1024:
        return None
    ext = version.document.extension
    workdir = tempfile.mkdtemp(prefix="dms-render-")
    try:
        src = os.path.join(workdir, f"source.{ext}")
        with storage.open(version.storage_key) as fh, open(src, "wb") as out:
            shutil.copyfileobj(fh, out)
        profile = "file:///" + os.path.join(workdir, "profile").replace("\\", "/").lstrip("/")
        subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                        "--convert-to", "pdf", "--outdir", workdir, src],
                       capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pdf = os.path.join(workdir, "source.pdf")
        if not os.path.isfile(pdf):
            log.warning("LibreOffice did not produce a PDF for document %s", version.document_id)
            return None
        with open(pdf, "rb") as fh:
            try:
                storage.save_stream(key, fh)
            except StorageError:
                pass  # converted concurrently by another request
        return key
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Office conversion failed", exc_info=True)
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _attr_filter(tag, attr, value):
    if attr in ("href", "src") and value.strip().lower().startswith("data:"):
        if tag == "img" and value.lower().startswith(("data:image/png", "data:image/jpeg", "data:image/gif")):
            return value
        return None
    return value


def _clean(fragment):
    return nh3.clean(fragment, tags=_TAGS, attributes=_ATTRS, url_schemes={"http", "https", "mailto", "data"},
                     attribute_filter=_attr_filter, link_rel="noopener noreferrer nofollow", strip_comments=True)


def _docx_html(data):
    import mammoth
    result = mammoth.convert_to_html(io.BytesIO(data))
    return _clean(result.value)


def _xlsx_html(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:10]:
        parts.append(f'<h2 class="sheet">{html.escape(ws.title)}</h2><table>')
        truncated = False
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= 1000:
                truncated = True
                break
            cells = "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in row[:60])
            parts.append(f"<tr>{cells}</tr>")
        parts.append("</table>")
        if truncated:
            parts.append('<p class="note">Only the first 1,000 rows are shown. Download the file to see everything.</p>')
    wb.close()
    return "".join(parts)


def _text_html(text, ext):
    if ext in ("pptx", "odp"):
        out = []
        for block in text.split("[Slide ")[1:] if "[Slide " in text else [text]:
            head, _, body = block.partition("]")
            out.append(f'<h2 class="slide">Slide {html.escape(head)}</h2><pre>{html.escape(body.strip())}</pre>'
                       if body else f"<pre>{html.escape(block)}</pre>")
        return "".join(out) or "<p class='note'>No text found in this presentation.</p>"
    return f"<pre>{html.escape(text)}</pre>" if text else "<p class='note'>No text found in this document.</p>"


def html_rendition(version):
    """A sanitised standalone HTML document for an Office file, or None if unsupported."""
    ext = version.document.extension
    if version.size > MAX_PREVIEW_MB * 1024 * 1024:
        return None
    data = get_storage().read_bytes(version.storage_key)
    try:
        if ext == "docx":
            body = _docx_html(data)
        elif ext == "xlsx":
            body = _xlsx_html(data)
        elif ext in ("pptx", "odt", "ods", "odp", "rtf"):
            body = _text_html(extract_bytes(data, ext), ext)
        else:
            return None
    except Exception:
        log.info("HTML preview failed for document %s", version.document_id, exc_info=True)
        return None
    note = ('<p class="note">Simplified preview: layout and some formatting may differ from the original. '
            'Install LibreOffice on the server for exact page rendering.</p>')
    return (f"<!doctype html><html><head><meta charset='utf-8'><base target='_blank'><style>{STYLE}</style>"
            f"</head><body>{note}{body}</body></html>")


def office_preview_mode(version):
    """'pdf' (LibreOffice rendition), 'html' (built-in rendering) or None."""
    ext = version.document.extension
    if libreoffice_path() and version.size <= MAX_PREVIEW_MB * 1024 * 1024:
        return "pdf"
    if ext in ("docx", "xlsx", "pptx", "odt", "ods", "odp", "rtf"):
        return "html"
    return None
