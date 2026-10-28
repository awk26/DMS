"""Plain-text extraction for full-text search and version comparison."""
import io
import logging
import re
import zipfile

from lxml import etree

log = logging.getLogger(__name__)

TEXT_LIMIT = 2_000_000  # characters kept per item
TEXT_EXTS = {"txt", "csv", "tsv", "md", "json", "xml", "log", "yaml", "yml", "ini", "html", "htm", "svg",
             "dxf", "step", "stp", "ifc", "iges", "igs"}
EXTRACTABLE = TEXT_EXTS | {"pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp", "rtf", "eml", "vsdx"}
_WS = re.compile(r"[ \t\r\f\v]+")


def _decode(data):
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _xml_text(data, para_tags=()):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    root = etree.fromstring(data, parser=parser)
    if root is None:
        return ""
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        if el.text and tag not in ("style", "script"):
            out.append(el.text)
        if tag in para_tags:
            out.append("\n")
        if el.tail:
            out.append(el.tail)
    return "".join(out)


def _pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= 500:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = ["word/document.xml"] + sorted(n for n in zf.namelist()
                                               if re.match(r"word/(header|footer|footnotes|endnotes)\d*\.xml", n))
        parts = []
        for n in names:
            if n in zf.namelist():
                parts.append(_xml_text(zf.read(n), para_tags=("p", "br", "tab")))
    return "\n".join(parts)


def _xlsx(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets[:50]:
        lines.append(f"[{ws.title}]")
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= 20000:
                break
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                lines.append("\t".join(cells).rstrip())
    wb.close()
    return "\n".join(lines)


def _pptx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = sorted((n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        return "\n".join(f"[Slide {i + 1}]\n" + _xml_text(zf.read(n), para_tags=("p",))
                         for i, n in enumerate(slides))


def _vsdx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        pages = sorted(n for n in zf.namelist() if re.match(r"visio/pages/page\d+\.xml$", n))
        return "\n".join(_xml_text(zf.read(n), para_tags=("Text",)) for n in pages)


def _odf(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return _xml_text(zf.read("content.xml"), para_tags=("p", "h", "table-row", "line-break", "tab"))


def _rtf(data):
    text = _decode(data)
    text = re.sub(r"\\par[d]?", "\n", text)
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)
    text = re.sub(r"\\'[0-9a-f]{2}", "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    return text.replace("{", "").replace("}", "")


def _eml(data):
    from .emails import email_text
    return email_text(data)


def extract_bytes(data, ext):
    """Best-effort plain text for ``data`` of type ``ext`` ('' when not extractable)."""
    ext = (ext or "").lower()
    try:
        if ext in ("html", "htm", "svg"):
            import nh3
            text = nh3.clean(_decode(data), tags=set())
        elif ext in TEXT_EXTS:
            text = _decode(data)
        elif ext == "pdf":
            text = _pdf(data)
        elif ext == "docx":
            text = _docx(data)
        elif ext == "xlsx":
            text = _xlsx(data)
        elif ext == "pptx":
            text = _pptx(data)
        elif ext == "vsdx":
            text = _vsdx(data)
        elif ext in ("odt", "ods", "odp"):
            text = _odf(data)
        elif ext == "rtf":
            text = _rtf(data)
        elif ext == "eml":
            text = _eml(data)
        else:
            return ""
    except Exception:
        log.info("Text extraction failed for a .%s file", ext, exc_info=True)
        return ""
    text = _WS.sub(" ", text.replace("\x00", ""))
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text[:TEXT_LIMIT].strip()


def extract_version(version, max_mb=50):
    from ..storage import get_storage
    if version is None or version.size > max_mb * 1024 * 1024:
        return ""
    ext = version.document.extension
    if ext not in EXTRACTABLE:
        return ""
    try:
        data = get_storage().read_bytes(version.storage_key)
    except Exception:
        return ""
    return extract_bytes(data, ext)
