from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select

from ..extensions import db
from ..models import (AccessEntry, ActivityLog, ArchivedEmail, Document, DocumentVersion, Favourite, Folder,
                      RecentItem, User)
from ..security.auth import require_capability
from ..security.permissions import resolver
from ..services.retention import recycle_expiry
from ..services import settings
from ..services.recycle_bin import recycle_bin_items
from ._helpers import ok, user

bp = Blueprint("main", __name__)


@bp.get("/")
def dashboard():
    res = resolver()
    active = Document.deleted_at.is_(None)
    visible = res.visible_documents_clause()
    can_emails = user().has("emails")
    stats = {
        "documents": db.session.execute(select(func.count(Document.id)).where(active, visible)).scalar(),
        "folders": len(res.visible_folder_ids()),
        "emails": db.session.execute(select(func.count(ArchivedEmail.id))).scalar() if can_emails else None,
        "versions": db.session.execute(select(func.count(DocumentVersion.id))
                    .join(Document, DocumentVersion.document_id == Document.id).where(active, visible)).scalar(),
        "storage": db.session.execute(select(func.coalesce(func.sum(DocumentVersion.size), 0))
                                      .join(Document, DocumentVersion.document_id == Document.id).where(visible)).scalar()
                   + (db.session.execute(select(func.coalesce(func.sum(ArchivedEmail.size), 0))).scalar() if can_emails else 0),
        "trash": db.session.execute(select(func.count(Document.id)).where(Document.deleted_at.is_not(None), visible)).scalar(),
    }
    recent_docs = db.session.execute(select(Document).where(active, visible).order_by(Document.created_at.desc())
                                     .limit(6)).scalars().all()
    updated_docs = db.session.execute(
        select(Document).join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(active, visible, DocumentVersion.version_number > 1)
        .order_by(Document.updated_at.desc()).limit(6)).scalars().all()
    recent_emails = db.session.execute(select(ArchivedEmail).order_by(ArchivedEmail.imported_at.desc()).limit(6)
                                       ).scalars().all() if can_emails else []
    activity = []
    if user().has("audit"):
        activity = db.session.execute(select(ActivityLog).order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
                                      .limit(8)).scalars().all()
    checked_out = db.session.execute(select(Document).where(active, Document.checked_out_by_id == user().id)
                                     .order_by(Document.checked_out_at.desc())).scalars().all()
    return render_template("dashboard.html", stats=stats, recent_docs=recent_docs, updated_docs=updated_docs,
                           recent_emails=recent_emails, activity=activity, checked_out=checked_out)


@bp.get("/recent")
def recent():
    res = resolver()
    viewed = db.session.execute(
        select(RecentItem).join(Document, RecentItem.document_id == Document.id)
        .where(RecentItem.user_id == user().id, Document.deleted_at.is_(None), res.visible_documents_clause())
        .order_by(RecentItem.viewed_at.desc()).limit(30)).scalars().all()
    docs = db.session.execute(select(Document).where(Document.deleted_at.is_(None), res.visible_documents_clause())
                              .order_by(Document.updated_at.desc()).limit(50)).scalars().all()
    return render_template("recent.html", documents=docs, viewed=viewed)


@bp.get("/my-files")
def my_files():
    from ..services.folders import ensure_home
    return redirect(url_for("folders.browse", folder_id=ensure_home(user()).id))


@bp.get("/shared")
def shared():
    """Folders and documents other people shared directly with the current user."""
    res = resolver()
    entries = db.session.execute(select(AccessEntry).where(
        AccessEntry.principal_type == "user", AccessEntry.principal_id == user().id,
        AccessEntry.target_type.in_(("folder", "document"))).order_by(AccessEntry.created_at.desc())).scalars().all()
    folders, documents, by = [], [], {}
    for e in entries:
        if e.created_by_id == user().id:
            continue  # the user's own ownership grant, not something shared with them
        model = Folder if e.target_type == "folder" else Document
        item = db.session.get(model, e.target_id)
        if item is None or item.deleted_at is not None or item.created_by_id == user().id                 or item.id == user().home_folder_id or "view" not in res.perms(item):
            continue
        (folders if model is Folder else documents).append(item)
        sharer = db.session.get(User, e.created_by_id) if e.created_by_id else None
        by[(e.target_type, item.id)] = sharer.label if sharer else None
    return render_template("shared.html", folders=folders, documents=documents, shared_by=by)


@bp.get("/favourites")
def favourites():
    res = resolver()
    favs = db.session.execute(select(Favourite).where(Favourite.user_id == user().id)
                              .order_by(Favourite.created_at.desc())).scalars().all()
    folders, documents = [], []
    for fav in favs:
        if fav.target_type == "folder":
            f = db.session.get(Folder, fav.target_id)
            if f is not None and f.deleted_at is None and "view" in res.folder(f):
                folders.append(f)
        else:
            d = db.session.get(Document, fav.target_id)
            if d is not None and d.deleted_at is None and "view" in res.document(d):
                documents.append(d)
    return render_template("favourites.html", folders=folders, documents=documents)


@bp.get("/recycle-bin")
def recycle_bin():
    """Deleted folders (with their contents) and documents, restorable until they expire."""
    folders, docs = recycle_bin_items(user(), resolver())
    contents = {}
    for f in folders:
        contents[f.id] = db.session.execute(select(func.count(Document.id)).where(
            Document.deleted_batch == f.deleted_batch)).scalar()
    return render_template("recycle_bin.html", folders=folders, documents=docs, contents=contents,
                           expiry=recycle_expiry, days=settings.get("recycle_bin_days"))


@bp.post("/recycle-bin/empty")
def empty_recycle_bin():
    """Permanently delete everything in the recycle bin that the user may purge."""
    from ..services.documents import purge_document
    from ..services.folders import purge_folder
    folders, docs = recycle_bin_items(user(), resolver())
    for f in folders:
        if db.session.get(Folder, f.id) is not None:
            purge_folder(f, reason="recycle bin emptied")
    for d in docs:
        if db.session.get(Document, d.id) is not None:
            purge_document(d, reason="recycle bin emptied")
    n = len(folders) + len(docs)
    return ok(f"Recycle bin emptied: {n} item{'' if n == 1 else 's'} permanently deleted." if n
              else "The recycle bin is already empty.", url_for("main.recycle_bin"))


@bp.get("/versions")
def versions():
    res = resolver()
    page = max(1, request.args.get("page", 1, type=int))
    per = 40
    stmt = select(DocumentVersion).join(Document, DocumentVersion.document_id == Document.id) \
        .where(Document.deleted_at.is_(None), res.visible_documents_clause())
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
                              .limit(per).offset((page - 1) * per)).scalars().all()
    return render_template("versions.html", versions=rows, page=page, pages=max(1, -(-total // per)), total=total)


@bp.get("/activity")
@require_capability("audit")
def activity():
    page = max(1, request.args.get("page", 1, type=int))
    category = request.args.get("category") or ""
    q = (request.args.get("q") or "").strip()
    per = 50
    stmt = select(ActivityLog)
    if category:
        stmt = stmt.where(ActivityLog.category == category)
    if q:
        stmt = stmt.where(or_(ActivityLog.message.ilike(f"%{q}%"), ActivityLog.user_name.ilike(f"%{q}%")))
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
                              .limit(per).offset((page - 1) * per)).scalars().all()
    categories = [c for (c,) in db.session.execute(select(ActivityLog.category).distinct().order_by(ActivityLog.category))]
    return render_template("activity.html", events=rows, page=page, pages=max(1, -(-total // per)),
                           category=category, categories=categories, q=q)
