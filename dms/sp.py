"""Stored-procedure access (MySQL). The procedures live in ``database/procedures.sql``.

Procedures run on the ORM session's own connection, so within one request they see the ORM's
pending work and never wait on its row locks. Note that a procedure which opens its own
transaction (START TRANSACTION) implicitly commits whatever the session had pending.
"""
from pathlib import Path

import pymysql

from .extensions import db
from .validation import ValidationError

PROCEDURES_FILE = Path(__file__).resolve().parent.parent / "database" / "procedures.sql"
SIGNAL_ERROR = 1644  # SIGNAL SQLSTATE '45000' raised by a procedure


def call(name, *args, flush=True, expire=True):
    """Run procedure ``name`` and return its first result set as a list of dicts.

    flush:  write pending ORM changes first so the procedure sees them.
    expire: expire loaded ORM objects afterwards so they re-read what the procedure wrote.
    """
    if flush:
        db.session.flush()
    cursor = db.session.connection().connection.cursor()
    try:
        try:
            cursor.callproc(name, args)
        except pymysql.err.MySQLError as exc:
            if exc.args and exc.args[0] == SIGNAL_ERROR:
                raise ValidationError(exc.args[1]) from None
            raise
        rows = []
        if cursor.description:
            cols = [c[0] for c in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        while cursor.nextset():
            pass
    finally:
        cursor.close()
    if expire:
        db.session.expire_all()
    return rows


def call_one(name, *args, **kwargs):
    rows = call(name, *args, **kwargs)
    return rows[0] if rows else None


def split_script(text):
    """Split a Workbench-style script (DELIMITER $$ ... DELIMITER ;) into single statements."""
    statements, delimiter, buf = [], ";", []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1]
            continue
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        if stripped.endswith(delimiter):
            sql = "\n".join(buf).rstrip()
            statements.append(sql[: -len(delimiter)].strip())
            buf = []
    if "".join(buf).strip():
        statements.append("\n".join(buf).strip())
    return [s for s in statements if s and not s.upper().startswith("USE ")]


AUDIT_ROUTINES = ("sp_audit_log_add",)
AUDIT_TRIGGERS = ("trg_activity_log_no_update", "trg_activity_log_no_delete")


def audit_script():
    """The statements between ``-- @audit-begin`` and ``-- @audit-end`` in procedures.sql."""
    text = PROCEDURES_FILE.read_text(encoding="utf-8")
    start = text.index("-- @audit-begin")
    end = text.index("-- @audit-end")
    block = text[text.index("\n", start) + 1:end]
    return split_script("DELIMITER $$\n" + block)


def ensure_audit_objects(connection):
    """Create the audit procedure and the append-only triggers if they are missing.

    Returns the names created. Needs CREATE ROUTINE and TRIGGER privileges; callers treat a
    failure as a warning (the full script can be installed with `flask install-procedures`).
    """
    from sqlalchemy import text
    have_routines = set(connection.execute(text(
        "SELECT routine_name FROM information_schema.routines "
        "WHERE routine_schema = DATABASE() AND routine_type = 'PROCEDURE'")).scalars())
    have_triggers = set(connection.execute(text(
        "SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE()")).scalars())
    missing = [n for n in AUDIT_ROUTINES if n not in have_routines] + \
              [n for n in AUDIT_TRIGGERS if n not in have_triggers]
    if not missing:
        return []
    raw = connection.connection.cursor()
    try:
        for sql in audit_script():
            raw.execute(sql)
            while raw.nextset():
                pass
    finally:
        raw.close()
    return missing


def install_procedures():
    """(Re)create every procedure from database/procedures.sql. Returns the number of statements run."""
    statements = split_script(PROCEDURES_FILE.read_text(encoding="utf-8"))
    cursor = db.session.connection().connection.cursor()
    try:
        for sql in statements:
            cursor.execute(sql)
            while cursor.nextset():
                pass
    finally:
        cursor.close()
    db.session.commit()
    return len(statements)
