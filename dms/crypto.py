"""Encryption at rest (AES-256-GCM, envelope encryption).

Every stored file gets its own random 256-bit data key. The data key is wrapped
(AES-256-GCM) with a master key (key-encryption key) from the key ring and
stored in the file header together with the master key's id, so master keys can
be rotated without re-encrypting content.

File layout::

    MAGIC(8) | key_id_len(1) | key_id | wrapped_len(2) | wrapped_dek | base_nonce(8)
    chunk*:  ciphertext_len(4) | AES-GCM(chunk plaintext) incl. 16-byte tag

Chunks hold CHUNK_SIZE bytes of plaintext (the last one may be shorter or
empty). Chunk ``i`` uses nonce ``base_nonce || i`` and the AAD binds the chunk
index and a "final" flag, so chunks cannot be reordered, dropped or truncated
without detection. Fixed-size chunks allow random access (HTTP range requests).

Master keys ("customer-managed keys"):

* ``DMS_MASTER_KEYS``  -- ``id1:<base64 32 bytes>,id2:<base64>``; ``DMS_ACTIVE_KEY_ID`` picks the
  key used for new files (default: the last one listed).
* ``DMS_MASTER_KEY_FILE`` -- JSON file ``{"active": "id", "keys": {"id": "<base64>"}}``.
  If neither is configured, ``<instance>/master.key`` is created on first use.
* ``DMS_KEY_PROVIDER`` -- ``package.module:ClassName`` of a custom provider (for example one that
  unwraps keys through Azure Key Vault, AWS KMS or an HSM). It must offer ``active_key_id``,
  ``wrap(dek) -> (key_id, blob)`` and ``unwrap(key_id, blob) -> dek``.
"""
import base64
import importlib
import io
import json
import logging
import os
import secrets
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

MAGIC = b"DMSENC01"
CHUNK_SIZE = 64 * 1024
TAG = 16
_CT_CHUNK = 4 + CHUNK_SIZE + TAG  # on-disk size of a full chunk


class CryptoError(Exception):
    pass


class LocalKeyRing:
    """Master keys held locally (environment variable or key file)."""

    def __init__(self, keys, active):
        if not keys:
            raise CryptoError("No master keys configured")
        for kid, key in keys.items():
            if len(key) != 32:
                raise CryptoError(f"Master key '{kid}' must be 32 bytes (AES-256)")
            if not kid or len(kid.encode()) > 64:
                raise CryptoError("Invalid master key id")
        if active not in keys:
            raise CryptoError(f"Active key id '{active}' is not in the key ring")
        self.keys, self.active_key_id = keys, active

    def wrap(self, dek):
        nonce = secrets.token_bytes(12)
        return self.active_key_id, nonce + AESGCM(self.keys[self.active_key_id]).encrypt(
            nonce, dek, self.active_key_id.encode())

    def unwrap(self, key_id, blob):
        key = self.keys.get(key_id)
        if key is None:
            raise CryptoError(f"Master key '{key_id}' is not available")
        try:
            return AESGCM(key).decrypt(blob[:12], blob[12:], key_id.encode())
        except Exception as exc:
            raise CryptoError("Could not unwrap the data key") from exc

    def encrypt_small(self, plaintext: bytes) -> str:
        """Encrypt a short secret (e.g. an MFA seed) for storage in the database."""
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self.keys[self.active_key_id]).encrypt(nonce, plaintext, b"field")
        return f"{self.active_key_id}:{base64.b64encode(nonce + ct).decode()}"

    def decrypt_small(self, token: str) -> bytes:
        kid, _, data = (token or "").partition(":")
        raw = base64.b64decode(data)
        key = self.keys.get(kid)
        if key is None:
            raise CryptoError(f"Master key '{kid}' is not available")
        return AESGCM(key).decrypt(raw[:12], raw[12:], b"field")


def _decode_key(text):
    raw = base64.b64decode(text.strip())
    if len(raw) != 32:
        raise CryptoError("Master keys must be 32 bytes, base64-encoded")
    return raw


def load_key_ring(config):
    provider = os.environ.get("DMS_KEY_PROVIDER") or config.get("DMS_KEY_PROVIDER")
    if provider:
        module, _, cls = provider.partition(":")
        return getattr(importlib.import_module(module), cls)(config)

    env_keys = config.get("DMS_MASTER_KEYS") or os.environ.get("DMS_MASTER_KEYS")
    if env_keys:
        keys = {}
        for item in env_keys.split(","):
            kid, _, value = item.strip().partition(":")
            keys[kid.strip()] = _decode_key(value)
        active = config.get("DMS_ACTIVE_KEY_ID") or os.environ.get("DMS_ACTIVE_KEY_ID") or list(keys)[-1]
        return LocalKeyRing(keys, active)

    path = Path(config.get("DMS_MASTER_KEY_FILE") or Path(config["INSTANCE_DIR"]) / "master.key")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        kid = "k1"
        data = {"active": kid, "keys": {kid: base64.b64encode(secrets.token_bytes(32)).decode()}}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        log.warning("Generated a new master encryption key at %s. Back it up securely: without it the "
                    "stored files cannot be decrypted. For production, supply a customer-managed key "
                    "(DMS_MASTER_KEYS / DMS_MASTER_KEY_FILE / DMS_KEY_PROVIDER).", path)
    data = json.loads(path.read_text())
    return LocalKeyRing({k: _decode_key(v) for k, v in data["keys"].items()}, data["active"])


def add_key_to_file(path, make_active=True):
    """Add a new random master key to a key file (rotation). Returns the new key id."""
    path = Path(path)
    data = json.loads(path.read_text())
    n = 1
    while f"k{n}" in data["keys"]:
        n += 1
    kid = f"k{n}"
    data["keys"][kid] = base64.b64encode(secrets.token_bytes(32)).decode()
    if make_active:
        data["active"] = kid
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
    return kid


# --------------------------------------------------------------------------- stream format

def _header(key_id, wrapped, base_nonce):
    kid = key_id.encode()
    return MAGIC + bytes([len(kid)]) + kid + struct.pack(">H", len(wrapped)) + wrapped + base_nonce


def _aad(index, final):
    return MAGIC + struct.pack(">Q", index) + (b"\x01" if final else b"\x00")


def _nonce(base, index):
    return base + struct.pack(">I", index)


def encrypt_stream(ring, src, dst, on_plain=None):
    """Encrypt ``src`` (readable) into ``dst`` (writable). ``on_plain(chunk)`` sees the plaintext."""
    dek = secrets.token_bytes(32)
    key_id, wrapped = ring.wrap(dek)
    base_nonce = secrets.token_bytes(8)
    dst.write(_header(key_id, wrapped, base_nonce))
    aes = AESGCM(dek)
    index = 0
    chunk = _read_full(src, CHUNK_SIZE)
    while True:
        nxt = _read_full(src, CHUNK_SIZE) if len(chunk) == CHUNK_SIZE else b""
        final = len(nxt) == 0
        if on_plain and chunk:
            on_plain(chunk)
        ct = aes.encrypt(_nonce(base_nonce, index), chunk, _aad(index, final))
        dst.write(struct.pack(">I", len(ct)) + ct)
        if final:
            break
        chunk, index = nxt, index + 1


def _read_full(fh, n):
    buf = bytearray()
    while len(buf) < n:
        part = fh.read(n - len(buf))
        if not part:
            break
        buf += part
    return bytes(buf)


def is_encrypted(fh):
    pos = fh.tell()
    head = fh.read(len(MAGIC))
    fh.seek(pos)
    return head == MAGIC


def read_header(fh):
    """Parse the header at the current position. Returns (key_id, wrapped, base_nonce, header_len)."""
    start = fh.tell()
    if fh.read(len(MAGIC)) != MAGIC:
        raise CryptoError("Not an encrypted file")
    kid_len = fh.read(1)[0]
    key_id = fh.read(kid_len).decode()
    (wlen,) = struct.unpack(">H", fh.read(2))
    wrapped = fh.read(wlen)
    base_nonce = fh.read(8)
    if len(wrapped) != wlen or len(base_nonce) != 8:
        raise CryptoError("Truncated header")
    return key_id, wrapped, base_nonce, fh.tell() - start


class DecryptingReader(io.RawIOBase):
    """Seekable, read-only plaintext view of an encrypted file."""

    def __init__(self, fh, ring):
        self._fh = fh
        key_id, wrapped, self._base, self._hlen = read_header(fh)
        self._aes = AESGCM(ring.unwrap(key_id, wrapped))
        fh.seek(0, os.SEEK_END)
        body = fh.tell() - self._hlen
        full, rest = divmod(body, _CT_CHUNK)
        if rest == 0:
            # The file always ends with a final chunk; a body that is an exact multiple of a full
            # chunk means the last full chunk is the final one.
            if full == 0:
                raise CryptoError("Truncated file")
            self._chunks = full
            self._size = full * CHUNK_SIZE
        else:
            if rest < 4 + TAG:
                raise CryptoError("Truncated file")
            self._chunks = full + 1
            self._size = full * CHUNK_SIZE + (rest - 4 - TAG)
        self._pos = 0
        self._cache = (None, b"")

    @property
    def size(self):
        return self._size

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            pos = offset
        elif whence == io.SEEK_CUR:
            pos = self._pos + offset
        else:
            pos = self._size + offset
        self._pos = max(0, pos)
        return self._pos

    def _chunk(self, index):
        if self._cache[0] == index:
            return self._cache[1]
        self._fh.seek(self._hlen + index * _CT_CHUNK)
        (n,) = struct.unpack(">I", _read_full(self._fh, 4))
        ct = _read_full(self._fh, n)
        if len(ct) != n:
            raise CryptoError("Truncated chunk")
        final = index == self._chunks - 1
        try:
            plain = self._aes.decrypt(_nonce(self._base, index), ct, _aad(index, final))
        except Exception as exc:
            raise CryptoError("Stored file failed authentication (corrupted or tampered)") from exc
        self._cache = (index, plain)
        return plain

    def readinto(self, b):
        if self._pos >= self._size:
            return 0
        index, offset = divmod(self._pos, CHUNK_SIZE)
        data = self._chunk(index)[offset:offset + len(b)]
        b[:len(data)] = data
        self._pos += len(data)
        return len(data)

    def read(self, size=-1):
        if size is None or size < 0:
            size = self._size - self._pos
        out = bytearray()
        while len(out) < size and self._pos < self._size:
            index, offset = divmod(self._pos, CHUNK_SIZE)
            data = self._chunk(index)[offset:offset + (size - len(out))]
            out += data
            self._pos += len(data)
        return bytes(out)

    def close(self):
        try:
            self._fh.close()
        finally:
            super().close()


def rewrap_file(path, ring):
    """Re-wrap a file's data key with the active master key (header-only change, atomic)."""
    path = Path(path)
    with open(path, "rb") as fh:
        if not is_encrypted(fh):
            return False
        key_id, wrapped, base_nonce, hlen = read_header(fh)
        if key_id == ring.active_key_id:
            return False
        dek = ring.unwrap(key_id, wrapped)
        new_kid, new_wrapped = ring.wrap(dek)
        tmp = path.with_name(f".{path.name}.rewrap")
        with open(tmp, "wb") as out:
            out.write(_header(new_kid, new_wrapped, base_nonce))
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
    os.replace(tmp, path)
    return True
