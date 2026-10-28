"""Time-based one-time passwords (RFC 6238) and recovery codes for multi-factor authentication."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

import segno
from flask import current_app

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    padded = secret + "=" * (-len(secret) % 8)
    return base64.b32decode(padded.upper())


def totp(secret, counter):
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{code:0{DIGITS}d}"


def verify_totp(secret, code, window=1, now=None):
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return False
    counter = int((now or time.time()) // STEP)
    return any(hmac.compare_digest(totp(secret, counter + d), code) for d in range(-window, window + 1))


def provisioning_uri(secret, account, issuer):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg_data_uri(uri):
    return segno.make(uri, error="m").svg_data_uri(scale=4, border=2)


# --------------------------------------------------------------------------- secret storage

def seal(secret):
    ring = current_app.extensions["dms_storage"].ring
    return ring.encrypt_small(secret.encode()) if ring else "plain:" + secret


def unseal(token):
    if not token:
        return None
    if token.startswith("plain:"):
        return token[6:]
    ring = current_app.extensions["dms_storage"].ring
    return ring.decrypt_small(token).decode() if ring else None


# --------------------------------------------------------------------------- recovery codes

def _hash_code(code):
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes(n=8):
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(n)]
    return codes, json.dumps([_hash_code(c) for c in codes])


def use_recovery_code(user, code):
    try:
        hashes = json.loads(user.mfa_recovery_json or "[]")
    except ValueError:
        hashes = []
    h = _hash_code(code or "")
    for stored in hashes:
        if hmac.compare_digest(stored, h):
            hashes.remove(stored)
            user.mfa_recovery_json = json.dumps(hashes)
            return True
    return False


def remaining_recovery_codes(user):
    try:
        return len(json.loads(user.mfa_recovery_json or "[]"))
    except ValueError:
        return 0
