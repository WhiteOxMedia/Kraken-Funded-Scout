"""provision_push.py — OPERATOR PROVISIONING, ONE SHOT (Sep 25).

Generates the VAPID keypair into the settings table and a
PUSH_OPERATOR_TOKEN for the machine leg, IF (and only if) they are
absent — idempotent: an existing key is never overwritten (every
existing browser subscription is bound to its key).

Key FORMAT (hard rule): pywebpush reads the private key with
Vapid.from_string -> b64url(DER). The provisioner stamps every key it
writes with vapid_key_format=der_b64url; a PEM-format key WITHOUT the
stamp is refused (the sender cannot read it — regenerate). The
private key is never printed, never logged, never echoed. Output:
fingerprints only.
"""
import base64
import hashlib
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
import database as db  # noqa: E402


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def main() -> int:
    changed = []

    # ── VAPID keypair ────────────────────────────────────────────
    priv = db.get_setting("VAPID_PRIVATE_KEY", "") or ""
    stamp = db.get_setting("vapid_key_format", "") or ""
    if priv and stamp == "der_b64url":
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_der_private_key)
            load_der_private_key(_unb64u(priv), password=None)
            print(f"VAPID: already provisioned (der_b64url, fingerprint "
                  f"{hashlib.sha256(_unb64u(priv)).hexdigest()[:12]})")
        except Exception as exc:
            print(f"VAPID: PROVISIONED KEY UNREADABLE ({exc}) — "
                  f"delete VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY/"
                  f"vapid_key_format settings and re-run")
            return 1
    elif priv and stamp != "der_b64url":  # PEM-era key, no stamp:
        print("VAPID: refusing PEM-format key without a format stamp — "
              "the sender (Vapid.from_string) cannot read it. "
              "Regenerating REQUIRED: clearing the three settings and "
              "re-provisioning (no subscriptions existed to orphan).")
        db.set_setting("VAPID_PRIVATE_KEY", "")
        db.set_setting("VAPID_PUBLIC_KEY", "")
        db.set_setting("vapid_key_format", "")
        return main()  # re-enter with cleared state
    else:
        from py_vapid import Vapid
        import cryptography.hazmat.primitives.serialization as ser
        v = Vapid()
        v.generate_keys()
        der = v.private_key.private_bytes(
            ser.Encoding.DER, ser.PrivateFormat.PKCS8,
            ser.NoEncryption())
        pub_bytes = v.public_key.public_bytes(
            ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
        db.set_setting("VAPID_PRIVATE_KEY", _b64u(der))
        db.set_setting("VAPID_PUBLIC_KEY", _b64u(pub_bytes))
        db.set_setting("vapid_key_format", "der_b64url")
        print(f"VAPID: GENERATED (der_b64url, public fingerprint "
              f"{hashlib.sha256(pub_bytes).hexdigest()[:12]}); private "
              f"key stored in settings, never printed")
        changed.append("VAPID")

    # ── the machine leg's operator token ─────────────────────────
    if db.get_setting("PUSH_OPERATOR_TOKEN", "") or "":
        print("OPERATOR TOKEN: already provisioned (value withheld)")
    else:
        db.set_setting("PUSH_OPERATOR_TOKEN", secrets.token_hex(32))
        print("OPERATOR TOKEN: GENERATED (stored in settings, value "
              "withheld)")
        changed.append("TOKEN")

    print("DONE" + ("" if changed else " (nothing to do)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
