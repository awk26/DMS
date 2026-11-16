"""Audit log (``activity_log``) and per-folder structural history (``folder_activity``).

Audit entries are append-only. They are written through the ``sp_audit_log_add`` procedure inside
the caller's transaction, database triggers reject any UPDATE or DELETE of existing rows, and the
ORM refuses to flush changes to loaded ``ActivityLog`` objects. Every signed-in user reads only their
own entries (``GET /api/v1/activity``).

Each entry has a ``status``: ``success``, ``denied`` (the user lacked the permission; written by
``log_denied`` in its own transaction so it is kept although the request fails) or ``failed``.
"""
import json
import logging

import pymysql
from flask import g, has_request_context, request
from sqlalchemy import event

from .. import sp
from ..extensions import db
from ..models import ActivityLog, FolderActivity

log = logging.getLogger(__name__)

PROCEDURE_MISSING = 1305  # ER_SP_DOES_NOT_EXIST
STATUSES = ("success", "denied", "failed")
DETAILS_MAX = 60000       # TEXT column holds 65535 bytes
# Categories whose entity_id always names an entity of the same kind.
_CATEGORY_ENTITY = {"document": "document", "folder": "folder", "email": "email"}


def _actor():
    if not has_request_context():
        return None, "system", None
    user = getattr(g, "user", None)
    ip = request.remote_addr
    if user is None:
        return None, None, ip
    return user.id, user.label, ip


def _details_json(changes, details):
    data = dict(details or {})
    if changes:
        data["changes"] = changes
    if not data:
        return None
    text = json.dumps(data, default=str, ensure_ascii=False)
    if len(text.encode("utf-8")) > DETAILS_MAX:
        text = json.dumps({"truncated": True, "summary": text[:2000]}, ensure_ascii=False)
    return text


def diff(old, new):
    """{field: {"old": a, "new": b}} for every key whose value differs between two dicts."""
    out = {}
    for key in list(old) + [k for k in new if k not in old]:
        a, b = old.get(key), new.get(key)
        if a != b:
            out[key] = {"old": a, "new": b}
    return out


def _missing(exc):
    return isinstance(exc, pymysql.err.MySQLError) and exc.args and exc.args[0] == PROCEDURE_MISSING


def _write(callproc, category, action, message, entity_id, entity_type, entity_name, payload, status):
    """Write one entry through the newest audit procedure the database has."""
    user_id, user_name, ip = _actor()
    if entity_type is None and entity_id is not None:
        entity_type = _CATEGORY_ENTITY.get(category)
    name = (entity_name or "")[:500] or None
    try:
        callproc("sp_audit_log_add_v2", (category, action, message[:500], entity_type, entity_id, name, payload,
                                         user_id, user_name, ip, status))
        return
    except pymysql.err.MySQLError as exc:
        if not _missing(exc):
            raise
    log.warning("sp_audit_log_add_v2 is missing; audit status not stored. Run 'flask install-procedures'.")
    try:
        callproc("sp_audit_log_add", (category, action, message[:500], entity_type, entity_id, name, payload,
                                      user_id, user_name, ip))
    except pymysql.err.MySQLError as exc:
        if not _missing(exc):
            raise
        # Database not upgraded yet (run `flask install-procedures`): keep the basic entry.
        callproc("sp_activity_log_add", (category, action, message[:500], entity_id, user_id, user_name, ip))


def log_activity(category, action, message, entity_id=None, *, entity_type=None, entity_name=None,
                 changes=None, details=None, status="success"):
    """Append an audit entry: who (current user and IP), what (category/action/message), which
    entity (type, id, name), the outcome (``status``) and, where applicable, what changed
    (``changes``: field -> old/new).

    Runs inside the current transaction, so a later rollback also discards the entry.
    """
    if status not in STATUSES:
        raise ValueError(status)
    _write(lambda proc, args: sp.call(proc, *args, flush=False, expire=False), category, action, message,
           entity_id, entity_type, entity_name, _details_json(changes, details), status)


def log_denied(permission, obj, can_view):
    """Record a refused attempt (status ``denied``) in its own transaction, so it is kept even though
    the request fails and rolls back. Visible, like every entry, only to the user who tried.

    ``can_view`` False: the user may not even see the item, so its name is not stored (the entry
    would otherwise tell them the name of something hidden from them)."""
    from .access import target_key, target_label
    if not has_request_context() or getattr(g, "user", None) is None:
        return
    ttype, tid = target_key(obj)
    if can_view or obj is None:
        name, message = target_label(obj), f"Access denied ({permission}): {target_label(obj)}"
    else:
        name, message = None, f"Access denied ({permission}): {ttype} #{tid}"

    def callproc(proc, args):
        raw = conn.connection.cursor()
        try:
            raw.callproc(proc, args)
            while raw.nextset():
                pass
        finally:
            raw.close()

    try:
        with db.engine.begin() as conn:
            _write(callproc, "security", "access_denied", message, tid, ttype, name,
                   _details_json(None, {"permission": permission}), "denied")
    except Exception:  # never turn a 403/404 into a 500 because the audit write failed
        log.exception("Could not record a denied access attempt")


def log_folder(target, action, /, **details):
    """Record a structural change for folder ``target`` (None = the root level, not recorded)."""
    if target is None:
        return
    db.session.add(FolderActivity(
        folder_id=target.id, folder_name=target.name, action=action,
        details_json=json.dumps(details, default=str), user_name=_actor()[1],
    ))


# --------------------------------------------------------------------------- tamper protection

class AuditLogImmutable(RuntimeError):
    pass


@event.listens_for(ActivityLog, "before_update")
def _refuse_update(mapper, connection, target):
    raise AuditLogImmutable("Audit log entries cannot be modified.")


@event.listens_for(ActivityLog, "before_delete")
def _refuse_delete(mapper, connection, target):
    raise AuditLogImmutable("Audit log entries cannot be deleted.")
