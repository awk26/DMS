"""Document and version operations.

Concurrent uploads are protected by:
* one database transaction per upload (document + version + search entry commit together; on any
  failure everything rolls back and the stored file is deleted);
* a MySQL named lock per (folder, name) for uploads, so a name is created only once;
* a row lock (SELECT ... FOR UPDATE) on the document while a version is added, so versions are
  numbered and checked (duplicate content, check-out) one at a time;
* optimistic locking: ``base_version`` on uploads (409 if the document moved on) and the
  ``documents.row_version`` counter for every other document update;
* unique (document_id, version_number) and idempotency keys as the last line of defence.
"""
import hashlib
import logging
import re
from contextlib import contextmanager

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Document, DocumentLink, DocumentVersion, RecentItem, utcnow
from ..security.malware import scan_stream
from ..storage import get_storage
from ..validation import (
    ConflictError, DuplicateDocumentError, ValidationError, extension_of, validate_name, validate_upload,
)
from . import search, settings
from .activity import log_activity, log_folder
from .folders import NotFound

log = logging.getLogger(__name__)
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def get_document(doc_id, include_deleted=False):
    doc = db.session.get(Document, doc_id)
    if doc is None or (doc.deleted_at is not None and not include_deleted):
        raise NotFound("Document not found.")
    return doc


def get_version(doc, version_number):
    for v in doc.versions:
        if v.version_number == version_number:
            return v
    raise NotFound("Version not found.")


def _clean_key(key):
    key = (key or "").strip()
    return key if _IDEMPOTENCY_RE.match(key) else None


def _same_name_query(name, folder_id):
    q = select(Document).where(func.lower(Document.name) == name.lower(), Document.deleted_at.is_(None))
    return q.where(Document.folder_id.is_(None) if folder_id is None else Document.folder_id == folder_id)


def _name_taken(name, folder_id, exclude_id=None):
    q = _same_name_query(name, folder_id)
    if exclude_id:
        q = q.where(Document.id != exclude_id)
    return db.session.execute(q.with_only_columns(Document.id)).first() is not None


def find_by_name(name, folder_id):
    """The live document called ``name`` (case-insensitive) in the folder, or None."""
    return db.session.execute(_same_name_query(name, folder_id).limit(1)).scalar_one_or_none()


def _unique_name(name, folder_id):
    if not _name_taken(name, folder_id):
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(1, 1000):
        candidate = f"{stem} ({i}).{ext}" if ext else f"{stem} ({i})"
        if not _name_taken(candidate, folder_id):
            return candidate
    raise ValidationError("Could not find a free name for this document.")


def _scan(file_storage, display_name):
    """Malware-scan an upload before anything is stored."""
    result = scan_stream(file_storage.stream)
    if result.status == "infected":
        log.warning("Upload '%s' rejected: malware detected (%s, %s)", display_name, result.engine, result.detail)
        log_activity("security", "malware_blocked",
                     f"Upload '{display_name}' blocked: malware detected ({result.detail or result.engine})")
        db.session.commit()
        raise ValidationError(f"'{display_name}' was rejected because malware was detected "
                              f"({result.detail or 'threat found'}).")
    if result.status != "clean":
        raise ValidationError(f"'{display_name}' could not be scanned for malware, so it was not accepted. "
                              "Try again later or contact an administrator.")
    return result


def _store(stream):
    storage = get_storage()
    key = storage.new_document_key()
    size, sha = storage.save_stream(key, stream)
    return key, size, sha


def _existing_for_key(key):
    if not key:
        return None
    return db.session.execute(select(DocumentVersion).where(DocumentVersion.idempotency_key == key)).scalar_one_or_none()


def _uid(user):
    return user.id if user is not None else None


# ask: prompt about identical content; replace: the user confirmed replacing it;
# new: the user renamed the file to upload it as a separate document, so it must not collide again.
ON_DUPLICATE = ("ask", "replace", "new")


def _stream_sha256(file_storage):
    """SHA-256 of an upload without storing it (the stream is rewound afterwards)."""
    stream = file_storage.stream
    start = stream.tell()
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    stream.seek(start)
    return digest.hexdigest()


def duplicate_info(doc, user, sha=None):
    """What the user is told about the same-named document an upload collides with.

    With ``sha`` (the upload's SHA-256) it also says whether the file is identical to a stored version."""
    from ..security.permissions import Resolver
    res = Resolver(user)
    reason = None
    if not res.can("edit", doc):
        reason = "You do not have permission to add versions to the existing document."
    elif doc.checked_out_by_id and doc.checked_out_by_id != _uid(user):
        who = doc.checked_out_by.label if doc.checked_out_by else "another user"
        reason = f"The existing document is checked out by {who}."
    current = doc.current_version
    info = {"name": doc.name, "folder_id": doc.folder_id, "can_replace": reason is None, "reason": reason}
    info["identical"] = False
    if res.can("view", doc):  # never reveal details of a document the user may not see
        info.update(document_id=doc.id, version=current.label if current else None,
                    size=current.size if current else None,
                    updated_at=doc.updated_at.isoformat() + "Z" if doc.updated_at else None)
        if sha:
            same = db.session.execute(select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.sha256 == sha
            ).order_by(DocumentVersion.version_number.desc()).limit(1)).scalar_one_or_none()
            if same is not None:
                info.update(identical=True, identical_version=same.label,
                            identical_is_current=same.id == doc.current_version_id)
    return info


def upload_document(file_storage, folder=None, idempotency_key=None, note=None, user=None,
                    document_type=None, metadata=None, tags=None, on_duplicate="ask", allow_identical=False):
    """Upload a file into ``folder``. Returns (document, created, new_version).

    * No document of that name in the folder: a new document, version 1.0
      (created=True, new_version=None).
    * A document of that name already exists:
      - with different content: the file becomes its next minor version (1.0 -> 1.1 -> 1.2 ...),
        keeping every earlier version (created=False, new_version=the version). Refused if the
        uploader may not edit the document or someone else has it checked out.
      - with content identical to one of its versions (same SHA-256):
        ``on_duplicate="ask"`` (default): nothing is stored and DuplicateDocumentError (HTTP 409)
        tells the user, who can replace it or cancel (cancel = nothing changes).
        ``on_duplicate="replace"`` + ``allow_identical`` (the user chose Replace): the identical old
        version is deleted and the upload is stored in its place as the current version, so the
        document never holds two identical versions. Other versions are kept.
        ``on_duplicate="replace"`` without ``allow_identical``: refused as identical (HTTP 400).
      - ``on_duplicate="new"`` (the user renamed the file in the duplicate prompt to keep it as a
        separate document): refused (HTTP 400) whatever the content, so the renamed upload never
        silently becomes a version of another document. The user can pick another name.
    * A retried request (same idempotency key): the earlier result (created=False, new_version=None).
    """
    on_duplicate = (on_duplicate or "ask").strip().lower()
    if on_duplicate not in ON_DUPLICATE:
        raise ValidationError("on_duplicate must be one of: " + ", ".join(ON_DUPLICATE))
    key = _clean_key(idempotency_key)
    existing = _existing_for_key(key)
    if existing:
        return existing.document, False, None

    display_name, ext, mime = validate_upload(file_storage)
    # Naming convention (length, configured pattern) applies to uploads too, and a folder never
    # holds two documents with the same name (FR-FLD-03): a changed file is a new version instead.
    validate_name(display_name, "document")
    folder_id = folder.id if folder else None
    # Concurrent uploads of the same name into the same folder run one at a time, so two people
    # can never both create "report.txt" there; the second one becomes version 1.1 instead.
    with _name_lock(folder_id, display_name):
        _fresh_snapshot()  # see documents other uploads committed while we waited for the lock
        same_name = find_by_name(display_name, folder_id)
        if same_name is not None:
            if on_duplicate == "new":
                raise ValidationError(
                    f"A document named '{same_name.name}' already exists in this folder. "
                    "Choose a different name.")
            if on_duplicate != "replace":
                # Changed content is simply the next version. Identical content is never stored
                # (or replaced) without asking the user first.
                info = duplicate_info(same_name, user, sha=_stream_sha256(file_storage))
                if info.get("identical"):
                    raise DuplicateDocumentError(
                        f"'{same_name.name}' already exists in this folder with exactly the same content "
                        f"(version {info['identical_version']}). Replace it with this upload, or cancel.",
                        duplicate=info, duplicates=[{"filename": display_name, **info}])
            return same_name, False, _version_same_name(same_name, file_storage, key, note, user,
                                                        allow_identical=allow_identical)
        return _create_document(file_storage, folder, display_name, ext, mime, key, note, user,
                                document_type, metadata, tags)


def _create_document(file_storage, folder, display_name, ext, mime, key, note, user, document_type, metadata, tags):
    """Store ``file_storage`` as a new document, version 1.0. Returns (document, True, None)."""
    scan = _scan(file_storage, display_name)
    storage_key, size, sha = _store(file_storage.stream)
    try:
        doc = Document(name=display_name, extension=ext, folder=folder,
                       created_by_id=_uid(user), document_type=document_type)
        db.session.add(doc)
        db.session.flush()
        version = DocumentVersion(
            document=doc, version_number=1, major=1, minor=0, storage_key=storage_key,
            original_filename=display_name, mime_type=mime, size=size, sha256=sha,
            note=(note or "").strip()[:500] or None, idempotency_key=key, created_by_id=_uid(user),
            scan_status="clean", scan_engine=scan.engine)
        db.session.add(version)
        db.session.flush()
        doc.current_version = version
        if metadata:
            from .metadata import apply_values
            apply_values(doc, metadata)
        if tags:
            doc.tags = list(tags)
        db.session.flush()
        from .access import grant_owner
        grant_owner(doc, user)
        search.index_document(doc)
        log_folder(folder, "document_added", document=doc.name, document_id=doc.id, via="upload")
        log_activity("document", "uploaded", f"Uploaded '{doc.name}'", doc.id, entity_name=doc.name,
                     details={"folder": folder.path if folder else "Root", "version": version.label,
                              "size": size, "sha256": sha, "note": version.note})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        get_storage().delete(storage_key)
        existing = _existing_for_key(key)
        if existing:  # concurrent retry won the race
            return existing.document, False, None
        raise
    except Exception:
        db.session.rollback()
        get_storage().delete(storage_key)
        raise
    return doc, True, None


@contextmanager
def _name_lock(folder_id, name, timeout=30):
    """Hold a MySQL named lock on (folder, lower-case name) for the duration of the block.

    It lives on its own connection so committing the ORM session (which returns the session's
    connection to the pool) never releases or leaks it. MySQL also frees it if the process dies.
    """
    digest = hashlib.sha1(f"{folder_id or 0}/{name.lower()}".encode("utf-8")).hexdigest()
    lock_name = f"dms-docname-{digest}"  # MySQL lock names are limited to 64 characters
    conn = db.engine.connect()
    try:
        got = conn.execute(text("SELECT GET_LOCK(:n, :t)"), {"n": lock_name, "t": timeout}).scalar()
        if got != 1:
            raise ConflictError(f"'{name}' is being uploaded by someone else right now. Try again in a moment.")
        try:
            yield
        finally:
            conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": lock_name})
    finally:
        conn.close()


def _fresh_snapshot():
    """End the session's transaction so the next reads see everything committed up to now.

    MySQL (REPEATABLE READ) keeps a transaction's first read snapshot; without this, a check made
    after waiting for a lock could still read data from before another request committed.
    Nothing is pending at the call sites, so this only ends the read snapshot.
    """
    db.session.commit()


def _lock_document(doc):
    """Lock ``doc``'s row (SELECT ... FOR UPDATE) until the transaction ends and reload it.

    Version uploads, check-ins and restores of one document then run one after another, and the
    checks made after this call (checked out?, base version, duplicate content, next number)
    see the latest committed state.
    """
    _fresh_snapshot()
    db.session.refresh(doc, with_for_update=True)


def _check_base_version(doc, base_version):
    """Optimistic locking for uploads: refuse if the document moved on from the version the client started from."""
    if base_version is None:
        return
    current = doc.current_version
    if current is not None and current.version_number != base_version:
        raise ConflictError(
            f"'{doc.name}' was changed while you were working on it: the current version is now {current.label}"
            f"{' (uploaded by ' + current.created_by.label + ')' if current.created_by else ''}. "
            "Review the latest version, then upload your file again.",
            current_version=current.label, current_version_number=current.version_number)


def _version_same_name(doc, file_storage, key, note, user, allow_identical=False):
    """Add an uploaded file as the next version of the same-named document ``doc``."""
    from ..security.permissions import Resolver
    if not Resolver(user).can("edit", doc):
        raise ValidationError(f"A document named '{doc.name}' already exists in this folder and you do not "
                              "have permission to add versions to it. Rename the file to upload it separately.")
    # Refuses content already stored, unless the user confirmed replacing with identical content.
    version, _ = add_version(doc, file_storage, key, note, user=user, allow_identical=allow_identical)
    return version


# --------------------------------------------------------------------------- check-out / locking

def ensure_unlocked(doc, user, action="change"):
    """Refuse changes to a document checked out by someone else (FR-DOC-04)."""
    if doc.checked_out_by_id and doc.checked_out_by_id != _uid(user):
        who = doc.checked_out_by.label if doc.checked_out_by else "another user"
        raise ValidationError(f"'{doc.name}' is checked out by {who}. It cannot be changed until it is checked in.")


def checkout(doc, user):
    if doc.checked_out_by_id == user.id:
        return doc
    ensure_unlocked(doc, user)
    doc.checked_out_by_id = user.id
    doc.checked_out_at = utcnow()
    log_activity("document", "checked_out", f"Checked out '{doc.name}'", doc.id, entity_name=doc.name)
    db.session.commit()
    return doc


def release(doc, user, force=False):
    """Undo a check-out without a new version. ``force`` lets records managers break another user's lock."""
    if not doc.checked_out_by_id:
        return doc
    if doc.checked_out_by_id != user.id and not force:
        raise ValidationError("Only the user who checked the document out (or a records manager) can release it.")
    holder = doc.checked_out_by.label if doc.checked_out_by else "unknown"
    doc.checked_out_by_id = None
    doc.checked_out_at = None
    log_activity("document", "check_out_cancelled" if not force or holder == user.label else "check_out_overridden",
                 f"Check-out of '{doc.name}' released" + (f" (was held by {holder})" if force else ""), doc.id,
                 entity_name=doc.name, details={"held_by": holder})
    db.session.commit()
    return doc


def checkin(doc, user, file_storage=None, note=None, major=False, idempotency_key=None, base_version=None):
    """Check in: optionally upload the new content, then release the lock."""
    if doc.checked_out_by_id != user.id:
        raise ValidationError("You do not have this document checked out.")
    version = None
    if file_storage is not None and file_storage.filename:
        version, _ = add_version(doc, file_storage, idempotency_key, note, user=user, major=major,
                                 base_version=base_version)
    doc.checked_out_by_id = None
    doc.checked_out_at = None
    log_activity("document", "checked_in", f"Checked in '{doc.name}'" + (f" as {version.label}" if version else ""),
                 doc.id, entity_name=doc.name, details={"version": version.label} if version else None)
    db.session.commit()
    return version


# --------------------------------------------------------------------------- versions

def _next_label(doc, major):
    latest = db.session.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                                .order_by(DocumentVersion.version_number.desc()).limit(1)).scalar_one_or_none()
    if latest is None:
        return 1, 0
    m = latest.major if latest.major is not None else latest.version_number
    n = latest.minor or 0
    return (m + 1, 0) if major else (m, n + 1)


def _check_comment(note, major):
    rule = settings.get("require_version_comment")
    if (rule == "all" or (rule == "major" and major)) and not (note or "").strip():
        raise ValidationError("A change comment is required for " + ("major versions." if rule == "major"
                                                                     else "every new version."))


def _append_version(doc, storage_key, size, sha, mime, original_filename, note, key, user, major,
                    restored_from=None, scan_engine=None, allow_identical=False, replaces=()):
    """Insert a version row as the new current version. Cleans up the stored file on failure.

    ``replaces``: versions (identical content) deleted first; their stored files are removed after
    the commit, and the new version takes their place."""
    replaced_keys, replaced_labels = [], []
    try:
        current = doc.current_version
        current_label = current.label if current else None
        if current is not None and current.sha256 == sha and not allow_identical:
            raise ValidationError("This content is identical to the current version; no new version was created.")
        if replaces:
            from .access import delete_entries_for
            replaced_keys = [v.storage_key for v in replaces]
            replaced_labels = [v.label for v in sorted(replaces, key=lambda v: v.version_number)]
            if doc.current_version_id in {v.id for v in replaces}:
                doc.current_version = None
                db.session.flush()
            delete_entries_for("version", [v.id for v in replaces])
            for v in replaces:
                db.session.delete(v)
            db.session.flush()
            db.session.expire(doc, ["versions"])
        next_no = (db.session.execute(
            select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == doc.id)
        ).scalar() or 0) + 1
        mj, mn = _next_label(doc, major)
        version = DocumentVersion(
            document=doc, version_number=next_no, major=mj, minor=mn, storage_key=storage_key,
            original_filename=original_filename, mime_type=mime, size=size, sha256=sha,
            note=(note or "").strip()[:500] or None, idempotency_key=key, restored_from=restored_from,
            created_by_id=_uid(user), scan_status="clean", scan_engine=scan_engine)
        db.session.add(version)
        db.session.flush()
        doc.current_version = version
        doc.updated_at = utcnow()
        restored_label = None
        if restored_from:
            src = next((v for v in doc.versions if v.version_number == restored_from), None)
            restored_label = src.label if src else str(restored_from)
        what = f"restored {restored_label} as {version.label}" if restored_from else f"uploaded {version.label}"
        if replaced_labels:
            what = f"replaced identical version {', '.join(replaced_labels)} with a new upload ({version.label})"
        search.index_document(doc)
        log_folder(doc.folder, "document_updated", document=doc.name, document_id=doc.id,
                   version=version.label, restored_from=restored_label)
        action = "restored" if restored_from else "replaced_identical" if replaced_labels else "versioned"
        log_activity("document", action, f"'{doc.name}': {what}", doc.id,
                     entity_name=doc.name,
                     changes={"version": {"old": current_label, "new": version.label}},
                     details={"size": size, "sha256": sha, "note": version.note, "restored_from": restored_label,
                              "replaced_versions": replaced_labels or None})
        db.session.commit()
    except Exception:
        db.session.rollback()
        get_storage().delete(storage_key)
        raise
    delete_stored(replaced_keys)  # only after the commit: the replaced versions' files
    return version


def _version_conflict(doc, key, exc):
    """A version insert failed on a unique constraint: a retried request, or (should not happen
    under the row lock) a numbering clash. Returns the earlier result or raises ConflictError."""
    existing = _existing_for_key(key)
    if existing:
        return existing
    log.warning("Version insert conflict on document %s: %s", doc.id, exc)
    raise ConflictError(f"'{doc.name}' was updated by someone else at the same time. Try again.") from None


def add_version(doc, file_storage, idempotency_key=None, note=None, user=None, major=False, base_version=None,
                allow_identical=False):
    """Upload new content for ``doc``. Returns (version, created).

    Content identical to a stored version is refused unless ``allow_identical`` (the user was told
    and explicitly chose to replace the document anyway).

    ``base_version`` (optional) is the version number the uploader started from; if the document
    has moved on since, ConflictError (HTTP 409) is raised instead of silently stacking on top.
    """
    key = _clean_key(idempotency_key)
    existing = _existing_for_key(key)
    if existing:
        if existing.document_id != doc.id:
            raise ValidationError("Duplicate request token.")
        return existing, False
    # Fail fast before scanning/storing; everything is checked again under the row lock below.
    ensure_unlocked(doc, user)
    _check_base_version(doc, base_version)
    _check_comment(note, major)
    display_name, ext, mime = validate_upload(file_storage)
    if ext != doc.extension:
        raise ValidationError(f"A new version must be a .{doc.extension} file (received .{ext}).")
    scan = _scan(file_storage, display_name)
    storage_key, size, sha = _store(file_storage.stream)
    try:
        _lock_document(doc)
        ensure_unlocked(doc, user)
        _check_base_version(doc, base_version)
        # Same content as any stored version (SHA-256) is not saved again. Bringing back old content
        # is what "Restore this version" is for (restore_version does not go through this check).
        same = db.session.execute(select(DocumentVersion).where(
            DocumentVersion.document_id == doc.id, DocumentVersion.sha256 == sha
        ).order_by(DocumentVersion.version_number.desc()).limit(1)).scalar_one_or_none()
        replaces = []
        if same is not None and allow_identical:
            # The user chose to replace the identical copy: every version with this content is
            # removed and the upload takes its place (never two identical versions).
            replaces = db.session.execute(select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.sha256 == sha)).scalars().all()
        elif same is not None:
            if same.id == doc.current_version_id:
                raise ValidationError(f"'{doc.name}' was not changed: the file is identical to the current version "
                                      f"({same.label}), so no new version was created.")
            raise ValidationError(f"'{doc.name}' was not uploaded: the file is identical to version {same.label}. "
                                  "To make that content current again, use Restore on version "
                                  f"{same.label} in the version history.")
    except Exception:
        db.session.rollback()  # releases the row lock
        get_storage().delete(storage_key)
        raise
    try:
        return _append_version(doc, storage_key, size, sha, mime, display_name, note, key, user, major,
                               scan_engine=scan.engine, allow_identical=allow_identical, replaces=replaces), True  # commits, which releases the row lock
    except IntegrityError as exc:
        return _version_conflict(doc, key, exc), False


def restore_version(doc, version_number, idempotency_key=None, user=None, major=False, note=None,
                    base_version=None):
    """Make an old version current by creating a new version with its content (history is kept)."""
    key = _clean_key(idempotency_key)
    existing = _existing_for_key(key)
    if existing:
        return existing, False
    ensure_unlocked(doc, user)
    _check_base_version(doc, base_version)
    source = get_version(doc, version_number)
    if doc.current_version_id == source.id:
        raise ValidationError(f"Version {source.label} is already the current version.")
    storage = get_storage()
    if not storage.exists(source.storage_key) or storage.sha256(source.storage_key) != source.sha256:
        log.error("Integrity check failed for document %s v%s", doc.id, version_number)
        raise ValidationError(f"The stored content of version {source.label} failed an integrity check.")
    with storage.open(source.storage_key) as fh:
        storage_key, size, sha = _store(fh)
    try:
        _lock_document(doc)
        ensure_unlocked(doc, user)
        _check_base_version(doc, base_version)
        if doc.current_version_id == source.id:
            raise ValidationError(f"Version {source.label} is already the current version.")
    except Exception:
        db.session.rollback()
        get_storage().delete(storage_key)
        raise
    try:
        version = _append_version(doc, storage_key, size, sha, source.mime_type, source.original_filename,
                                  (note or "").strip() or f"Restored from version {source.label}", key, user, major,
                                  restored_from=version_number, scan_engine=source.scan_engine)
    except IntegrityError as exc:
        return _version_conflict(doc, key, exc), False
    return version, True


# --------------------------------------------------------------------------- organising

def rename_document(doc, new_name, user=None):
    ensure_unlocked(doc, user)
    new_name = validate_name(new_name, "document")
    if extension_of(new_name) != doc.extension:
        new_name = f"{new_name}.{doc.extension}" if doc.extension else new_name
    if new_name == doc.name:
        return doc
    if _name_taken(new_name, doc.folder_id, exclude_id=doc.id):
        raise ValidationError(f"A document named '{new_name}' already exists in this folder.")
    old = doc.name
    doc.name = new_name
    search.index_document_meta(doc)
    log_folder(doc.folder, "document_renamed", document_id=doc.id, old_name=old, new_name=new_name)
    log_activity("document", "renamed", f"Renamed '{old}' to '{new_name}'", doc.id, entity_name=new_name,
                 changes={"name": {"old": old, "new": new_name}})
    db.session.commit()
    return doc


def move_document(doc, folder, user=None):
    ensure_unlocked(doc, user)
    target_id = folder.id if folder else None
    if target_id == doc.folder_id:
        return doc
    if _name_taken(doc.name, target_id, exclude_id=doc.id):
        raise ValidationError(f"A document named '{doc.name}' already exists in the destination folder.")
    old = doc.folder
    doc.folder = folder
    db.session.query(DocumentLink).filter(DocumentLink.document_id == doc.id,
                                          DocumentLink.folder_id == target_id).delete(synchronize_session=False)
    search.index_document_meta(doc)
    log_folder(old, "document_removed", document=doc.name, document_id=doc.id,
               moved_to=folder.path if folder else "Root")
    log_folder(folder, "document_added", document=doc.name, document_id=doc.id,
               moved_from=old.path if old else "Root")
    log_activity("document", "moved", f"Moved '{doc.name}' to {folder.path if folder else 'Root'}", doc.id,
                 entity_name=doc.name,
                 changes={"folder": {"old": old.path if old else "Root", "new": folder.path if folder else "Root"}})
    db.session.commit()
    return doc


def copy_document(doc, folder, user=None, commit=True):
    """Copy the current version into a new, independent document (version 1.0)."""
    source = doc.current_version
    if source is None:
        raise ValidationError("This document has no content to copy.")
    storage = get_storage()
    with storage.open(source.storage_key) as fh:
        storage_key, size, sha = _store(fh)
    try:
        folder_id = folder.id if folder else None
        copy = Document(name=_unique_name(doc.name, folder_id), extension=doc.extension, folder=folder,
                        created_by_id=_uid(user), document_type_id=doc.document_type_id)
        db.session.add(copy)
        db.session.flush()
        version = DocumentVersion(
            document=copy, version_number=1, major=1, minor=0, storage_key=storage_key,
            original_filename=source.original_filename, mime_type=source.mime_type, size=size, sha256=sha,
            note=f"Copied from '{doc.folder.path + ' / ' if doc.folder else ''}{doc.name}' version {source.label}"[:500],
            created_by_id=_uid(user), scan_status=source.scan_status, scan_engine=source.scan_engine)
        db.session.add(version)
        db.session.flush()
        copy.current_version = version
        copy.tags = list(doc.tags)
        from ..models import MetadataValue
        for mv in doc.metadata_values:
            copy.metadata_values.append(MetadataValue(field_id=mv.field_id, value=mv.value))
        db.session.flush()
        entry = db.session.execute(select(search.SearchEntry).where(
            search.SearchEntry.kind == "document", search.SearchEntry.ref_id == doc.id)).scalar_one_or_none()
        search.index_document(copy, body=entry.body if entry else None)
        log_folder(folder, "document_added", document=copy.name, document_id=copy.id, via="copy")
        log_activity("document", "copied", f"Copied '{doc.name}' to {folder.path if folder else 'Root'}", copy.id,
                     entity_name=copy.name, details={"source_document_id": doc.id, "source_name": doc.name,
                                                     "source_version": source.label,
                                                     "folder": folder.path if folder else "Root"})
        if commit:  # a document copied as part of a folder copy is owned through that folder
            from .access import grant_owner
            grant_owner(copy, user)
            db.session.commit()
        return copy
    except Exception:
        if commit:
            db.session.rollback()
        get_storage().delete(storage_key)
        raise


def add_link(doc, folder, user=None):
    """Show the document in another folder as a shortcut (FR-FLD-04)."""
    if (folder.id if folder else None) == doc.folder_id:
        raise ValidationError("The document already lives in that folder.")
    exists = db.session.execute(select(DocumentLink).where(
        DocumentLink.document_id == doc.id,
        DocumentLink.folder_id.is_(None) if folder is None else DocumentLink.folder_id == folder.id)).first()
    if exists:
        raise ValidationError("A shortcut to this document already exists in that folder.")
    link = DocumentLink(document_id=doc.id, folder_id=folder.id if folder else None, created_by_id=_uid(user))
    db.session.add(link)
    log_folder(folder, "shortcut_added", document=doc.name, document_id=doc.id)
    log_activity("document", "shortcut_added",
                 f"Shortcut to '{doc.name}' added in {folder.path if folder else 'Root'}", doc.id,
                 entity_name=doc.name, details={"folder": folder.path if folder else "Root"})
    db.session.commit()
    return link


def remove_link(link):
    doc, folder = link.document, link.folder
    db.session.delete(link)
    log_folder(folder, "shortcut_removed", document=doc.name, document_id=doc.id)
    log_activity("document", "shortcut_removed",
                 f"Shortcut to '{doc.name}' removed from {folder.path if folder else 'Root'}", doc.id,
                 entity_name=doc.name, details={"folder": folder.path if folder else "Root"})
    db.session.commit()


def update_metadata(doc, document_type, values, tags, user=None):
    ensure_unlocked(doc, user)
    from .activity import diff
    from .metadata import apply_values
    before = _properties_snapshot(doc)
    doc.document_type = document_type
    apply_values(doc, values)
    doc.tags = list(tags)
    doc.updated_at = utcnow()
    db.session.flush()
    search.index_document_meta(doc)
    log_activity("document", "metadata_updated", f"Updated properties of '{doc.name}'", doc.id,
                 entity_name=doc.name, changes=diff(before, _properties_snapshot(doc)))
    db.session.commit()


def _properties_snapshot(doc):
    """Document type, tags and custom field values, for recording what a properties edit changed."""
    fields = {}
    for mv in doc.metadata_values:
        label = mv.field.label if mv.field is not None else f"field {mv.field_id}"
        fields[label] = mv.value
    return {"document_type": doc.document_type.name if doc.document_type else None,
            "tags": sorted(t.name for t in doc.tags),
            **{f"field: {k}": v for k, v in sorted(fields.items())}}


def record_view(doc, user):
    if user is None:
        return
    item = db.session.execute(select(RecentItem).where(RecentItem.user_id == user.id,
                                                       RecentItem.document_id == doc.id)).scalar_one_or_none()
    if item is None:
        db.session.add(RecentItem(user_id=user.id, document_id=doc.id))
    else:
        item.viewed_at = utcnow()
    db.session.commit()


# --------------------------------------------------------------------------- recycle bin

def trash_document(doc, user=None, batch=None, commit=True):
    if doc.deleted_at is not None:
        return doc
    ensure_unlocked(doc, user)
    doc.deleted_at = utcnow()
    doc.deleted_batch = batch
    doc.deleted_by_id = _uid(user)
    search.remove("document", doc.id)
    log_folder(doc.folder, "document_trashed", document=doc.name, document_id=doc.id)
    log_activity("document", "trashed", f"Moved '{doc.name}' to the recycle bin", doc.id, entity_name=doc.name,
                 details={"folder": doc.folder.path if doc.folder else "Root", "batch": batch})
    if commit:
        db.session.commit()
    return doc


def restore_document(doc, commit=True, user=None):
    from .folders import restore_target
    if doc.deleted_at is None:
        return doc
    if doc.folder is not None and doc.folder.deleted_at is not None:
        doc.folder = restore_target(doc.folder, user)  # its folder is (still) in the recycle bin
    if _name_taken(doc.name, doc.folder_id, exclude_id=doc.id):
        doc.name = _unique_name(doc.name, doc.folder_id)
    doc.deleted_at = None
    doc.deleted_batch = None
    doc.deleted_by_id = None
    db.session.flush()
    search.index_document(doc)
    log_folder(doc.folder, "document_restored", document=doc.name, document_id=doc.id)
    log_activity("document", "restored_from_trash", f"Restored '{doc.name}' from the recycle bin", doc.id,
                 entity_name=doc.name, details={"folder": doc.folder.path if doc.folder else "Root"})
    if commit:
        db.session.commit()
    return doc


def purge_document(doc, commit=True, reason=None):
    """Permanently delete a trashed document and every stored version."""
    if doc.deleted_at is None:
        raise ValidationError("Only documents in the recycle bin can be permanently deleted.")
    from .access import delete_entries_for
    from ..models import Favourite
    keys = [v.storage_key for v in doc.versions]
    version_ids = [v.id for v in doc.versions]
    name, folder, doc_id = doc.name, doc.folder, doc.id
    doc.current_version = None
    db.session.flush()
    delete_entries_for("document", [doc_id])
    delete_entries_for("version", version_ids)
    db.session.query(Favourite).filter(Favourite.target_type == "document", Favourite.target_id == doc_id
                                       ).delete(synchronize_session=False)
    search.remove("document", doc_id)
    log_folder(folder, "document_purged", document=name)
    log_activity("document", "purged", f"Permanently deleted '{name}'" + (f" ({reason})" if reason else ""),
                 doc_id, entity_name=name,
                 details={"folder": folder.path if folder else "Root", "versions": len(version_ids), "reason": reason})
    db.session.delete(doc)
    if commit:
        db.session.commit()
        delete_stored(keys)
    return keys  # with commit=False the caller deletes these after its own commit


def delete_stored(keys):
    """Remove stored files (call only after the deleting transaction committed)."""
    storage = get_storage()
    for key in keys:
        storage.delete(key)
