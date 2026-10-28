"""Administrator-managed settings stored in the database (with typed defaults)."""
import ipaddress
import json
import re

from flask import current_app, g, has_app_context

from ..extensions import db
from ..models import SystemSetting
from ..validation import DEFAULT_ALLOWED, DEFAULT_BLOCKED, ValidationError


def _defaults():
    return {
        # Repository & recycle bin
        "recycle_bin_days": 30,               # at least 30: deleted items stay restorable for a month
        "home_container_folder_id": None,     # hidden folder that holds every user's "My Files"
        "folder_name_pattern": "",            # optional regex every folder name must match
        "document_name_pattern": "",          # optional regex every document name must match
        "name_max_length": 200,
        # File types and size limits (FR-DOC-02)
        "allowed_extensions": sorted(DEFAULT_ALLOWED),
        "blocked_extensions": sorted(DEFAULT_BLOCKED),
        "max_upload_mb": current_app.config["MAX_UPLOAD_MB"] if has_app_context() else 50,
        "type_size_limits": {},               # {"mp4": 500}
        # Versions (FR-VER-02 / FR-VER-05)
        "require_version_comment": "none",    # none | major | all
        "version_keep_last": 0,               # 0 = keep all
        "version_keep_years": 0,              # 0 = keep forever
        # Metadata (FR-MET-02)
        "tags_controlled": False,             # only administrator-defined tags may be used
        # Security (FR-SEC-03 / FR-SEC-05)
        "mfa_required": False,                # local and LDAP accounts must enrol in MFA
        "session_idle_minutes": 30,
        "session_max_hours": 12,
        "ip_allowlist": [],                   # CIDRs; empty = any address
        "device_policy": "any",               # any | approval (new devices need administrator approval)
        "blocked_platforms": [],              # e.g. ["android", "ios"]
        "self_signup": True,                  # anyone who can reach the sign-in page may create an account
        "signup_default_role": "Contributor",
        "sso_auto_provision": True,
        "sso_default_role": "Reader",
        "group_role_map": {},                 # {"DMS-Admins": "Admin"}
        "lockout_threshold": 5,
        "lockout_minutes": 15,
    }


PLATFORMS = ("windows", "macos", "linux", "android", "ios", "other")


def _cache():
    if not has_app_context():
        return {}
    if "dms_settings" not in g:
        g.dms_settings = {}
    return g.dms_settings


def get(key):
    defaults = _defaults()
    if key not in defaults:
        raise KeyError(key)
    cache = _cache()
    if key in cache:
        return cache[key]
    row = db.session.get(SystemSetting, key)
    value = defaults[key]
    if row is not None:
        try:
            value = json.loads(row.value_json)
        except ValueError:
            pass
    cache[key] = value
    return value


def all_settings():
    return {k: get(k) for k in _defaults()}


def set_value(key, value):
    if key not in _defaults():
        raise KeyError(key)
    row = db.session.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value_json=json.dumps(value))
        db.session.add(row)
    else:
        row.value_json = json.dumps(value)
    _cache()[key] = value


def clear_cache():
    if has_app_context():
        g.pop("dms_settings", None)


# --------------------------------------------------------------------------- parsing from the admin form

def _ext_list(text):
    items = re.split(r"[\s,;]+", (text or "").lower())
    out = sorted({i.lstrip(".") for i in items if i.strip(".")})
    for e in out:
        if not re.fullmatch(r"[a-z0-9]{1,15}", e):
            raise ValidationError(f"'{e}' is not a valid file extension.")
    return out


def _int_range(form, name, lo, hi):
    try:
        value = int(form.get(name, ""))
    except ValueError:
        raise ValidationError(f"'{name.replace('_', ' ')}' must be a whole number.")
    if not lo <= value <= hi:
        raise ValidationError(f"'{name.replace('_', ' ')}' must be between {lo} and {hi}.")
    return value


def _regex(text, label):
    text = (text or "").strip()
    if text:
        try:
            re.compile(text)
        except re.error as exc:
            raise ValidationError(f"{label} is not a valid regular expression: {exc}")
    return text


def parse_cidrs(text):
    out = []
    for item in re.split(r"[\s,;]+", text or ""):
        if not item:
            continue
        try:
            out.append(str(ipaddress.ip_network(item, strict=False)))
        except ValueError:
            raise ValidationError(f"'{item}' is not a valid IP address or CIDR range.")
    return out


def update_from_form(form):
    """Validate and store the settings posted from Admin > Settings."""
    allowed = _ext_list(form.get("allowed_extensions"))
    blocked = _ext_list(form.get("blocked_extensions"))
    limits = {}
    for line in (form.get("type_size_limits") or "").splitlines():
        if not line.strip():
            continue
        ext, _, mb = line.partition("=")
        ext = ext.strip().lower().lstrip(".")
        try:
            limits[ext] = max(1, int(mb.strip()))
        except ValueError:
            raise ValidationError(f"Size limit line '{line.strip()}' must look like 'mp4 = 500'.")
    group_map = {}
    for line in (form.get("group_role_map") or "").splitlines():
        if not line.strip():
            continue
        group, _, role = line.partition("=")
        if not group.strip() or not role.strip():
            raise ValidationError(f"Group mapping line '{line.strip()}' must look like 'DMS-Admins = Admin'.")
        group_map[group.strip()] = role.strip()
    comment = form.get("require_version_comment", "none")
    if comment not in ("none", "major", "all"):
        raise ValidationError("Invalid version comment rule.")
    device_policy = form.get("device_policy", "any")
    if device_policy not in ("any", "approval"):
        raise ValidationError("Invalid device policy.")
    platforms = [p for p in form.getlist("blocked_platforms") if p in PLATFORMS]

    values = {
        "recycle_bin_days": _int_range(form, "recycle_bin_days", 30, 3650),
        "folder_name_pattern": _regex(form.get("folder_name_pattern"), "Folder name pattern"),
        "document_name_pattern": _regex(form.get("document_name_pattern"), "Document name pattern"),
        "name_max_length": _int_range(form, "name_max_length", 10, 255),
        "allowed_extensions": allowed,
        "blocked_extensions": blocked,
        "max_upload_mb": _int_range(form, "max_upload_mb", 1, current_app.config["MAX_REQUEST_MB"]),
        "type_size_limits": limits,
        "require_version_comment": comment,
        "version_keep_last": _int_range(form, "version_keep_last", 0, 100000),
        "version_keep_years": _int_range(form, "version_keep_years", 0, 1000),
        "tags_controlled": form.get("tags_controlled") == "1",
        "mfa_required": form.get("mfa_required") == "1",
        "session_idle_minutes": _int_range(form, "session_idle_minutes", 1, 1440),
        "session_max_hours": _int_range(form, "session_max_hours", 1, 720),
        "ip_allowlist": parse_cidrs(form.get("ip_allowlist")),
        "device_policy": device_policy,
        "blocked_platforms": platforms,
        "self_signup": form.get("self_signup") == "1",
        "signup_default_role": (form.get("signup_default_role") or "Contributor").strip(),
        "sso_auto_provision": form.get("sso_auto_provision") == "1",
        "sso_default_role": (form.get("sso_default_role") or "Reader").strip(),
        "group_role_map": group_map,
        "lockout_threshold": _int_range(form, "lockout_threshold", 1, 100),
        "lockout_minutes": _int_range(form, "lockout_minutes", 1, 1440),
    }
    for limit_ext, mb in limits.items():
        if mb > current_app.config["MAX_REQUEST_MB"]:
            raise ValidationError(f"The .{limit_ext} limit exceeds the server maximum of "
                                  f"{current_app.config['MAX_REQUEST_MB']} MB.")
    for key, value in values.items():
        set_value(key, value)
    return values
