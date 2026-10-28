from flask import Blueprint, abort, render_template, request, url_for
from sqlalchemy import func, select

from ..extensions import db
from ..models import Document, DocumentLink, Favourite, Folder, FolderActivity
from ..security.permissions import resolver
from ..services import folders as svc
from ..services.activity import log_activity
from ..services.recycle_bin import can_manage_trashed
from ..validation import ValidationError
from ._helpers import handle_errors, ok, require, safe_next, user
from .documents import accept_attr

bp = Blueprint("folders", __name__, url_prefix="/folders")


def _browse_url(folder_id=None, **_):
    return url_for("folders.browse", folder_id=folder_id) if folder_id else url_for("folders.root")


@bp.get("/")
def root():
    return _render_browse(None)


@bp.get("/<int:folder_id>")
@handle_errors(lambda **_: url_for("folders.root"))
def browse(folder_id):
    folder = svc.get_folder(folder_id)
    require("view", folder)
    return _render_browse(folder)


def _render_browse(folder):
    res = resolver()
    parent_id = folder.id if folder else None
    visible = None if res.is_admin else res.visible_folder_ids()
    tree = svc.folder_tree(visible)
    if folder is None:
        # Top level: real root folders plus folders shared with the user whose parent is hidden.
        subfolders = [f for f, depth in tree if depth == 0]
    else:
        subfolders = [f for f in folder.live_children if visible is None or f.id in visible]
        subfolders.sort(key=lambda f: f.name.lower())
    clause = res.visible_documents_clause()
    documents = db.session.execute(
        select(Document).where(Document.deleted_at.is_(None), clause,
                               Document.folder_id.is_(None) if folder is None else Document.folder_id == parent_id)
        .order_by(func.lower(Document.name))).scalars().all()
    links = db.session.execute(
        select(DocumentLink).join(Document, DocumentLink.document_id == Document.id)
        .where(Document.deleted_at.is_(None), clause,
               DocumentLink.folder_id.is_(None) if folder is None else DocumentLink.folder_id == parent_id)
    ).scalars().all()
    starred = bool(folder and db.session.execute(select(Favourite.id).where(
        Favourite.user_id == user().id, Favourite.target_type == "folder", Favourite.target_id == folder.id)).first())
    return render_template(
        "folders/browse.html", folder=folder, subfolders=subfolders, documents=documents, links=links,
        tree=tree, counts=svc.folder_counts(clause), starred=starred, perms=res.perms(folder),
        sub_counts={f.id: len([c for c in f.live_children if visible is None or c.id in visible]) for f, _ in tree},
        accept=accept_attr(), is_system=bool(folder and folder.id in svc.system_folder_ids()),
    )


@bp.post("/")
@handle_errors(lambda **_: safe_next(url_for("folders.root")))
def create():
    parent = svc.get_folder(request.form.get("parent_id"))
    require("edit", parent, "You do not have permission to create folders here.")
    folder = svc.create_folder(request.form.get("name", ""), parent, user())
    return ok(f"Folder '{folder.name}' created.", safe_next(url_for("folders.browse", folder_id=folder.id)),
              201, folder_id=folder.id)


@bp.post("/<int:folder_id>/rename")
@handle_errors(_browse_url)
def rename(folder_id):
    folder = svc.get_folder(folder_id)
    require("edit", folder)
    svc.rename_folder(folder, request.form.get("name", ""))
    return ok(f"Folder renamed to '{folder.name}'.", safe_next(url_for("folders.browse", folder_id=folder.id)))


@bp.post("/<int:folder_id>/move")
@handle_errors(_browse_url)
def move(folder_id):
    folder = svc.get_folder(folder_id)
    require("edit", folder)
    require("delete", folder, "Moving a folder needs delete permission on it.")
    target = svc.get_folder(request.form.get("parent_id"))
    require("edit", target, "You do not have permission to add folders to the destination.")
    svc.move_folder(folder, target)
    return ok(f"Folder moved to {target.path if target else 'Root'}.",
              safe_next(url_for("folders.browse", folder_id=folder.id)))


@bp.post("/<int:folder_id>/copy")
@handle_errors(_browse_url)
def copy(folder_id):
    folder = svc.get_folder(folder_id)
    require("download", folder, "Copying a folder needs download permission.")
    target = svc.get_folder(request.form.get("parent_id"))
    require("edit", target, "You do not have permission to add folders to the destination.")
    new = svc.copy_folder(folder, target, user())
    return ok(f"Folder copied to {target.path if target else 'Root'}.", url_for("folders.browse", folder_id=new.id),
              201, folder_id=new.id)


@bp.post("/<int:folder_id>/delete")
@handle_errors(_browse_url)
def delete(folder_id):
    folder = svc.get_folder(folder_id)
    require("delete", folder)
    res = resolver()
    for node in svc.subtree(folder):
        if "delete" not in res.folder(node):
            raise ValidationError(f"You do not have permission to delete the subfolder '{node.path}'.")
    parent_id, name = folder.parent_id, folder.name
    svc.delete_folder(folder, user())
    return ok(f"Folder '{name}' and its contents moved to the recycle bin.", _browse_url(parent_id))


@bp.post("/<int:folder_id>/restore")
@handle_errors(lambda **_: url_for("main.recycle_bin"))
def restore(folder_id):
    folder = svc.get_folder(folder_id, include_deleted=True)
    if not can_manage_trashed(folder, user(), resolver()):
        abort(403, description="You do not have permission to restore this folder.")
    svc.restore_folder(folder)
    return ok(f"Folder '{folder.name}' restored.", safe_next(url_for("main.recycle_bin")))


@bp.post("/<int:folder_id>/purge")
@handle_errors(lambda **_: url_for("main.recycle_bin"))
def purge(folder_id):
    folder = svc.get_folder(folder_id, include_deleted=True)
    if not can_manage_trashed(folder, user(), resolver()):
        abort(403, description="You do not have permission to permanently delete this folder.")
    name = folder.name
    svc.purge_folder(folder)
    return ok(f"Folder '{name}' permanently deleted.", url_for("main.recycle_bin"))


@bp.post("/<int:folder_id>/retention")
@handle_errors(_browse_url)
def retention(folder_id):
    folder = svc.get_folder(folder_id)
    if not (user().has("records") or user().is_admin):
        abort(403, description="Only records managers can set retention rules.")

    def opt(name):
        raw = (request.form.get(name) or "").strip()
        if raw == "":
            return None
        if not raw.isdigit():
            raise ValidationError("Retention values must be whole numbers (or empty to inherit).")
        return int(raw)

    folder.version_keep_last, folder.version_keep_years = opt("keep_last"), opt("keep_years")
    log_activity("folder", "retention_set", f"Version retention for '{folder.path}': keep last "
                                            f"{folder.version_keep_last or 'inherit'}, years {folder.version_keep_years or 'inherit'}",
                 folder.id)
    db.session.commit()
    return ok("Retention rule saved.", url_for("folders.browse", folder_id=folder.id))


@bp.get("/<int:folder_id>/history")
@handle_errors(lambda **_: url_for("folders.root"))
def history(folder_id):
    folder = svc.get_folder(folder_id)
    require("view", folder)
    events = db.session.execute(select(FolderActivity).where(FolderActivity.folder_id == folder.id)
                                .order_by(FolderActivity.created_at.desc(), FolderActivity.id.desc())
                                ).scalars().all()
    return render_template("folders/history.html", folder=folder, events=events)


def _zip_response(folder):
    from ._helpers import folder_zip_response
    return folder_zip_response(folder)


@bp.get("/download")
def download_root():
    """The whole document library (root level) as a ZIP."""
    return _zip_response(None)


@bp.get("/<int:folder_id>/download")
@handle_errors(lambda **_: url_for("folders.root"))
def download(folder_id):
    return _zip_response(svc.get_folder(folder_id))


# ------------------------------------------------------------------- favourites

@bp.post("/favourite")
@handle_errors(lambda **_: url_for("main.favourites"))
def toggle_favourite():
    from ..services.documents import get_document
    kind = request.form.get("kind")
    target_id = request.form.get("id", type=int)
    if kind == "folder":
        require("view", svc.get_folder(target_id))
    elif kind == "document":
        require("view", get_document(target_id))
    else:
        abort(400)
    fav = db.session.execute(select(Favourite).where(Favourite.user_id == user().id, Favourite.target_type == kind,
                                                     Favourite.target_id == target_id)).scalar_one_or_none()
    if fav is None:
        db.session.add(Favourite(user_id=user().id, target_type=kind, target_id=target_id))
        msg = "Added to favourites."
    else:
        db.session.delete(fav)
        msg = "Removed from favourites."
    db.session.commit()
    return ok(msg, safe_next(url_for("main.favourites")), starred=fav is None)
