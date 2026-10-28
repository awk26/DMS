import json

from flask import g, has_request_context, request

from ..extensions import db
from ..models import ActivityLog, FolderActivity


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
    db.session.add(ActivityLog(category=category, action=action, message=message[:500], entity_id=entity_id,
                               user_id=user_id, user_name=user_name, ip=ip))


def log_folder(target, action, /, **details):
    """Record a structural change for folder ``target`` (None = the root level, not recorded)."""
    if target is None:
        return
    db.session.add(FolderActivity(
        folder_id=target.id, folder_name=target.name, action=action,
        details_json=json.dumps(details, default=str), user_name=_actor()[1],
    ))
