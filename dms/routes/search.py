"""Advanced, security-trimmed full-text search (FR-SRC-01 .. FR-SRC-04)."""
from datetime import datetime

from flask import Blueprint, render_template, request
from sqlalchemy import func, select

from ..extensions import db
from ..models import User
from ..security.permissions import resolver
from ..services import metadata as meta_svc
from ..services import search as svc
from ..services.folders import folder_tree
from ..validation import TYPE_LABELS, ValidationError
from ._helpers import user

bp = Blueprint("search", __name__, url_prefix="/search")
PAGE_SIZE = 20


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _size(value, unit):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(n * {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}.get(unit, 1024 ** 2))


@bp.get("/")
def index():
    a = request.args
    params = svc.SearchParams(
        q=(a.get("q") or "").strip(),
        kind=a.get("kind") if a.get("kind") in ("document", "email") else "",
        types=[t for t in a.getlist("type") if t],
        folder_id=a.get("folder", type=int),
        include_subfolders=a.get("sub", "1") == "1",
        tags=[t for t in a.getlist("tag") if t],
        document_type_id=a.get("doctype", type=int),
        date_from=_date(a.get("from")), date_to=_date(a.get("to")),
        date_field="created" if a.get("datefield") == "created" else "modified",
        size_min=_size(a.get("size_min"), a.get("size_unit", "mb")) if a.get("size_min") else None,
        size_max=_size(a.get("size_max"), a.get("size_unit", "mb")) if a.get("size_max") else None,
        author_id=a.get("author", type=int),
        meta_field_id=a.get("field", type=int),
        meta_value=(a.get("value") or "").strip(),
        sort=a.get("sort") if a.get("sort") in ("relevance", "date", "name", "size") else "relevance",
    )
    page = max(1, a.get("page", 1, type=int))
    searched = any([params.q, params.kind, params.types, params.folder_id, params.tags, params.document_type_id,
                    params.date_from, params.date_to, params.size_min, params.size_max, params.author_id,
                    params.meta_value])
    hits, facets, error = [], {}, None
    if searched:
        try:
            hits, facets = svc.search(params, resolver(), user())
        except ValidationError as exc:
            error = str(exc)
    total = len(hits)
    res = resolver()
    return render_template(
        "search.html", params=params, hits=hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], total=total, page=page,
        pages=max(1, -(-total // PAGE_SIZE)), facets=facets, error=error, searched=searched,
        type_options=sorted(set(TYPE_LABELS.values()) | {"Email"}),
        folders=folder_tree(None if res.is_admin else res.visible_folder_ids()),
        tags=meta_svc.all_tags(), doc_types=meta_svc.document_types(), fields=meta_svc.fields(),
        authors=db.session.execute(select(User).order_by(func.lower(User.username))).scalars().all(),
        args=a,
    )
