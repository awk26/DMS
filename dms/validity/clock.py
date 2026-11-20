"""Business time zone and "today" (VALIDITY_TIMEZONE). Remaining days are always counted in it."""
import logging
import re
from datetime import datetime, time, timedelta, timezone

from flask import current_app, has_app_context

log = logging.getLogger(__name__)

# Used only when the IANA database is unavailable (e.g. Windows without the tzdata package).
_FALLBACK_OFFSETS = {"asia/kolkata": 330, "asia/calcutta": 330, "utc": 0, "etc/utc": 0, "gmt": 0,
                     "asia/dubai": 240, "asia/singapore": 480, "europe/london": 0}
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?$", re.I)
_warned = set()


def zone(name=None):
    """tzinfo for ``name`` (default VALIDITY_TIMEZONE)."""
    if name is None:
        name = current_app.config.get("VALIDITY_TIMEZONE", "Asia/Kolkata") if has_app_context() else "UTC"
    name = (name or "UTC").strip()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    m = _OFFSET_RE.match(name)
    minutes = None
    if m:
        minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
        minutes = -minutes if m.group(1) == "-" else minutes
    elif name.lower() in _FALLBACK_OFFSETS:
        minutes = _FALLBACK_OFFSETS[name.lower()]
    if name not in _warned:
        _warned.add(name)
        log.warning("Time zone '%s' is not in the IANA database here (install the 'tzdata' package); "
                    "using a fixed offset of %s minutes.", name, minutes or 0)
    return timezone(timedelta(minutes=minutes or 0))


def now_local(tz=None):
    return datetime.now(timezone.utc).astimezone(tz or zone())


def today(tz=None):
    """The current business date."""
    return now_local(tz).date()


def parse_hhmm(value, default=time(9, 0)):
    try:
        hh, mm = str(value or "").strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return default


def scheduled_time():
    raw = current_app.config.get("VALIDITY_SCHEDULER_TIME", "09:00") if has_app_context() else "09:00"
    return parse_hhmm(raw)


def utc_naive(moment):
    """Naive UTC datetime (how the database stores timestamps)."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)
