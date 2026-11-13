"""Audit log (``activity_log``) and per-folder structural history (``folder_activity``).

Audit entries are append-only. They are written through the ``sp_audit_log_add`` procedure inside
the caller's transaction, database triggers reject any UPDATE or DELETE of existing rows, and the
ORM refuses to flush changes to loaded ``ActivityLog`` objects. Reading them needs the ``audit``
capability (``GET /api/v1/activity``).
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


def log_activity(category, action, message, entity_id=None, *, entity_type=None, entity_name=None,
                 changes=None, details=None):
    """Append an audit entry: who (current user and IP), what (category/action/message), which
    entity (type, id, name) and, where applicable, what changed (``changes``: field -> old/new).

    Runs inside the current transaction, so a later rollback also discards the entry.
    """
    user_id, user_name, ip = _actor()
    if entity_type is None and entity_id is not None:
        entity_type = _CATEGORY_ENTITY.get(category)
    payload = _details_json(changes, details)
    try:
        sp.call("sp_audit_log_add", category, action, message[:500], entity_type,
                entity_id, (entity_name or "")[:500] or None, payload, user_id, user_name, ip,
                flush=False, expire=False)
    except pymysql.err.MySQLError as exc:
        if not (exc.args and exc.args[0] == PROCEDURE_MISSING):
            raise
        # Database not upgraded yet (run `flask install-procedures`): keep the basic entry.
        log.warning("sp_audit_log_add is missing; audit details not stored. Run 'flask install-procedures'.")
        sp.call("sp_activity_log_add", category, action, message[:500], entity_id, user_id, user_name, ip,
                flush=False, expire=False)


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
