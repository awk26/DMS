import json

from flask import g, has_request_context, request

from .. import sp
from ..extensions import db
from ..models import FolderActivity


def _actor():
    if not has_request_context():
        return None, "system", None
    user = getattr(g, "user", None)
    ip = request.remote_addr
    if user is None:
        return None, None, ip
    return user.id, user.label, ip


def log_activity(category, action, message, entity_id=None):
    user_id, user_name, ip = _actor()
    # Runs inside the current transaction, so a later rollback still discards the entry.
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
