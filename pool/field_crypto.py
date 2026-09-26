"""pool/field_crypto.py — APPLICATION-LEVEL ENCRYPTION AT REST (Sep 26).

Closes the R2 medium-term item: the users PII columns (email, phone,
full_name, wallet_address) and the push-subscription store are
encrypted at rest. The DB file alone must not yield user identities
or push endpoints.

CIPHER: AES-256-GCM (cryptography package AESGCM) — AEAD, so tampering
with a stored blob fails decryption instead of yielding garbage.
Envelope format (TEXT column, self-describing):

    "enc1:" + b85(nonce(12) || ciphertext || tag(16))

Version prefix "enc1" lets the key-management plan rotate the format
without re-reading ambiguity. Plaintext values are stored UNCHANGED —
"" stays "" — so no code anywhere needs to care whether a column holds
plain or envelope data.

KEY MANAGEMENT (full plan: KEY_MANAGEMENT_PLAN.md at the repo root):
  - 32 random bytes, generated once with secrets.token_hex, stored in
    pool/.field_key (gitignored, ACL'd to the operator account) or
    FIELD_KEY_HEX in the environment for headless deploys.
  - Rotation: new key appended as the ACTIVE key; the previous key
    moves to the DECRYPT-ONLY list; a background re-encrypt pass
    re-writes rows under the new key. Every envelope records which
    key made it (KEY ID IN THE AAD), so old rows stay readable during
    the rotation window.
  - Loss of the key file = loss of the PII columns (login by username
    still works; wallets/push recover from users re-registering). The
    plan requires an operator backup of the key OUTSIDE the DB host.
"""
import base64
import os
import secrets
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAVE_CRYPTO = True
except Exception:                                   # noqa: BLE001
    _HAVE_CRYPTO = False

KEY_DIR = Path(__file__).parent / ".keys"
KEY_FILE = KEY_DIR / "field_key"
PREFIX = "enc1:"
_KEY_CACHE = None                                   # [(key_id, bytes), ...]


def _b85(data: bytes) -> str:
    return base64.b85encode(data).decode("ascii")


def _unb85(text: str) -> bytes:
    return base64.b85decode(text.encode("ascii"))


def generate_key_file() -> dict:
    """One-time operator action (also the rotation step): create a NEW
    32-byte key and make it active; any existing key is demoted to
    decrypt-only. Refuses nothing — rotation is deliberate."""
    global _KEY_CACHE
    raw = secrets.token_bytes(32)
    # short, non-secret label — hex so it is a legal filename everywhere
    # (base85 includes *?<>| which Windows filenames reject)
    key_id = secrets.token_hex(4)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        KEY_FILE.rename(KEY_DIR / ("field_key.retired." + key_id))
    KEY_FILE.write_text(raw.hex() + "\n", encoding="ascii")
    try:
        os.chmod(KEY_FILE, 0o600)                   # POSIX; Windows ACLs inherit
    except OSError:
        pass
    _KEY_CACHE = None                               # force reload
    return {"ok": True, "key_id": key_id, "rotated": True}


def _load_keys():
    """ACTIVE key first (KEY_FILE), then retired ones — every
    field_key.retired.<id> file in the keys dir stays decrypt-only
    until a re-encrypt pass rewrites the rows under the active key.
    FIELD_KEY_HEX in the environment substitutes for a missing key
    file so headless deploys can inject the key without touching disk."""
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    keys = []
    if KEY_FILE.exists():
        try:
            h = KEY_FILE.read_text(encoding="ascii").strip()
            if h:
                keys.append(("k" + _b85(bytes.fromhex(h))[:6],
                             bytes.fromhex(h)))
        except (OSError, ValueError):
            pass
    try:
        for f in sorted(KEY_DIR.glob("field_key.retired.*")):
            try:
                h = f.read_text(encoding="ascii").strip()
                if h:
                    keys.append(("r" + _b85(bytes.fromhex(h))[:6],
                                 bytes.fromhex(h)))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    if not keys and os.environ.get("FIELD_KEY_HEX"):
        try:
            keys.append(("k0", bytes.fromhex(os.environ["FIELD_KEY_HEX"])))
        except ValueError:
            pass
    _KEY_CACHE = keys
    return _KEY_CACHE


def available() -> bool:
    return _HAVE_CRYPTO and bool(_load_keys())


def encrypt(value):
    """plaintext -> envelope string. Non-strings (None, ints) and empty
    strings pass through untouched so callers need no branching."""
    if value is None:
        return None
    s = str(value)
    if not s or s.startswith(PREFIX):
        return s                                    # empty, or already sealed
    keys = _load_keys()
    if not _HAVE_CRYPTO or not keys:
        return s                                    # unconfigured: store plain
    key_id, key = keys[0]                           # ACTIVE key seals
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, s.encode("utf-8"),
                             key_id.encode("ascii"))  # AAD binds the key id
    return PREFIX + key_id + ":" + _b85(nonce + ct)


def decrypt(value):
    """envelope -> plaintext; anything that is not an envelope returns
    unchanged (plaintext rows from before the migration stay readable).
    The stored key id is part of the AAD, so the loop simply tries each
    loaded key against it — the key that sealed the envelope is the
    only one that can open it. A corrupted envelope raises ValueError:
    the caller decides loud vs soft."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    body = value[len(PREFIX):]
    key_id, _, blob = body.partition(":")
    if not blob:
        raise ValueError("field_crypto: malformed envelope")
    raw = _unb85(blob)
    for _, key in _load_keys():
        try:
            return AESGCM(key).decrypt(
                raw[:12], raw[12:],
                key_id.encode("ascii")).decode("utf-8")
        except Exception:                           # noqa: BLE001
            continue
    raise ValueError("field_crypto: no key opens this envelope")


def blind_index(value, domain="wallet"):
    """KEYED BLIND INDEX for equality lookups on an encrypted column
    (users.wallet_idx): HMAC-SHA256 over a key DERIVED from the active
    key (never the AES key itself). Deterministic so `WHERE
    wallet_idx = ?` works; opaque without the key, so the index does
    not leak frequency or plaintext. Empty when crypto is unavailable
    (lookups then degrade to decrypt-and-compare in the caller)."""
    if not value:
        return ""                     # empty identity -> empty index
    keys = _load_keys()
    if not _HAVE_CRYPTO or not keys:
        return ""
    import hashlib
    import hmac as _hmac
    key_id, key = keys[0]
    derived = hashlib.sha256(key + b"blind-index:" + domain.encode()).digest()
    return _hmac.new(derived, str(value).encode("utf-8"),
                     hashlib.sha256).hexdigest()[:32]


def reencrypt(value):
    """Rotation helper: open with ANY key, reseal with the ACTIVE key.
    Plain values pass through to be sealed on the next write."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return encrypt(value)
    plain = decrypt(value)
    return encrypt(plain)
