import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import FileStorage
from sqlalchemy import func, select

from ..extensions import db
from ..models import (ArchivedEmail, Document, DocumentLink, DocumentVersion, EmailAttachment, Folder, Tag,
                      document_tags)
from ..security.permissions import resolver
from ..services import documents as svc
from ..services import metadata as meta_svc
from ..services.compare import compare_versions, supported as compare_supported
from ..services.emails import attachment_bytes
from ..services.folders import NotFound, folder_tree, get_folder
from ..services.preview import html_rendition, office_preview_mode, pdf_rendition
from ..storage import get_storage
from ..validation import TYPE_LABELS, ValidationError, preview_kind, type_policy
from ..services.recycle_bin import can_manage_trashed
from ._helpers import fail, handle_errors, ok, require, require_visible, safe_next, send_version, stream_stored, user

bp = Blueprint("documents", __name__, url_prefix="/documents")

PAGE_SIZE = 25
SORTS = {
    "name": func.lower(Document.name),
    "type": Document.extension,
    "size": DocumentVersion.size,
    "folder": func.lower(Folder.name),
    "uploaded": Document.created_at,
    "modified": Document.updated_at,
    "version": DocumentVersion.version_number,
}
TEXT_PREVIEW_LIMIT = 512 * 1024
RENDER_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
              "frame-ancestors 'self'; sandbox")


def _doc_url(doc_id=None, **_):
    return url_for("documents.detail", doc_id=doc_id) if doc_id else url_for("documents.index")


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def accept_attr():
    allowed, blocked = type_policy()
    return ",".join("." + e for e in sorted(allowed - blocked))


@bp.get("/")
def index():
    args = request.args
    q = (args.get("q") or "").strip()
    ftype = args.get("type") or ""
    folder_param = args.get("folder") or ""
    tag = (args.get("tag") or "").strip()
    date_from, date_to = _parse_date(args.get("from")), _parse_date(args.get("to"))
    sort = args.get("sort") if args.get("sort") in SORTS else "modified"
    direction = "asc" if args.get("dir") == "asc" else "desc"
    page = max(1, args.get("page", 1, type=int))
    res = resolver()

    stmt = (select(Document).outerjoin(DocumentVersion, Document.current_version_id == DocumentVersion.id)
            .outerjoin(Folder, Document.folder_id == Folder.id)
            .where(Document.deleted_at.is_(None), res.visible_documents_clause()))
    if q:
        stmt = stmt.where(Document.name.ilike(f"%{q}%"))
    if ftype:
        exts = [e for e, label in TYPE_LABELS.items() if label == ftype]
        stmt = stmt.where(Document.extension.in_(exts or [ftype.lower()]))
    if folder_param == "root":
        stmt = stmt.where(Document.folder_id.is_(None))
    elif folder_param.isdigit():
        stmt = stmt.where(Document.folder_id == int(folder_param))
    if tag:
        stmt = stmt.where(Document.id.in_(select(document_tags.c.document_id).join(Tag, Tag.id == document_tags.c.tag_id)
                                          .where(func.lower(Tag.name) == tag.lower())))
    if date_from:
        stmt = stmt.where(Document.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Document.created_at < date_to + timedelta(days=1))

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    col = SORTS[sort]
    stmt = stmt.order_by(col.asc() if direction == "asc" else col.desc(), Document.id.desc())
    docs = db.session.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).scalars().all()

    return render_template(
        "documents/index.html", documents=docs, total=total, page=page,
        pages=max(1, -(-total // PAGE_SIZE)), sort=sort, direction=direction,
        filters={"q": q, "type": ftype, "folder": folder_param, "tag": tag,
                 "from": args.get("from", ""), "to": args.get("to", "")},
        type_options=sorted(set(TYPE_LABELS.values())),
        folders=folder_tree(None if res.is_admin else res.visible_folder_ids()), accept=accept_attr(),
    )


def _upload_context(form, folder):
    document_type = meta_svc.document_type_from_form(form)
    values = meta_svc.values_from_form(form, folder, document_type.id if document_type else None)
    tags = meta_svc.resolve_tags(meta_svc.parse_tags(form.get("tags")))
    return document_type, values, tags


@bp.post("/upload")
@handle_errors(lambda **_: url_for("documents.index"))
def upload():
    folder = get_folder(request.form.get("folder_id"))
    require("edit", folder, "You do not have permission to upload to this folder.")
    files = request.files.getlist("file")
    if not files or not any(f.filename for f in files):
        raise ValidationError("Please choose a file to upload.")
    document_type, values, tags = _upload_context(request.form, folder)
    if len(files) > 1:
        # Multiple files: each is an independent upload; report per-file outcome.
        done, errors = [], []
        for f in files:
            try:
                doc, _ = svc.upload_document(f, folder, user=user(), document_type=document_type,
                                             metadata=values, tags=tags, note=request.form.get("note"))
                done.append(doc.name)
            except ValidationError as exc:
                db.session.rollback()
                errors.append(f"{f.filename}: {exc}")
        target = safe_next(url_for("folders.browse", folder_id=folder.id) if folder else url_for("documents.index"))
        if errors and not done:
            return fail("; ".join(errors), target)
        msg = f"Uploaded {len(done)} file(s)." + (f" Skipped: {'; '.join(errors)}" if errors else "")
        return ok(msg, target, 201)
    doc, created = svc.upload_document(files[0], folder, request.form.get("idempotency_key"),
                                       request.form.get("note"), user=user(), document_type=document_type,
                                       metadata=values, tags=tags)
    # Show the stored document where it now lives (its folder, or the library root).
    target = safe_next(url_for("folders.browse", folder_id=doc.folder_id, highlight=doc.id) if doc.folder_id
                       else url_for("folders.root", highlight=doc.id))
    return ok(f"Uploaded '{doc.name}'." if created else f"'{doc.name}' was already uploaded.",
              target, 201 if created else 200, document_id=doc.id)


@bp.get("/schema.json")
def schema_json():
    """Metadata fields that apply to an upload into a folder (and optional document type)."""
    try:
        folder = get_folder(request.args.get("folder_id"))
    except NotFound:
        folder = None
    type_id = request.args.get("type_id", type=int)
    return jsonify(fields=meta_svc.schema_json(folder, type_id),
                   types=[{"id": t.id, "name": t.name} for t in meta_svc.document_types()],
                   tags=[t.name for t in meta_svc.all_tags()])


@bp.get("/<int:doc_id>")
@handle_errors(_doc_url)
def detail(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    require_visible(doc)
    res = resolver()
    perms = res.perms(doc)
    cv = doc.current_version
    kind = preview_kind(doc.extension)
    text_preview, truncated, office_mode = None, False, None
    if kind == "text" and cv:
        with get_storage().open(cv.storage_key) as fh:
            data = fh.read(TEXT_PREVIEW_LIMIT + 1)
        truncated = len(data) > TEXT_PREVIEW_LIMIT
        text_preview = data[:TEXT_PREVIEW_LIMIT].decode("utf-8", errors="replace")
    if kind == "office" and cv:
        office_mode = office_preview_mode(cv)
    if not doc.is_deleted:
        svc.record_view(doc, user())
    versions = [v for v in doc.versions if "view" in res.version(v)]
    fields = meta_svc.applicable_fields(doc.folder, doc.document_type_id)
    values = {v.field_id: v.value for v in doc.metadata_values}
    links = [l for l in doc.links if l.folder is None or l.folder.deleted_at is None]
    from ..models import Favourite
    starred = db.session.execute(select(Favourite.id).where(
        Favourite.user_id == user().id, Favourite.target_type == "document", Favourite.target_id == doc.id)).first()
    from ..services.retention import recycle_expiry
    return render_template("documents/detail.html", doc=doc, preview_kind=kind, text_preview=text_preview,
                           truncated=truncated, office_mode=office_mode, perms=perms, versions=versions,
                           fields=fields, values=values, links=links, starred=bool(starred),
                           doc_types=meta_svc.document_types(), compare_ok=compare_supported(doc.extension),
                           accept="." + doc.extension if doc.extension else "",
                           expires=recycle_expiry(doc.deleted_at), all_tags=meta_svc.all_tags())


def _send_version(doc, version, inline=False):
    return send_version(doc, version, inline=inline)


def _version_for_read(doc, number, perm):
    version = svc.get_version(doc, number) if number else doc.current_version
    if version is None:
        abort(404)
    if perm not in resolver().version(version):
        abort(403, description="You do not have permission to access this version.")
    return version


@bp.get("/<int:doc_id>/download")
@handle_errors(_doc_url)
def download(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    require("download", doc)
    return _send_version(doc, _version_for_read(doc, None, "download"))


@bp.get("/<int:doc_id>/versions/<int:number>/download")
@handle_errors(_doc_url)
def download_version(doc_id, number):
    doc = svc.get_document(doc_id, include_deleted=True)
    require("download", doc)
    return _send_version(doc, _version_for_read(doc, number, "download"))


@bp.get("/<int:doc_id>/preview")
@handle_errors(_doc_url)
def preview(doc_id):
    """Inline content for PDF, image, video and audio previews (and Office PDF renditions)."""
    doc = svc.get_document(doc_id, include_deleted=True)
    require("view", doc)
    version = _version_for_read(doc, request.args.get("version", type=int), "view")
    kind = preview_kind(doc.extension)
    if kind in ("pdf", "image", "video", "audio"):
        return _send_version(doc, version, inline=True)
    if kind == "office":
        key = pdf_rendition(version)
        if key:
            resp = stream_stored(key, "application/pdf", doc.name.rsplit(".", 1)[0] + ".pdf", as_attachment=False)
            resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
            return resp
    abort(404, description="Preview is not available for this file type.")


@bp.get("/<int:doc_id>/render")
@handle_errors(_doc_url)
def render(doc_id):
    """Sanitised HTML rendering of an Office document (shown in a sandboxed frame)."""
    doc = svc.get_document(doc_id, include_deleted=True)
    require("view", doc)
    version = _version_for_read(doc, request.args.get("version", type=int), "view")
    html_doc = html_rendition(version)
    if html_doc is None:
        abort(404, description="Preview is not available for this file.")
    resp = Response(html_doc, mimetype="text/html")
    resp.headers["Content-Security-Policy"] = RENDER_CSP
    return resp


@bp.post("/<int:doc_id>/versions")
@handle_errors(_doc_url)
def new_version(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    version, created = svc.add_version(doc, request.files.get("file"), request.form.get("idempotency_key"),
                                       request.form.get("note"), user=user(), major=request.form.get("major") == "1")
    msg = f"Version {version.label} uploaded." if created else "This version was already uploaded."
    return ok(msg, url_for("documents.detail", doc_id=doc.id), 201 if created else 200,
              version=version.version_number, label=version.label)


@bp.post("/<int:doc_id>/versions/<int:number>/restore")
@handle_errors(_doc_url)
def restore_version(doc_id, number):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    _version_for_read(doc, number, "view")
    version, created = svc.restore_version(doc, number, request.form.get("idempotency_key"), user=user(),
                                           major=request.form.get("major") == "1")
    source = svc.get_version(doc, number)
    msg = f"Restored {source.label} as new version {version.label}." if created else "Already restored."
    return ok(msg, url_for("documents.detail", doc_id=doc.id), 201 if created else 200,
              version=version.version_number, label=version.label)


@bp.get("/<int:doc_id>/compare")
@handle_errors(_doc_url)
def compare(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    require("view", doc)
    numbers = sorted(v.version_number for v in doc.versions)
    if len(numbers) < 2:
        raise ValidationError("This document has only one version.")
    picks = sorted({int(n) for n in request.args.getlist("pick") if n.isdigit()})
    if request.args.getlist("pick") and len(picks) != 2:
        raise ValidationError("Select exactly two versions to compare.")
    if len(picks) == 2:
        return redirect(url_for("documents.compare", doc_id=doc.id, a=picks[0], b=picks[1]))
    b = request.args.get("b", type=int) or numbers[-1]
    a = request.args.get("a", type=int) or next((n for n in reversed(numbers) if n < b), numbers[0])
    va, vb = _version_for_read(doc, a, "view"), _version_for_read(doc, b, "view")
    result = compare_versions(doc, va, vb)
    return render_template("documents/compare.html", doc=doc, va=va, vb=vb, result=result,
                           versions=[v for v in doc.versions if "view" in resolver().version(v)],
                           mode=request.args.get("mode", "side"))


@bp.post("/<int:doc_id>/rename")
@handle_errors(_doc_url)
def rename(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    svc.rename_document(doc, request.form.get("name", ""), user())
    return ok(f"Renamed to '{doc.name}'.", safe_next(url_for("documents.detail", doc_id=doc.id)))


@bp.post("/<int:doc_id>/move")
@handle_errors(_doc_url)
def move(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    require("delete", doc, "Moving a document out of its folder needs delete permission there.")
    folder = get_folder(request.form.get("folder_id"))
    require("edit", folder, "You do not have permission to add documents to the destination folder.")
    svc.move_document(doc, folder, user())
    return ok(f"Moved '{doc.name}' to {folder.path if folder else 'Root'}.",
              safe_next(url_for("documents.detail", doc_id=doc.id)))


@bp.post("/<int:doc_id>/copy")
@handle_errors(_doc_url)
def copy(doc_id):
    doc = svc.get_document(doc_id)
    require("download", doc, "Copying a document needs download permission.")
    folder = get_folder(request.form.get("folder_id"))
    require("edit", folder, "You do not have permission to add documents to the destination folder.")
    new = svc.copy_document(doc, folder, user())
    return ok(f"Copied to {folder.path if folder else 'Root'} as '{new.name}'.",
              url_for("documents.detail", doc_id=new.id), 201, document_id=new.id)


@bp.post("/<int:doc_id>/links")
@handle_errors(_doc_url)
def add_link(doc_id):
    doc = svc.get_document(doc_id)
    require("view", doc)
    folder = get_folder(request.form.get("folder_id"))
    require("edit", folder, "You do not have permission to add shortcuts to that folder.")
    svc.add_link(doc, folder, user())
    return ok(f"Shortcut added to {folder.path if folder else 'Root'}.",
              safe_next(url_for("documents.detail", doc_id=doc.id)))


@bp.post("/links/<int:link_id>/remove")
@handle_errors(lambda **_: url_for("documents.index"))
def remove_link(link_id):
    link = db.session.get(DocumentLink, link_id)
    if link is None:
        raise NotFound("Shortcut not found.")
    require("edit", link.folder, "You do not have permission to change that folder.")
    doc_id = link.document_id
    svc.remove_link(link)
    return ok("Shortcut removed.", safe_next(url_for("documents.detail", doc_id=doc_id)))


@bp.post("/<int:doc_id>/delete")
@handle_errors(_doc_url)
def delete(doc_id):
    doc = svc.get_document(doc_id)
    require("delete", doc)
    svc.trash_document(doc, user())
    return ok(f"'{doc.name}' moved to the recycle bin.", safe_next(url_for("documents.index")))


@bp.post("/<int:doc_id>/metadata")
@handle_errors(_doc_url)
def update_metadata(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    document_type = meta_svc.document_type_from_form(request.form)
    values = meta_svc.values_from_form(request.form, doc.folder, document_type.id if document_type else None)
    tags = meta_svc.resolve_tags(meta_svc.parse_tags(request.form.get("tags")))
    svc.update_metadata(doc, document_type, values, tags, user())
    return ok("Properties saved.", url_for("documents.detail", doc_id=doc.id))


# ------------------------------------------------------------------- check-out / check-in

@bp.post("/<int:doc_id>/checkout")
@handle_errors(_doc_url)
def checkout(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    svc.checkout(doc, user())
    return ok(f"'{doc.name}' is checked out to you. Others cannot change it until you check it in.",
              safe_next(url_for("documents.detail", doc_id=doc.id)))


@bp.post("/<int:doc_id>/checkin")
@handle_errors(_doc_url)
def checkin(doc_id):
    doc = svc.get_document(doc_id)
    require("edit", doc)
    version = svc.checkin(doc, user(), request.files.get("file"), request.form.get("note"),
                          major=request.form.get("major") == "1", idempotency_key=request.form.get("idempotency_key"))
    msg = f"Checked in as version {version.label}." if version else "Checked in without changes."
    return ok(msg, url_for("documents.detail", doc_id=doc.id), 201 if version else 200)


@bp.post("/<int:doc_id>/release")
@handle_errors(_doc_url)
def release(doc_id):
    doc = svc.get_document(doc_id)
    force = doc.checked_out_by_id not in (None, user().id)
    if force and not (user().has("records") or "manage" in resolver().perms(doc)):
        abort(403, description="Only records managers can release another user's check-out.")
    require("view", doc)
    svc.release(doc, user(), force=force)
    return ok("Check-out released.", safe_next(url_for("documents.detail", doc_id=doc.id)))


# ------------------------------------------------------------------- recycle bin

@bp.get("/trash")
def trash():
    return redirect(url_for("main.recycle_bin"))


@bp.post("/<int:doc_id>/restore")
@handle_errors(lambda **_: url_for("main.recycle_bin"))
def restore(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    if not can_manage_trashed(doc, user(), resolver()):
        abort(403, description="You do not have permission to restore this document.")
    svc.restore_document(doc)
    return ok(f"'{doc.name}' restored.", safe_next(url_for("main.recycle_bin")))


@bp.post("/<int:doc_id>/purge")
@handle_errors(lambda **_: url_for("main.recycle_bin"))
def purge(doc_id):
    doc = svc.get_document(doc_id, include_deleted=True)
    if not can_manage_trashed(doc, user(), resolver()):
        abort(403, description="You do not have permission to permanently delete this document.")
    name = doc.name
    svc.purge_document(doc)
    return ok(f"'{name}' permanently deleted.", url_for("main.recycle_bin"))


@bp.get("/search.json")
def search_json():
    """Lightweight lookup used by the global search box (security-trimmed)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return {"documents": [], "folders": []}
    res = resolver()
    docs = db.session.execute(select(Document).where(Document.deleted_at.is_(None), Document.name.ilike(f"%{q}%"),
                                                     res.visible_documents_clause())
                              .order_by(Document.updated_at.desc()).limit(8)).scalars().all()
    folder_q = select(Folder).where(Folder.deleted_at.is_(None), Folder.name.ilike(f"%{q}%"))
    if not res.is_admin:
        folder_q = folder_q.where(Folder.id.in_(res.visible_folder_ids() or [-1]))
    folders = db.session.execute(folder_q.limit(5)).scalars().all()
    return {
        "documents": [{"name": d.name, "url": url_for("documents.detail", doc_id=d.id),
                       "folder": d.folder.path if d.folder else "Root"} for d in docs],
        "folders": [{"name": f.name, "url": url_for("folders.browse", folder_id=f.id), "path": f.path}
                    for f in folders],
    }


# ------------------------------------------------------------------- email attachment -> library

@bp.post("/import-attachment")
@handle_errors(lambda **_: url_for("emails.index"))
def import_attachment():
    """Copy one attachment out of an archived email into the document library.

    The archived email and its .eml file are only read; the new document is
    independent of it from then on.
    """
    if not user().has("emails"):
        abort(403)
    record = db.session.get(ArchivedEmail, request.form.get("email_id", type=int) or 0)
    if record is None:
        raise NotFound("Email not found.")
    if record.scan_status == "infected":
        raise ValidationError("This email was flagged as containing malware; its attachments cannot be imported.")
    part_index = request.form.get("part_index", type=int)
    att = db.session.execute(select(EmailAttachment).where(
        EmailAttachment.email_id == record.id, EmailAttachment.part_index == part_index)).scalar_one_or_none()
    if att is None:
        raise NotFound("Attachment not found.")
    data = attachment_bytes(record, part_index)
    if not data:
        raise ValidationError("The attachment is empty or could not be read from the original message.")
    folder = get_folder(request.form.get("folder_id"))
    require("edit", folder, "You do not have permission to add documents to that folder.")
    file = FileStorage(stream=io.BytesIO(data), filename=att.filename)
    subject = (record.subject or "(no subject)")[:200]
    doc, created = svc.upload_document(file, folder, request.form.get("idempotency_key"),
                                       note=f"Imported from archived email: {subject}", user=user())
    target = url_for("documents.detail", doc_id=doc.id)
    return ok(f"'{doc.name}' saved to the document library." if created else f"'{doc.name}' was already saved.",
              target, 201 if created else 200, document_id=doc.id)
