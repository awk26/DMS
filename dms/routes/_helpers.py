import re
import unicodedata
from functools import wraps
from urllib.parse import quote, urlparse

from flask import Response, abort, flash, g, jsonify, redirect, request

from .. import wants_json
from ..extensions import db
from ..security.permissions import resolver
from ..services.folders import NotFound
from ..storage import StorageError, get_storage
from ..services.activity import log_activity
from ..validation import ValidationError

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def safe_next(default):
    """A same-site redirect target from the form/query, else ``default``."""
    target = request.form.get("next") or request.args.get("next")
    if target:
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc and target.startswith("/") and not target.startswith("//") \
                and "\\" not in target:
            return target
    return default


def ok(message, redirect_to, status=200, **data):
    if wants_json():
        return jsonify(ok=True, message=message, redirect=redirect_to, **data), status
    flash(message, "success")
    return redirect(redirect_to, code=303)


def fail(message, redirect_to, status=400):
    if wants_json():
        return jsonify(ok=False, error=message), status
    flash(message, "error")
    return redirect(redirect_to, code=303)


def handle_errors(fallback):
    """Turn service-layer ValidationError/NotFound into user-facing responses.

    ``fallback`` is a callable (taking the view kwargs) returning the redirect URL.
    """
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except ValidationError as exc:
                db.session.rollback()
                return fail(str(exc), safe_next(fallback(**kwargs)), 400)
            except NotFound as exc:
                db.session.rollback()
                if wants_json():
                    return jsonify(ok=False, error=str(exc)), 404
                abort(404, description=str(exc))
        return wrapper
    return deco


def user():
    return getattr(g, "user", None)


def require(perm, obj=None, message=None):
    """Abort with 403 unless the signed-in user has ``perm`` on ``obj`` (None = repository)."""
    if not resolver().can(perm, obj):
        if obj is not None and not resolver().can("view", obj):
            abort(404, description="Not found.")  # do not reveal items the user cannot see
        abort(403, description=message or "You do not have permission to do this.")


def require_visible(obj):
    if not resolver().can("view", obj):
        abort(404, description="Not found.")


def _content_disposition(disposition, name):
    try:
        name.encode("ascii")
        simple = name.replace("\\", "_").replace('"', "_")
        return f'{disposition}; filename="{simple}"'
    except UnicodeEncodeError:
        simple = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        simple = simple.replace("\\", "_").replace('"', "_") or "download"
        return f"{disposition}; filename=\"{simple}\"; filename*=UTF-8''{quote(name, safe='')}"


def stream_stored(storage_key, mimetype, download_name, as_attachment=True, etag=None):
    """Stream stored (decrypted) content, with single-range support for media seeking."""
    storage = get_storage()
    try:
        reader = storage.open_reader(storage_key)
    except FileNotFoundError:
        abort(404, description="The stored file is missing.")
    except StorageError:
        abort(500)
    size = reader.size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("Range")
    if rng and size > 0:
        m = _RANGE_RE.match(rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                reader.close()
                resp = Response(status=416)
                resp.headers["Content-Range"] = f"bytes */{size}"
                return resp
            status = 206
    length = max(0, end - start + 1) if size else 0

    def generate():
        try:
            reader.seek(start)
            remaining = length
            while remaining > 0:
                chunk = reader.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            reader.close()

    resp = Response(generate(), status=status, mimetype=mimetype, direct_passthrough=True)
    resp.headers["Content-Length"] = str(length)
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Disposition"] = _content_disposition("attachment" if as_attachment else "inline",
                                                              download_name)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if etag:
        resp.set_etag(etag)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def send_version(doc, version, inline=False):
    from ..validation import NEVER_INLINE
    storage = get_storage()
    if not storage.exists(version.storage_key):
        abort(404, description="The stored file for this version is missing.")
    if inline and doc.extension in NEVER_INLINE:
        inline = False
    name = doc.name
    if version.id != doc.current_version_id and not inline:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem} (v{version.label}).{ext}" if dot else f"{name} (v{version.label})"
    response = stream_stored(version.storage_key, version.mime_type, name,
                             as_attachment=not inline, etag=version.sha256)
    if inline and doc.extension == "pdf":
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


def folder_zip_response(folder):
    from flask import send_file
    from ..services.folder_export import build_folder_zip, safe_component

    require("download", folder, "You do not have permission to download this folder.")
    include_versions = request.args.get("versions") == "1"
    temp_file, stats = build_folder_zip(folder, include_versions=include_versions, resolver=resolver())
    name = safe_component(folder.name if folder else "Documents", "folder")
    log_activity(
        "folder", "downloaded",
        f"Downloaded {('folder ' + chr(39) + folder.path + chr(39)) if folder else 'the document library'} as ZIP "
        f"({stats['files']} file(s){', with version history' if include_versions else ''})",
        folder.id if folder else None,
    )
    db.session.commit()
    suffix = " (with versions)" if include_versions else ""
    return send_file(temp_file, mimetype="application/zip", as_attachment=True,
                     download_name=f"{name}{suffix}.zip", max_age=0)
