"""Upload validation: file types (admin allow/block lists), size limits, content (magic-byte)
checks, and naming conventions."""
import os
import re
import unicodedata
import zipfile

from werkzeug.utils import secure_filename


class ValidationError(Exception):
    """A user-correctable problem with the request."""


class ConflictError(ValidationError):
    """The item changed (or is being changed) by someone else since the request was prepared (HTTP 409).

    ``current`` carries details the client can show or retry with, e.g. the current version.
    """

    code = "conflict"

    def __init__(self, message, **current):
        super().__init__(message)
        self.current = current


class DuplicateDocumentError(ConflictError):
    """An upload's name matches a document already in the folder and the user has not confirmed
    replacing it (HTTP 409, code ``duplicate_document``). Nothing was stored."""

    code = "duplicate_document"


# extension -> (mime type, content checker name). Extensions an administrator allows that are not
# listed here are accepted as application/octet-stream without a content check.
KNOWN_TYPES = {
    # Documents
    "pdf": ("application/pdf", "pdf"),
    "doc": ("application/msword", "ole"),
    "xls": ("application/vnd.ms-excel", "ole"),
    "ppt": ("application/vnd.ms-powerpoint", "ole"),
    "msg": ("application/vnd.ms-outlook", "ole"),
    "vsd": ("application/vnd.visio", "ole"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "ooxml:word/"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "ooxml:xl/"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "ooxml:ppt/"),
    "vsdx": ("application/vnd.ms-visio.drawing", "ooxml:visio/"),
    "odt": ("application/vnd.oasis.opendocument.text", "odf"),
    "ods": ("application/vnd.oasis.opendocument.spreadsheet", "odf"),
    "odp": ("application/vnd.oasis.opendocument.presentation", "odf"),
    "odg": ("application/vnd.oasis.opendocument.graphics", "odf"),
    "rtf": ("application/rtf", "rtf"),
    # Text
    "txt": ("text/plain", "text"),
    "csv": ("text/csv", "text"),
    "tsv": ("text/tab-separated-values", "text"),
    "md": ("text/markdown", "text"),
    "json": ("application/json", "text"),
    "xml": ("application/xml", "text"),
    "log": ("text/plain", "text"),
    "yaml": ("application/yaml", "text"),
    "yml": ("application/yaml", "text"),
    "ini": ("text/plain", "text"),
    "eml": ("message/rfc822", "text"),
    "html": ("text/html", "text"),   # not allowed by default (active content); never shown inline
    "htm": ("text/html", "text"),
    "svg": ("image/svg+xml", "text"),
    # Images
    "png": ("image/png", "png"),
    "jpg": ("image/jpeg", "jpeg"),
    "jpeg": ("image/jpeg", "jpeg"),
    "gif": ("image/gif", "gif"),
    "webp": ("image/webp", "webp"),
    "bmp": ("image/bmp", "bmp"),
    "tif": ("image/tiff", "tiff"),
    "tiff": ("image/tiff", "tiff"),
    # CAD
    "dwg": ("image/vnd.dwg", "dwg"),
    "dxf": ("image/vnd.dxf", "dxf"),
    "dwf": ("model/vnd.dwf", "dwf"),
    "step": ("model/step", "step"),
    "stp": ("model/step", "step"),
    "ifc": ("application/x-step", "step"),
    "iges": ("model/iges", "text"),
    "igs": ("model/iges", "text"),
    "stl": ("model/stl", "any"),
    # Audio
    "mp3": ("audio/mpeg", "mp3"),
    "wav": ("audio/wav", "wav"),
    "m4a": ("audio/mp4", "isobmff"),
    "aac": ("audio/aac", "aac"),
    "ogg": ("audio/ogg", "ogg"),
    "oga": ("audio/ogg", "ogg"),
    "flac": ("audio/flac", "flac"),
    "wma": ("audio/x-ms-wma", "asf"),
    # Video
    "mp4": ("video/mp4", "isobmff"),
    "m4v": ("video/mp4", "isobmff"),
    "mov": ("video/quicktime", "isobmff"),
    "3gp": ("video/3gpp", "isobmff"),
    "avi": ("video/x-msvideo", "avi"),
    "mkv": ("video/x-matroska", "ebml"),
    "webm": ("video/webm", "ebml"),
    "wmv": ("video/x-ms-wmv", "asf"),
    "mpg": ("video/mpeg", "mpeg"),
    "mpeg": ("video/mpeg", "mpeg"),
    "ogv": ("video/ogg", "ogg"),
    # Archives
    "zip": ("application/zip", "zip"),
    "7z": ("application/x-7z-compressed", "7z"),
    "rar": ("application/vnd.rar", "rar"),
    "tar": ("application/x-tar", "tar"),
    "gz": ("application/gzip", "gzip"),
    "tgz": ("application/gzip", "gzip"),
    "bz2": ("application/x-bzip2", "bzip2"),
}
# Backwards-compatible name used by templates/tests.
ALLOWED_TYPES = KNOWN_TYPES

# Active content is known but not allowed unless an administrator opts in.
DEFAULT_ALLOWED = set(KNOWN_TYPES) - {"html", "htm", "svg"}
DEFAULT_BLOCKED = {
    "exe", "dll", "com", "bat", "cmd", "msi", "msp", "scr", "pif", "cpl", "ps1", "psm1", "vbs", "vbe",
    "js", "jse", "wsf", "wsh", "hta", "jar", "sh", "lnk", "reg", "app", "docm", "xlsm", "pptm",
}

IMAGE_PREVIEW = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
TEXT_PREVIEW = {"txt", "csv", "tsv", "md", "json", "xml", "log", "yaml", "yml", "ini"}
PDF_PREVIEW = {"pdf"}
VIDEO_PREVIEW = {"mp4", "m4v", "webm", "ogv", "mov"}
AUDIO_PREVIEW = {"mp3", "wav", "ogg", "oga", "m4a", "aac", "flac"}
OFFICE_PREVIEW = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf"}
# Never served inline, whatever the preview kind.
NEVER_INLINE = {"html", "htm", "svg", "xml"}

TYPE_LABELS = {
    "pdf": "PDF", "doc": "Word", "docx": "Word", "odt": "Document", "rtf": "Rich Text",
    "xls": "Excel", "xlsx": "Excel", "ods": "Spreadsheet", "csv": "CSV", "tsv": "CSV",
    "ppt": "PowerPoint", "pptx": "PowerPoint", "odp": "Presentation", "odg": "Drawing",
    "msg": "Outlook message", "eml": "Email", "vsd": "Visio", "vsdx": "Visio",
    "txt": "Text", "md": "Markdown", "json": "JSON", "xml": "XML", "log": "Text", "yaml": "YAML",
    "yml": "YAML", "ini": "Text", "html": "HTML", "htm": "HTML", "svg": "Image",
    "png": "Image", "jpg": "Image", "jpeg": "Image", "gif": "Image", "webp": "Image",
    "bmp": "Image", "tif": "Image", "tiff": "Image",
    "dwg": "CAD", "dxf": "CAD", "dwf": "CAD", "step": "CAD", "stp": "CAD", "ifc": "CAD",
    "iges": "CAD", "igs": "CAD", "stl": "CAD",
    "mp3": "Audio", "wav": "Audio", "m4a": "Audio", "aac": "Audio", "ogg": "Audio", "oga": "Audio",
    "flac": "Audio", "wma": "Audio",
    "mp4": "Video", "m4v": "Video", "mov": "Video", "3gp": "Video", "avi": "Video", "mkv": "Video",
    "webm": "Video", "wmv": "Video", "mpg": "Video", "mpeg": "Video", "ogv": "Video",
    "zip": "Archive", "7z": "Archive", "rar": "Archive", "tar": "Archive", "gz": "Archive",
    "tgz": "Archive", "bz2": "Archive",
}

_RESERVED_WIN = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_ASF = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"


def clean_display_name(name, max_len=200):
    """Normalise an uploaded file name (the client controls it). Stored only as metadata."""
    name = unicodedata.normalize("NFC", (name or "")).strip()
    name = name.replace("\\", "/").split("/")[-1]  # drop any client path
    name = _BAD_CHARS.sub("", name).strip(" .")
    if not name:
        raise ValidationError("Name cannot be empty.")
    if name.split(".")[0].upper() in _RESERVED_WIN:
        name = "_" + name
    return name[:max_len]


def validate_name(name, kind="document"):
    """Enforce naming conventions for names a user types (folders, renames). Rejects instead of fixing."""
    from .services import settings
    name = unicodedata.normalize("NFC", (name or "")).strip()
    label = "Folder" if kind == "folder" else "Document"
    if not name:
        raise ValidationError(f"{label} name cannot be empty.")
    bad = sorted(set(_BAD_CHARS.findall(name)))
    if bad:
        shown = " ".join(c if c.isprintable() else "control characters" for c in bad)
        raise ValidationError(f'{label} names cannot contain {shown}. Characters not allowed: < > : " / \\ | ? *')
    if name.endswith("."):
        raise ValidationError(f"{label} names cannot end with a period.")
    if name.split(".")[0].upper() in _RESERVED_WIN:
        raise ValidationError(f"'{name.split('.')[0]}' is a reserved name.")
    max_len = settings.get("name_max_length")
    if len(name) > max_len:
        raise ValidationError(f"{label} names can be at most {max_len} characters.")
    pattern = settings.get("folder_name_pattern" if kind == "folder" else "document_name_pattern")
    if pattern:
        subject = name if kind == "folder" else name.rsplit(".", 1)[0]
        if not re.fullmatch(pattern, subject):
            raise ValidationError(f"{label} name '{name}' does not follow the naming convention ({pattern}).")
    return name


def extension_of(filename):
    base = secure_filename(filename or "") or ""
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[1].lower()


def _check_ooxml(fh, prefix):
    try:
        with zipfile.ZipFile(fh) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and any(n.startswith(prefix) for n in names)


def _check_odf(fh):
    try:
        with zipfile.ZipFile(fh) as zf:
            return zf.read("mimetype").startswith(b"application/vnd.oasis.opendocument")
    except (zipfile.BadZipFile, KeyError):
        return False


def _check_text(head):
    if b"\x00" in head:
        return False
    for enc in ("utf-8", "utf-16"):
        try:
            head.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    # Tolerate a multibyte character cut at the sample boundary, or legacy encodings.
    try:
        head[:-4].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return all(b >= 0x20 or b in (9, 10, 13) for b in head[:4096])


def _check_content(checker, head, stream):
    if checker == "any":
        return True
    if checker == "pdf":
        return b"%PDF-" in head[:1024]
    if checker == "ole":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if checker.startswith("ooxml:"):
        return head.startswith(b"PK\x03\x04") and _check_ooxml(stream, checker.split(":", 1)[1])
    if checker == "odf":
        return head.startswith(b"PK\x03\x04") and _check_odf(stream)
    if checker == "rtf":
        return head.startswith(b"{\\rtf")
    if checker == "text":
        return _check_text(head)
    if checker == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if checker == "jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if checker == "gif":
        return head[:6] in (b"GIF87a", b"GIF89a")
    if checker == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if checker == "bmp":
        return head[:2] == b"BM"
    if checker == "tiff":
        return head[:4] in (b"II*\x00", b"MM\x00*")
    if checker == "dwg":
        return re.match(rb"AC\d{4}", head[:6]) is not None
    if checker == "dxf":
        return head.startswith(b"AutoCAD Binary DXF") or _check_text(head)
    if checker == "dwf":
        return head.startswith(b"(DWF V") or head.startswith(b"PK\x03\x04")
    if checker == "step":
        return b"ISO-10303-21" in head[:2048]
    if checker == "mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if checker == "wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if checker == "aac":
        return head[:4] == b"ADIF" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0)
    if checker == "ogg":
        return head[:4] == b"OggS"
    if checker == "flac":
        return head[:4] == b"fLaC"
    if checker == "asf":
        return head.startswith(_ASF)
    if checker == "isobmff":
        return head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")
    if checker == "avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if checker == "ebml":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if checker == "mpeg":
        return head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")
    if checker == "zip":
        return head[:4] in (b"PK\x03\x04", b"PK\x05\x06")
    if checker == "7z":
        return head[:6] == b"7z\xbc\xaf\x27\x1c"
    if checker == "rar":
        return head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00"
    if checker == "tar":
        return head[257:262] == b"ustar"
    if checker == "gzip":
        return head[:2] == b"\x1f\x8b"
    if checker == "bzip2":
        return head[:3] == b"BZh"
    return False


def type_policy():
    """(allowed, blocked) extension sets from Admin > Settings."""
    from .services import settings
    return set(settings.get("allowed_extensions")), set(settings.get("blocked_extensions"))


def is_allowed_extension(ext):
    allowed, blocked = type_policy()
    return bool(ext) and ext in allowed and ext not in blocked


def size_limit_mb(ext):
    from .services import settings
    return settings.get("type_size_limits").get(ext) or settings.get("max_upload_mb")


def _stream_size(stream):
    pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(pos)
    return size


def validate_upload(file_storage):
    """Validate a werkzeug FileStorage. Returns (display_name, extension, mime_type)."""
    if file_storage is None or not file_storage.filename:
        raise ValidationError("Please choose a file to upload.")
    display_name = clean_display_name(file_storage.filename)
    ext = extension_of(display_name)
    allowed, blocked = type_policy()
    if not ext:
        raise ValidationError("Files without an extension are not allowed.")
    if ext in blocked:
        raise ValidationError(f"File type '.{ext}' is blocked by the administrator.")
    if ext not in allowed:
        raise ValidationError(f"File type '.{ext}' is not allowed. Ask an administrator to allow it.")
    mime, checker = KNOWN_TYPES.get(ext, ("application/octet-stream", "any"))

    stream = file_storage.stream
    stream.seek(0)
    size = _stream_size(stream)
    limit = size_limit_mb(ext)
    if size > limit * 1024 * 1024:
        raise ValidationError(f"'{display_name}' is larger than the {limit} MB limit for .{ext} files.")
    head = stream.read(8192)
    stream.seek(0)
    if not head:
        raise ValidationError("The file is empty.")
    ok = _check_content(checker, head, stream)
    stream.seek(0)
    if not ok:
        raise ValidationError(f"The file content does not match the '.{ext}' file type.")
    return display_name, ext, mime


def preview_kind(ext):
    if ext in PDF_PREVIEW:
        return "pdf"
    if ext in IMAGE_PREVIEW:
        return "image"
    if ext in TEXT_PREVIEW:
        return "text"
    if ext in VIDEO_PREVIEW:
        return "video"
    if ext in AUDIO_PREVIEW:
        return "audio"
    if ext in OFFICE_PREVIEW:
        return "office"
    return None
