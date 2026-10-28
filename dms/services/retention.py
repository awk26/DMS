"""Recycle-bin expiry (FR-FLD-05) and version-retention rules (FR-VER-05)."""
import logging
import threading
from datetime import timedelta

from sqlalchemy import select

from ..extensions import db
from ..models import Document, DocumentVersion, Folder, utcnow
from . import settings
from .activity import log_activity

log = logging.getLogger(__name__)


def recycle_expiry(deleted_at):
    return deleted_at + timedelta(days=settings.get("recycle_bin_days")) if deleted_at else None


def purge_expired_recycle_bin():
    """Permanently delete recycle-bin items older than the configured retention period."""
    from .documents import purge_document
    from .folders import purge_folder
    cutoff = utcnow() - timedelta(days=settings.get("recycle_bin_days"))
    purged = 0
    # Top-level deleted folders first (their contents go with them).
    for folder in db.session.execute(select(Folder).where(Folder.deleted_at.is_not(None),
                                                          Folder.deleted_at < cutoff)).scalars().all():
        if db.session.get(Folder, folder.id) is None:
            continue
        if folder.parent is not None and folder.parent.deleted_at is not None:
            continue  # handled with its parent
        purge_folder(folder, reason="recycle bin retention expired")
        purged += 1
    for doc in db.session.execute(select(Document).where(Document.deleted_at.is_not(None),
                                                         Document.deleted_at < cutoff)).scalars().all():
        purge_document(doc, reason="recycle bin retention expired")
        purged += 1
    return purged


def rule_for(doc):
    """(keep_last, keep_years) for a document: nearest folder override, else the system setting."""
    keep_last, keep_years = None, None
    node = doc.folder
    while node is not None and (keep_last is None or keep_years is None):
        if keep_last is None and node.version_keep_last is not None:
            keep_last = node.version_keep_last
        if keep_years is None and node.version_keep_years is not None:
            keep_years = node.version_keep_years
        node = node.parent
    if keep_last is None:
        keep_last = settings.get("version_keep_last")
    if keep_years is None:
        keep_years = settings.get("version_keep_years")
    return keep_last or 0, keep_years or 0


def versions_to_prune(doc, now=None):
    """Old versions outside the retention rule. A version is kept when it is the current one, among
    the last N versions (keep_last), or younger than X years (keep_years). With no rule, all are kept."""
    keep_last, keep_years = rule_for(doc)
    if not keep_last and not keep_years:
        return []
    now = now or utcnow()
    ordered = sorted(doc.versions, key=lambda v: v.version_number, reverse=True)
    out = []
    for idx, v in enumerate(ordered):
        if v.id == doc.current_version_id:
            continue
        keep = False
        if keep_last and idx < keep_last:
            keep = True
        if keep_years and v.created_at > now - timedelta(days=365 * keep_years):
            keep = True
        if not keep:
            out.append(v)
    return out


def apply_version_retention():
    from .access import delete_entries_for
    from .documents import delete_stored
    removed = 0
    for doc in db.session.execute(select(Document).where(Document.deleted_at.is_(None))).scalars().all():
        prune = versions_to_prune(doc)
        if not prune:
            continue
        keys = [v.storage_key for v in prune]
        labels = ", ".join(v.label for v in sorted(prune, key=lambda x: x.version_number))
        delete_entries_for("version", [v.id for v in prune])
        for v in prune:
            db.session.delete(v)
        log_activity("document", "versions_pruned", f"Retention rule removed version(s) {labels} of '{doc.name}'",
                     doc.id)
        db.session.commit()
        delete_stored(keys)
        removed += len(prune)
    return removed


def cleanup_renditions():
    """Remove cached Office preview renditions whose source content no longer exists."""
    from ..storage import get_storage
    storage = get_storage()
    shas = set(db.session.execute(select(DocumentVersion.sha256)).scalars())
    removed = 0
    folder = storage.root / "renditions"
    if not folder.is_dir():
        return 0
    for path in folder.glob("*.pdf"):
        if path.stem not in shas:
            storage.delete(f"renditions/{path.name}")
            removed += 1
    return removed


def run_maintenance():
    result = {"recycle_bin_purged": purge_expired_recycle_bin(), "versions_pruned": apply_version_retention(),
              "renditions_removed": cleanup_renditions()}
    db.session.commit()
    return result


class MaintenanceScheduler:
    """Runs maintenance periodically in a background thread."""

    def __init__(self, app):
        self.app = app
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="dms-maintenance", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        interval = self.app.config["MAINTENANCE_INTERVAL_SECONDS"]
        self._stop.wait(60)
        while not self._stop.is_set():
            with self.app.app_context():
                try:
                    result = run_maintenance()
                    if any(result.values()):
                        log.info("Maintenance: %s", result)
                except Exception:
                    log.exception("Maintenance run failed")
                    db.session.rollback()
                finally:
                    db.session.remove()
            self._stop.wait(interval)
