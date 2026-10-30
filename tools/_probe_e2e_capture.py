"""THROWAWAY probe — THE RECEIVER-SIDE PROOF (Sep 25).

A local HTTP receiver stands in for the push service endpoint (the
preview browser cannot mint a real FCM subscription, so the one hop
we cannot drive locally is replaced by a controlled receiver). The
REAL sender chain runs unchanged: push_service.send_to_user ->
pywebpush -> HTTP POST. The captured wire bytes are then verified
INDEPENDENTLY (not by pywebpush): the VAPID JWT's ES256 signature
against the provisioned public key, and the aes128gcm payload
decrypted by a hand-rolled RFC 8188 implementation.

Sandbox DB throughout — the live jackpot.db is never touched.
"""
import base64
import json
import secrets
import struct
import sys

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="push_e2e_probe_"))
sys.path.insert(0, str(HERE / "pool"))

import database as db                                    # noqa: E402
db.DB_PATH = SANDBOX / "jackpot.db"
db.init_db()

UID = int(db.create_user("e2eprobe",
                         "x" * 64, "e2eprobe@example.com"))

# provision VAPID into the SANDBOX (same generation as provision_push)
from py_vapid import Vapid                               # noqa: E402
import cryptography.hazmat.primitives.serialization as ser  # noqa: E402
v = Vapid()
v.generate_keys()
der = v.private_key.private_bytes(
    ser.Encoding.DER, ser.PrivateFormat.PKCS8, ser.NoEncryption())
pub_bytes = v.public_key.public_bytes(
    ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
b64u = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
db.set_setting("VAPID_PRIVATE_KEY", b64u(der))
db.set_setting("VAPID_PUBLIC_KEY", b64u(pub_bytes))
db.set_setting("vapid_key_format", "der_b64url")

CAP = {}


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        CAP["auth"] = self.headers.get("Authorization", "")
        CAP["ttl"] = self.headers.get("TTL", "")
        CAP["ce"] = self.headers.get("Content-Encoding", "")
        CAP["topic"] = self.headers.get("Topic", "")
        CAP["urgency"] = self.headers.get("Urgency", "")
        n = int(self.headers.get("Content-Length", 0))
        CAP["body"] = self.rfile.read(n)
        self.send_response(201)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 8443), Receiver)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# subscribe a LOCAL endpoint for the sandbox user (an https URL is the
# service's own rule; https://127.0.0.1.localhost names THIS machine
# while still passing the scheme check)
ENDPOINT = "https://127.0.0.1.localhost:8443/push/probeTokenXYZ"
import push_service as ps                                # noqa: E402
# REAL UA keys: the receiver's own P-256 pair (the ECDH in aes128gcm
# needs valid points — a fake byte pattern fails inside encryption)
ua_priv = ec.generate_private_key(ec.SECP256R1())
UA_PUB_RAW = ua_priv.public_key().public_bytes(
    ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
UA_AUTH = secrets.token_bytes(16)
sub = ps.subscribe(UID, {
    "endpoint": ENDPOINT,
    "keys": {"p256dh": b64u(UA_PUB_RAW), "auth": b64u(UA_AUTH)},
})
print("subscribe:", sub)

import requests as _rq
_real_post = _rq.post


def _local_post(url, *a, **k):
    """Transport seam ONLY: the local receiver has no cert, so the
    https endpoint URL is redirected to plain http on this box. The
    crypto, the headers, and the JWT's aud (minted from the ORIGINAL
    https endpoint) are untouched — that is exactly what is proven."""
    if url.startswith("https://127.0.0.1.localhost:8443"):
        url = url.replace("https://127.0.0.1.localhost:8443",
                          "http://127.0.0.1:8443")
    return _real_post(url, *a, **k)


_rq.post = _local_post

# DEBUG: one direct send with the real exception surfaced
from pywebpush import webpush, WebPushException as _WPE  # noqa: E402
try:
    webpush(
        subscription_info={"endpoint": ENDPOINT,
                           "keys": {"p256dh": b64u(UA_PUB_RAW),
                                    "auth": b64u(UA_AUTH)}},
        data=json.dumps({"probe": True}),
        vapid_private_key=db.get_setting("VAPID_PRIVATE_KEY", ""),
        vapid_claims={"sub": "mailto:operator@xmrjackpot.com"},
    )
    print("DEBUG direct webpush: OK")
except _WPE as e:
    import traceback
    traceback.print_exc()
    print("DEBUG direct webpush FAILED:", str(e)[:300])

res = ps.send_to_user(UID, "signal_buy", {"signal": "E2E PROOF 2026"})
print("send:", res)
assert res.get("sent") == 1, "the send did not deliver"
srv.shutdown()

# ── 1. THE WIRE ────────────────────────────────────────────────────
auth = CAP.get("auth", "")
body = CAP.get("body", b"")
print("\ncaptured: TTL=%s CE=%s Urgency=%s Topic=%s bodylen=%d"
      % (CAP.get("ttl"), CAP.get("ce"), CAP.get("urgency"),
         CAP.get("topic"), len(body)))
assert auth.startswith("vapid t="), "missing vapid authorization"
assert CAP.get("ce") == "aes128gcm", "wrong content encoding"

# ── 2. VAPID JWT — ES256 signature verified against the REAL key ───
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import hashes         # noqa: E402
tok = auth.split("t=")[1].split(",")[0].strip()
h64, p64, s64 = tok.split(".")
header = json.loads(base64.urlsafe_b64decode(h64 + "=" * 3))
claims = json.loads(base64.urlsafe_b64decode(p64 + "=" * 3))
sig = base64.urlsafe_b64decode(s64 + "=" * 3)
if len(sig) == 64:
    # JOSE ES256 carries RAW r||s; cryptography.verify wants DER
    from cryptography.hazmat.primitives.asymmetric.utils import (
        encode_dss_signature)
    sig = encode_dss_signature(int.from_bytes(sig[:32], "big"),
                               int.from_bytes(sig[32:], "big"))
v.public_key.verify(sig, f"{h64}.{p64}".encode("ascii"),
                    ec.ECDSA(hashes.SHA256()))
print("JWT header:", header)
print("JWT claims:", {k: claims[k] for k in ("aud", "sub", "exp")})
now = time.time()
assert header["alg"] == "ES256" and header["typ"] == "JWT"
assert claims["aud"] == "https://127.0.0.1.localhost:8443"
assert claims["sub"] == "mailto:operator@xmrjackpot.com"
assert int(claims["exp"]) > now and int(claims["exp"]) <= now + 86400

# ── 3. THE PAYLOAD — hand-rolled RFC 8188 aes128gcm decryption ─────
# (our key pair for the receiver side: the SAME keys we subscribed with
#  are the UA keys; the sender embeds its ephemeral public key in the
#  record header's keyid field)
salt = body[:16]
rs = struct.unpack("!I", body[16:20])[0]
idlen = body[20]
keyid = body[21:21 + idlen]
ct = body[21 + idlen:]
assert idlen == 65, "aes128gcm keyid must carry the ephemeral pubkey"
ua_pub = UA_PUB_RAW                       # the subscribed p256dh, raw
as_pub = keyid                            # sender's ephemeral public key
auth_secret = UA_AUTH                     # the subscribed auth, raw


def hkdf(salt, ikm, info, length):
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length,
                salt=salt, info=info).derive(ikm)


# RFC 8291 §2.2 (aes128gcm), the correct two-step derivation:
#   IKM   = HKDF(salt=auth_secret, ikm=ecdh_shared, info=WebPush:info||uapub||aspub, 32)
#   cek   = HKDF(salt=record_salt, ikm=IKM, info="Content-Encoding: aes128gcm", 16)
#   nonce = HKDF(salt=record_salt, ikm=IKM, info="Content-Encoding: nonce", 12)
shared = ua_priv.exchange(
    ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), as_pub))
ikm = hkdf(auth_secret, shared,
           b"WebPush: info\x00" + ua_pub + as_pub, 32)
cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
plain = AESGCM(cek).decrypt(nonce, ct, None)       # raises on ANY tamper
payload = plain.split(b"\x02")[0]                  # strip padding delimiter
doc = json.loads(payload)
print("\ndecrypted payload:", json.dumps(doc, ensure_ascii=True))
ev = ps.EVENTS["signal_buy"]
assert doc["title"] == ev["title"] and doc["tag"] == ev["tag"]
assert doc["body"] == ev["body"] and doc["url"] == ev["url"]
assert doc["signal"] == "E2E PROOF 2026"

print("\nPROBE VERDICT: JWT signature VERIFIED against the provisioned "
      "key; aud/exp/sub claims correct; aes128gcm payload decrypted "
      "independently; template byte-exact; GCM tag proves zero "
      "tampering in flight.")
