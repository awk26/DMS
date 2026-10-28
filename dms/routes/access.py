"""Permissions pages for the repository, folders, documents and versions (FR-SEC-02, FR-FLD-02)."""
from flask import Blueprint, abort, render_template, request, url_for
from sqlalchemy import func, select

from ..extensions import db
from ..models import Role, User
from ..security.permissions import CONTENT_PERMS, PERM_HELP, PERM_LABELS, resolver
from ..services import access as svc
from ..services.documents import get_document, get_version
from ..services.folders import get_folder
from ..validation import ValidationError
from ._helpers import handle_errors, ok, require, safe_next, user

bp = Blueprint("access", __name__, url_prefix="/permissions")


def _target(kind, obj_id, number=None):
    if kind == "repository":
        return None
    if kind == "folder":
        return get_folder(obj_id)
    if kind == "document":
        return get_document(obj_id)
    if kind == "version":
        return get_version(get_document(obj_id), number)
    abort(404)


def _url(kind, obj_id=None, number=None, **_):
    if kind == "version":
        return url_for("access.manage", kind=kind, obj_id=obj_id, number=number)
    return url_for("access.manage", kind=kind, obj_id=obj_id or 0)


def _back(obj):
    from ..models import Document, DocumentVersion, Folder
    if obj is None:
        return url_for("folders.root")
    if isinstance(obj, Folder):
        return url_for("folders.browse", folder_id=obj.id)
    if isinstance(obj, Document):
        return url_for("documents.detail", doc_id=obj.id)
    return url_for("documents.detail", doc_id=obj.document_id) + "#versions"


@bp.get("/<kind>/<int:obj_id>")
@bp.get("/<kind>/<int:obj_id>/<int:number>")
@handle_errors(lambda **_: url_for("folders.root"))
def manage(kind, obj_id, number=None):
    obj = _target(kind, obj_id, number)
    require("view", obj)
    res = resolver()
    my_perms = res.perms(obj)
    can_manage = "manage" in my_perms
    can_share = "share" in my_perms or can_manage
    chain = svc.inheritance_chain(obj) if can_manage else []
    users = db.session.execute(select(User).where(User.is_active.is_(True)).order_by(func.lower(User.username))).scalars().all()
    roles = db.session.execute(select(Role).order_by(Role.name)).scalars().all()
    # Simple view: the people given access directly on this item, with their access level.
    people = []
    for e in svc.entries_for(obj):
        person = db.session.get(User, e.principal_id) if e.principal_type == "user" else None
        if person is not None:
            people.append((e, person, svc.level_of(e.permission_set)))
    levels = [(k, label) for k, (label, perms) in svc.SHARE_LEVELS.items()
              if can_manage or (k != "full" and perms <= my_perms)]
    return render_template("permissions.html", obj=obj, kind=kind, obj_id=obj_id, number=number,
                           label=svc.target_label(obj), chain=chain, can_manage=can_manage, can_share=can_share,
                           my_perms=my_perms, perms=CONTENT_PERMS, perm_labels=PERM_LABELS, perm_help=PERM_HELP,
                           users=users, roles=roles, principal_label=svc.principal_label, back=_back(obj),
                           shareable=svc.SHAREABLE, people=people, levels=levels, level_labels=svc.SHARE_LEVELS,
                           owner=getattr(obj, "created_by", None))


def _principal_from_form():
    value = request.form.get("principal", "")
    ptype, _, pid = value.partition(":")
    if ptype not in ("user", "role") or not pid.isdigit():
        raise ValidationError("Choose a user or role.")
    return ptype, int(pid)


@bp.post("/<kind>/<int:obj_id>/grant")
@bp.post("/<kind>/<int:obj_id>/<int:number>/grant")
@handle_errors(_url)
def grant(kind, obj_id, number=None):
    obj = _target(kind, obj_id, number)
    require("manage", obj)
    ptype, pid = _principal_from_form()
    perms = [p for p in CONTENT_PERMS if p in request.form.getlist("perms")]
    if obj is None and ptype == "user" and pid == user().id and "manage" not in perms and not user().is_admin:
        raise ValidationError("You cannot remove your own permission to manage the repository.")
    svc.set_entry(obj, ptype, pid, perms, user())
    return ok("Permissions updated.", _url(kind, obj_id, number))


@bp.post("/<kind>/<int:obj_id>/entries/<int:entry_id>/remove")
@bp.post("/<kind>/<int:obj_id>/<int:number>/entries/<int:entry_id>/remove")
@handle_errors(_url)
def remove(kind, obj_id, entry_id, number=None):
    obj = _target(kind, obj_id, number)
    require("manage", obj)
    svc.remove_entry(obj, entry_id)
    return ok("Access removed.", _url(kind, obj_id, number))


@bp.post("/<kind>/<int:obj_id>/inheritance")
@bp.post("/<kind>/<int:obj_id>/<int:number>/inheritance")
@handle_errors(_url)
def inheritance(kind, obj_id, number=None):
    obj = _target(kind, obj_id, number)
    require("manage", obj)
    inherit = request.form.get("inherit") == "1"
    svc.set_inheritance(obj, inherit, copy_inherited=request.form.get("copy", "1") == "1", actor=user())
    return ok("Inheritance restored: permissions now come from the parent." if inherit else
              "Inheritance stopped: only the permissions listed here apply.", _url(kind, obj_id, number))


@bp.post("/<kind>/<int:obj_id>/share")
@handle_errors(_url)
def share(kind, obj_id, number=None):
    obj = _target(kind, obj_id, number)
    my_perms = resolver().perms(obj)
    if "share" not in my_perms and "manage" not in my_perms:
        abort(403, description="You do not have permission to share this item.")
    ptype, pid = _principal_from_form()
    if request.form.get("level"):
        if ptype != "user":
            raise ValidationError("Choose a person to share with.")
        person = db.session.get(User, pid)
        svc.share_level(obj, my_perms, pid, request.form["level"], user())
        return ok(f"Shared with {person.label if person else 'the user'} ({svc.SHARE_LEVELS[request.form['level']][0]}).",
                  safe_next(_url(kind, obj_id, number)))
    svc.share(obj, my_perms, ptype, pid, request.form.getlist("perms"), user())
    return ok("Shared.", safe_next(_back(obj)))
