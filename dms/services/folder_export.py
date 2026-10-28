"""Export a folder (recursively) as a ZIP archive.

Only document content from storage is written into the archive, under names
derived from the document/folder display names. Nothing else from the
application (database, credentials, storage paths) can end up in it.
The archive is built in a temporary file that is deleted when closed.
"""
import re
import tempfile
import zipfile

from sqlalchemy import func, select

from ..extensions import db
from ..models import Document, Folder
from ..storage import CHUNK, get_storage

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_component(name, fallback="untitled"):
    """A single, safe path component for use inside the ZIP."""
    name = _UNSAFE.sub("_", name or "").strip(" .")
    if name in ("", ".", ".."):
        name = fallback
    return name[:150]


class _Dir:
    """Tracks names used within one ZIP directory to avoid duplicates."""

    def __init__(self):
        self.used = set()

    def claim(self, name):
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem:
            stem, ext = name, ""
        candidate, n = name, 1
        while candidate.lower() in self.used:
            candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
            n += 1
        self.used.add(candidate.lower())
        return candidate


def _children(parent_id, resolver=None):
    rows = db.session.execute(
        select(Folder).where(Folder.deleted_at.is_(None),
                             Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id)
        .order_by(func.lower(Folder.name))).scalars().all()
    if resolver is not None:
        rows = [f for f in rows if "view" in resolver.folder(f)]
    return rows


def _documents(folder_id, resolver=None):
    stmt = select(Document).where(Document.deleted_at.is_(None),
                                  Document.folder_id.is_(None) if folder_id is None else Document.folder_id == folder_id)
    if resolver is not None:
        stmt = stmt.where(resolver.visible_documents_clause())
    rows = db.session.execute(stmt.order_by(func.lower(Document.name))).scalars().all()
    if resolver is not None:
        rows = [d for d in rows if "download" in resolver.document(d)]
    return rows


def _write_file(zf, arcname, storage_key):
    storage = get_storage()
    with storage.open(storage_key) as src, zf.open(arcname, "w", force_zip64=True) as dst:
        for chunk in iter(lambda: src.read(CHUNK), b""):
            dst.write(chunk)


def build_folder_zip(folder, include_versions=False, resolver=None):
    """Return (temp_file, stats). ``folder=None`` exports the whole library root.

    With a ``resolver`` only folders the user can see and documents they may download are included.

    The temp file is positioned at 0; the caller must close it (deleting it).
    """
    storage = get_storage()
    stats = {"files": 0, "folders": 0, "missing": []}
    root_name = safe_component(folder.name if folder else "Documents", "folder")
    tmp = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            def walk(folder_id, prefix, seen):
                if folder_id in seen:  # defensive: never loop on bad data
                    return
                seen.add(folder_id)
                stats["folders"] += 1
                names = _Dir()
                entries = 0
                for sub in _children(folder_id, resolver):
                    sub_name = names.claim(safe_component(sub.name, "folder"))
                    walk(sub.id, f"{prefix}{sub_name}/", seen)
                    entries += 1
                history_dir = None
                for doc in _documents(folder_id, resolver):
                    version = doc.current_version
                    doc_name = names.claim(safe_component(doc.name, "document"))
                    if version is None or not storage.exists(version.storage_key):
                        stats["missing"].append(f"{prefix}{doc_name}")
                        continue
                    _write_file(zf, f"{prefix}{doc_name}", version.storage_key)
                    stats["files"] += 1
                    entries += 1
                    if include_versions and len(doc.versions) > 1:
                        if history_dir is None:
                            history_dir = names.claim("_version_history")
                        for v in sorted(doc.versions, key=lambda x: x.version_number):
                            if resolver is not None and "download" not in resolver.version(v):
                                continue
                            if not storage.exists(v.storage_key):
                                stats["missing"].append(f"{prefix}{doc_name} v{v.version_number}")
                                continue
                            arc = f"{prefix}{history_dir}/{doc_name}/v{v.label} - {doc_name}"
                            _write_file(zf, arc, v.storage_key)
                if entries == 0:
                    zf.writestr(zipfile.ZipInfo(prefix), b"")  # keep empty folders

            walk(folder.id if folder else None, f"{root_name}/", set())
            if stats["missing"]:
                zf.writestr(f"{root_name}/MISSING_FILES.txt",
                            "The stored content of these items could not be found:\n"
                            + "\n".join(stats["missing"]) + "\n")
        tmp.seek(0)
        return tmp, stats
    except BaseException:
        tmp.close()
        raise
