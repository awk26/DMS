"""Inspect an archived .eml file.

    python verify_eml.py                          # first archived email in the database
    python verify_eml.py <storage key>            # e.g. emails/1/1a153708fc08a28a.eml
    python verify_eml.py path/to/file.eml         # a plain .eml file on disk

Stored files are encrypted at rest, so archived emails are read through the application's
storage layer (which decrypts them); the bytes shown are exactly the original message.
"""
import hashlib
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path


def load(arg):
    if arg and Path(arg).is_file() and not arg.startswith("emails/"):
        return str(Path(arg)), Path(arg).read_bytes()
    from dms import create_app
    from dms.extensions import db
    from dms.models import ArchivedEmail
    from dms.storage import get_storage
    app = create_app(GMAIL_SYNC_ENABLED=False, MAINTENANCE_ENABLED=False)
    with app.app_context():
        key = arg
        if not key:
            record = db.session.query(ArchivedEmail).order_by(ArchivedEmail.id).first()
            if record is None:
                return None, None
            key = record.storage_key
        return key, get_storage().read_bytes(key)


name, raw = load(sys.argv[1] if len(sys.argv) > 1 else None)
if raw is None:
    print("No EML files found.")
    raise SystemExit(1)

message = BytesParser(policy=policy.default).parsebytes(raw)

print("\n--- EML Verification ---")
print("File:", name)
print("Size:", len(raw), "bytes")
print("SHA-256:", hashlib.sha256(raw).hexdigest())
print("From:", message.get("From", "N/A"))
print("To:", message.get("To", "N/A"))
print("Date:", message.get("Date", "N/A"))
print("Subject:", message.get("Subject", "N/A"))
attachments = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
print("Attachments:", ", ".join(attachments) if attachments else "none")
