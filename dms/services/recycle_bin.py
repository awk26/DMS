from sqlalchemy import select

from ..extensions import db
from ..models import Document, Folder


def can_manage_trashed(item, actor, permission_resolver):
    # No capability bypass (records/admin): another user's private items never show up in someone
    # else's recycle bin. Records managers still see everything in the Shared library through their
    # repository-level delete permission.
    return item.deleted_by_id == actor.id or permission_resolver.can("delete", item)


def recycle_bin_items(actor, permission_resolver):
    folders = db.session.execute(
        select(Folder).where(Folder.deleted_at.is_not(None)).order_by(Folder.deleted_at.desc())
    ).scalars().all()
    folders = [
        folder for folder in folders
        if not (folder.parent is not None and folder.parent.deleted_batch == folder.deleted_batch
                and folder.parent.deleted_at is not None)
    ]
    folder_batches = {folder.deleted_batch for folder in folders}
    documents = db.session.execute(
        select(Document).where(Document.deleted_at.is_not(None)).order_by(Document.deleted_at.desc())
    ).scalars().all()
    documents = [
        document for document in documents
        if document.deleted_batch is None or document.deleted_batch not in folder_batches
    ]
    return (
        [folder for folder in folders if can_manage_trashed(folder, actor, permission_resolver)],
        [document for document in documents if can_manage_trashed(document, actor, permission_resolver)],
    )
