"""Access checks for the validity module.

Built on the existing resolver (security/permissions.py), with ``honour_admin=False``: an
administrator is treated exactly like any other user here, so the admin role gives no extra validity
privileges and no access to other people's private documents through this module. Everyone,
administrators included, gets what ownership and shares give them:

* view  (``view`` on the document/folder): see validity details, follow alerts
* edit  (``edit``; owners have it): confirm/correct dates, pause/disable monitoring, record exceptions,
  re-run the analysis, change a folder's monitoring setting
* download (``download``): open exception evidence
"""
from flask import abort, g, has_request_context, request

from ..security.permissions import Resolver


def resolver_for(user):
    """A plain (non-admin) resolver for ``user``, cached for the current request only (``g`` can outlive
    a request when an application context is shared, so the cache lives in the request environ)."""
    if has_request_context() and user is not None:
        cache = request.environ.setdefault("dms.validity_resolvers", {})
        if user.id not in cache:
            cache[user.id] = Resolver(user, honour_admin=False)
        return cache[user.id]
    return Resolver(user, honour_admin=False)


def perms(obj, user=None):
    user = user or g.user
    return resolver_for(user).perms(obj)


def can(perm, obj, user=None):
    return perm in perms(obj, user)


def require(perm, obj, message=None):
    """404 when the user may not even see ``obj``, 403 when they may see but not ``perm``. Audited."""
    from ..services.activity import log_denied
    if can(perm, obj):
        return
    visible = can("view", obj)
    log_denied(f"validity.{perm}", obj, visible)
    if not visible:
        abort(404, description="Not found.")
    abort(403, description=message or "You do not have permission to change validity monitoring here.")
