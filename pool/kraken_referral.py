r"""kraken_referral.py — THE KRAKEN REFERRAL PROJECTION WORKER (Sep 26).

Fetches each user's Kraken referral payout status, computes an
expected referral bonus, converts it into a seeded jackpot
equivalent, and maintains a self-adjusting "possible future jackpot"
(jackpot_projection) that the UI shows as an ESTIMATE.

PROVIDER HONESTY: the Kraken referral API is only used when the
operator has provisioned credentials — KRAKEN_API_KEY /
KRAKEN_API_SECRET (resolved through ONE tiered seam shared with
src/kraken_client.py: complete OS-environment pair first, then
settings, then the OS keyring — Windows Credential Manager, where
setup_kraken.py stores them — the secrets NEVER leave their store
for the database) plus KRAKEN_REFERRAL_ENDPOINT (settings only),
optionally
KRAKEN_REFERRAL_TTL_SECONDS, KRAKEN_REFERRAL_BATCH_SIZE,
KRAKEN_REFERRAL_MAX_RETRIES, KRAKEN_REFERRAL_CONFIDENCE,
KRAKEN_PROJECTION_SPIKE_PCT. Without credentials the worker refuses
the live provider and — only when mode="simulate" — runs the same
pipeline against DETERMINISTIC test fixtures, clearly labeled source
"kraken_referral_sim_v1". Nothing is ever presented as a live Kraken
number when it is not one.

CONFIG COMES FROM settings (the existing convention — no hardcoded
keys, no hardcoded URLs, no env surprises) with ONE read-only
fallback: the OS keyring for the key/secret pair, shared with
src/kraken_client.py so both halves of the project see the same
provisioned identity.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

import database as db
import jackpot_engine as engine
import jackpot_funding as funding
import push_service as push_svc

SOURCE_LIVE = "kraken_referral_api_v1"
SOURCE_SIM = "kraken_referral_sim_v1"
SOURCE_NONE = "kraken_referral_unconfigured_v1"

# Kraken statuses -> the four internal categories.
STATUS_MAP = {
    "active": "Eligible", "eligible": "Eligible", "open": "Eligible",
    "pending": "Pending", "queued": "Pending", "processing": "Pending",
    "paid": "Paid", "paid_out": "Paid", "complete": "Paid",
    "expired": "Ineligible", "invalid": "Ineligible",
    "rejected": "Ineligible", "closed": "Ineligible",
}

PROJECTION_TTL_KEY = "KRAKEN_REFERRAL_TTL_SECONDS"          # default 3600
PROJECTION_BATCH_KEY = "KRAKEN_REFERRAL_BATCH_SIZE"         # default 25
PROJECTION_RETRIES_KEY = "KRAKEN_REFERRAL_MAX_RETRIES"      # default 4
PROJECTION_CONF_KEY = "KRAKEN_REFERRAL_CONFIDENCE"          # default 0.6
PROJECTION_SPIKE_KEY = "KRAKEN_PROJECTION_SPIKE_PCT"        # default 25
PROJECTION_MODE_KEY = "KRAKEN_REFERRAL_MODE"                # "" | simulate


def _cfg(name, default, cast=str):
    raw = db.get_setting(name, "")
    try:
        return cast(raw) if raw not in ("", None) else default
    except (TypeError, ValueError):
        return default


# ── THE CREDENTIAL ACCESSOR (tiered, one seam for the project) ──────
# Every half of the project sees the operator's Kraken identity the
# SAME way, in tier order: the OS environment (a COMPLETE pair only),
# then settings (explicit provisioning through the admin panel or
# setup_kraken.py), then the OS keyring (Windows Credential Manager —
# the store setup_kraken.py writes and src/kraken_client.py resolves
# through the same order, so there is exactly one credential truth).
# Read-only: neither the env nor the keyring is ever written, and no
# tier ever copies a secret into another — that is the entire point
# of tiered storage. Import-guarded so boxes without a keyring
# backend degrade to settings-only behavior instead of erroring.
KEYRING_SERVICE = "kraken"
KEYRING_KEY_USER = "kraken_api_key"
KEYRING_SEC_USER = "kraken_api_secret"


def _env_creds():
    """THE OS ENVIRONMENT tier (highest): KRAKEN_API_KEY and
    KRAKEN_API_SECRET as a COMPLETE pair — a lone env var is never
    half-adopted, the same no-mix rule the keyring tier follows.
    This is the top of the seam src/kraken_client.py mirrors."""
    key = str(os.environ.get("KRAKEN_API_KEY") or "").strip()
    sec = str(os.environ.get("KRAKEN_API_SECRET") or "").strip()
    if key and sec:
        return {"key": key, "secret": sec}
    return None


def _keyring_creds():
    try:
        import keyring
    except Exception:
        return None
    try:
        key = str(keyring.get_password(KEYRING_SERVICE,
                                       KEYRING_KEY_USER) or "").strip()
        sec = str(keyring.get_password(KEYRING_SERVICE,
                                       KEYRING_SEC_USER) or "").strip()
    except Exception:
        # a locked/unavailable backend counts as "not provisioned
        # there" — settings remain the source of truth
        return None
    if key and sec:
        return {"key": key, "secret": sec}
    return None


def kraken_credentials():
    """(api_key, api_secret, source) with source in 'env', 'settings',
    'keyring', or '' (unconfigured). The pair must be COMPLETE from
    ONE tier — no mixing a settings key with a keyring secret, and no
    lone environment variable is half-adopted."""
    env = _env_creds()
    if env:
        return env["key"], env["secret"], "env"
    key = str(db.get_setting("KRAKEN_API_KEY", "") or "").strip()
    sec = str(db.get_setting("KRAKEN_API_SECRET", "") or "").strip()
    if key and sec:
        return key, sec, "settings"
    kr = _keyring_creds()
    if kr:
        return kr["key"], kr["secret"], "keyring"
    return "", "", ""


def _ttl():
    return max(60, _cfg(PROJECTION_TTL_KEY, 3600, int))


def _confidence():
    return min(1.0, max(0.0, _cfg(PROJECTION_CONF_KEY, 0.6, float)))


def provider_mode():
    """'simulate' when explicitly requested (the operator's labeled
    dry-run ALWAYS wins — stale credentials must not trap the panel
    in live mode); 'live' iff credentials AND endpoint provisioned;
    otherwise 'unconfigured'."""
    if (db.get_setting(PROJECTION_MODE_KEY, "") or "") == "simulate":
        return "simulate"
    key, sec, _src = kraken_credentials()
    ep = db.get_setting("KRAKEN_REFERRAL_ENDPOINT", "")
    if key and sec and ep:
        return "live"
    return "unconfigured"


def provider_posture():
    """THE POSTURE READ (what setup_kraken.py prints after storing
    anything, and any admin surface may reuse): where the credentials
    live, whether the endpoint is provisioned, and whether the
    projection is live — derived from real state only. Tails only:
    the secret material itself never leaves the credential accessor.
    The endpoint is settings-only by charter (the keyring never
    stores URLs), and a settings tier holding exactly one half of the
    pair is flagged, not mixed — the accessor's no-mix rule is the
    single source of truth for what actually counts."""
    key = str(db.get_setting("KRAKEN_API_KEY", "") or "").strip()
    sec = str(db.get_setting("KRAKEN_API_SECRET", "") or "").strip()
    kr_ = _keyring_creds()
    env = _env_creds()
    eff_key, eff_sec, tier = kraken_credentials()
    ep = str(db.get_setting("KRAKEN_REFERRAL_ENDPOINT", "") or "").strip()
    mode = provider_mode()
    missing = []
    if not tier:
        # no complete pair resolvable from any single tier — name the
        # fragment(s) the operator still owes (half-pair -> the absent
        # half; nothing -> both)
        if key and not sec:
            missing.append("KRAKEN_API_SECRET")
        elif sec and not key:
            missing.append("KRAKEN_API_KEY")
        else:
            missing.extend(["KRAKEN_API_KEY", "KRAKEN_API_SECRET"])
    if not ep:
        missing.append("KRAKEN_REFERRAL_ENDPOINT")
    return {
        "mode": mode,
        "projection_live": mode == "live",
        "credentials_tier": tier,
        "settings_key_set": bool(key),
        "settings_secret_set": bool(sec),
        "settings_partial_pair": bool(key) != bool(sec),
        "keyring_pair": bool(kr_),
        "env_pair": bool(env),
        "endpoint_set": bool(ep),
        "endpoint": ep,
        # tails of the EFFECTIVE pair (the one provider_mode actually
        # resolves), labeled by tier at print time — plus the tails of
        # any settings-side fragments so a stale half-pair can be
        # identified without ever exposing the material
        "api_key_tail": ("…" + eff_key[-4:]) if eff_key else "",
        "api_secret_tail": ("…" + eff_sec[-4:]) if eff_sec else "",
        "settings_key_tail": ("…" + key[-4:]) if key else "",
        "settings_secret_tail": ("…" + sec[-4:]) if sec else "",
        "missing": missing,
    }


def sign_request(api_secret, path, nonce, body):
    """Kraken's auth scheme (API-Key header, API-Sign =
    HMAC-SHA512(path + SHA256(nonce + body), b64secret))."""
    api_secret = api_secret.strip()
    pad = len(api_secret) % 4
    if pad:
        api_secret += "=" * (4 - pad)
    secret = hashlib.sha256((str(nonce) + body).encode()).digest()
    return hmac.new(api_secret.encode(), path.encode() + secret,
                    hashlib.sha512).hexdigest()


def _kraken_post(endpoint, api_key, api_secret, body):
    nonce = int(time.time() * 1000)
    path = "/" + urllib.parse.urlparse(endpoint).path.lstrip("/")
    data = urllib.parse.urlencode({**body, "nonce": nonce}).encode()
    req = urllib.request.Request(
        endpoint, data=data, method="POST",
        headers={"API-Key": api_key, "API-Sign":
                 sign_request(api_secret, path, nonce, body),
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_kraken_response(resp):
    """Extract the canonical fields from one Kraken referral record.
    Tolerant of key spellings; unknown -> unknown status. Returns
    (referral_id, status, total_paid, pending_amount, explicit_amount,
    last_updated)."""
    if not isinstance(resp, dict):
        return None, "unknown", 0.0, 0.0, None, ""
    g = resp.get

    def _money(*names):
        for n in names:
            v = g(n)
            if v is None:
                continue
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                continue
        return 0.0

    rid = str(g("referral_id") or g("referralId")
              or g("ref_code") or g("id") or "") or None
    raw_status = str(g("status") or g("referral_status")
                     or "unknown").strip().lower()
    status = STATUS_MAP.get(raw_status, "unknown")
    total_paid = _money("total_paid", "paid_amount", "amount_paid_usd",
                        "total_paid_usd")
    # THE explicit amount: any of the pending-name aliases carrying a
    # usable number counts as Kraken-provided (estimate_used = 0
    # downstream); garbage strings fall through to the estimate path.
    explicit = None
    for _n in ("pending_amount", "pending_usd", "expected_amount"):
        if g(_n) is None:
            continue
        try:
            explicit = max(0.0, float(g(_n)))
            break
        except (TypeError, ValueError):
            continue
    pending = explicit if explicit is not None else 0.0
    return rid, status, total_paid, pending, explicit, \
        str(g("last_updated") or g("lastUpdated") or "")


def compute_expected_bonus(status, total_paid, pending_amount,
                           explicit_amount=None, confidence=None):
    """THE FORMULA (documented for the methodology modal):
      - explicit Kraken pending/expected amount wins, used as-is
        (conservative = best = explicit, estimate_used = 0);
      - otherwise a conservative estimate from historical referral
        velocity (the funding ledger's own 14-day rate — the pool's
        real observed referral economics), scaled by the configurable
        confidence factor (default 0.6): conservative = rate * conf;
        best = rate (estimate_used = 1).
    Paid users contribute their actual total_paid as the floor.
    Rounding: USD cents, half-up at write time (round())."""
    if confidence is None:
        confidence = _confidence()
    rate = funding.referral_velocity_usd()          # USD/day, 14-day
    if status == "Paid":
        return round(max(total_paid, 0.0), 2), \
            round(max(total_paid, 0.0), 2), 0
    if explicit_amount is not None:
        return round(explicit_amount, 2), round(explicit_amount, 2), 0
    if status in ("Pending", "Eligible"):
        return round(rate * confidence, 2), round(rate, 2), 1
    return 0.0, 0.0, 0                               # Ineligible/unknown


def persist_snapshot(user_id, referral_id, status, total_paid, pending,
                     exp_cons, exp_best, seeded, estimate_used, source):
    """One immutable history row per fetch (the table IS the audit
    trail for the per-user numbers)."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO kraken_referral_snapshot (user_id, referral_id,"
            " kraken_status, total_paid, pending_amount,"
            " expected_bonus_conservative, expected_bonus_best,"
            " seeded_equivalent, estimate_used, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(user_id), referral_id, status, round(total_paid, 2),
             round(pending, 2), round(exp_cons, 2), round(exp_best, 2),
             round(seeded, 2), int(estimate_used), source))


def emit_audit(user_id, action, **details):
    """Audit-or-die, same convention as push_service._audit."""
    db.add_audit_log(user_id, action, json.dumps(details))


# ── simulation fixtures (deterministic, NEVER shown as live) ─────────
SIM_RESPONSES = {
    "SIM-PAID-001": {"referral_id": "SIM-PAID-001", "status": "paid",
                     "total_paid": 40.0, "last_updated": "sim"},
    "SIM-PEND-002": {"referral_id": "SIM-PEND-002", "status": "pending",
                     "pending_amount": 25.0, "last_updated": "sim"},
    "SIM-ELIG-003": {"referral_id": "SIM-ELIG-003", "status": "active",
                     "last_updated": "sim"},
    "SIM-EXPI-004": {"referral_id": "SIM-EXPI-004", "status": "expired",
                     "last_updated": "sim"},
    "SIM-ODD-005": {"referral_id": "SIM-ODD-005", "status": "quantum",
                    "total_paid": "not-a-number", "last_updated": "sim"},
}


def fetch_user_status(user_id, referral_id):
    """One user's referral status through the ACTIVE provider.
    live -> signed Kraken POST with backoff; simulate -> fixtures;
    unconfigured -> hard refusal (the caller records it and stops)."""
    mode = provider_mode()
    if mode == "unconfigured":
        return {"ok": False, "reason": "unconfigured",
                "error": "no Kraken API credentials provisioned"}
    if mode == "simulate":
        resp = SIM_RESPONSES.get(referral_id)
        if resp is None:
            resp = {"referral_id": referral_id, "status": "active",
                    "last_updated": "sim"}
        return {"ok": True, "raw": resp, "mode": mode}
    api_key, api_secret, _src = kraken_credentials()
    endpoint = db.get_setting("KRAKEN_REFERRAL_ENDPOINT", "")
    tries = _cfg(PROJECTION_RETRIES_KEY, 4, int)
    delay = 1.0
    for attempt in range(tries):
        try:
            resp = _kraken_post(endpoint, api_key, api_secret,
                                {"referral_id": referral_id})
            err = resp.get("error") if isinstance(resp, dict) else None
            if err and err not in ("EAPI:InvalidKey", []):
                if any("Rate limit" in str(e) or
                       str(e).startswith("EAPI:Rate") for e in
                       (err if isinstance(err, list) else [err])):
                    raise RuntimeError("rate limited")
                return {"ok": False, "reason": "kraken_error",
                        "error": str(err)[:200], "mode": mode}
            result = resp.get("result") if isinstance(resp, dict) else None
            if isinstance(result, list):
                result = result[0] if result else None
            raw = result if isinstance(result, dict) else resp
            return {"ok": True, "raw": raw, "mode": mode}
        except Exception as exc:                    # noqa: BLE001
            if attempt == tries - 1:
                return {"ok": False, "reason": "fetch_failed",
                        "error": str(exc)[:200], "mode": mode}
            time.sleep(delay)
            delay *= 2                              # exponential backoff
    return {"ok": False, "reason": "fetch_failed", "error": "retries exhausted",
            "mode": mode}


def notify_window_closing():
    """THE NOTICE (user flow, Sep 26): before the referral window
    expires and the bonus is NOT gifted, every subscriber with the
    jackpot family on gets one push. Rate: 2/hour/user caps it."""
    return push_svc.send_to_subscribers("referral_window")


def run_worker(subset_limit=None):
    """THE PIPELINE: discover -> fetch -> normalize -> compute ->
    persist -> audit -> aggregate -> anomaly guard. Returns the run
    summary dict (also what the canary report quotes). Batching is
    bounded by the configured batch size; the loop is sequential and
    every fetch honors the shared backoff in fetch_user_status."""
    batch_size = max(1, _cfg(PROJECTION_BATCH_KEY, 25, int))
    started = time.time()
    mode = provider_mode()
    source = (SOURCE_LIVE if mode == "live"
              else SOURCE_SIM if mode == "simulate" else SOURCE_NONE)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, referral_code, kraken_referral_id "
            "FROM users ORDER BY id").fetchall()
    if subset_limit:
        rows = rows[:int(subset_limit)]
    processed = unmapped = errors = 0
    seed_total = 0.0
    for r in rows:
        user_id = int(r["id"])
        # THE REAL KRAKEN ID (user order, Sep 26): discovery keys on
        # kraken_referral_id ONLY — the operator-mapped id the provider
        # actually knows. The internal referral_code is the site's own
        # invite system and is NEVER sent to Kraken as an identity:
        # a legacy fallback here would manufacture provider errors for
        # ids Kraken has never seen.
        rid = str(r["kraken_referral_id"] or "").strip()
        if not rid:
            unmapped += 1
            emit_audit(user_id, "kraken_referral.unmapped",
                       username=r["username"],
                       reason="no kraken_referral_id on file "
                              "(operator mapping needed)")
            continue
        res = fetch_user_status(user_id, rid)
        if not res.get("ok"):
            errors += 1
            emit_audit(user_id, "kraken_referral.fetch_error",
                       reason=res.get("reason"),
                       error=str(res.get("error"))[:160])
            if res.get("reason") == "unconfigured":
                break
            continue
        rid_n, status, total_paid, pending, explicit, _lu = \
            normalize_kraken_response(res["raw"])
        exp_cons, exp_best, est_used = compute_expected_bonus(
            status, total_paid, pending, explicit)
        seeded = exp_cons * 1.0                    # conversion_factor = 1
        persist_snapshot(user_id, rid_n, status, total_paid, pending,
                         exp_cons, exp_best, seeded, est_used, source)
        emit_audit(user_id, "kraken_referral.fetched",
                   status=status, estimate_used=bool(est_used))
        if status in ("Paid", "Pending", "Eligible"):
            seed_total += seeded
        processed += 1
    # THE baseline is the pot the UI actually shows (engine balance),
    # not the daily accrual formula — the card sits under the hero
    # plate and must never contradict it.
    stats = engine.get_pool_stats()
    current_usd = round(float(stats.get("jackpot_usd") or 0.0), 2)
    seed = round(seed_total, 2)
    possible = round(current_usd + seed, 2)
    prev = latest_projection()
    anomaly = 0
    spike = _cfg(PROJECTION_SPIKE_KEY, 25.0, float)
    if prev and prev.get("possible_future_jackpot"):
        base = float(prev["possible_future_jackpot"])
        if base > 0 and abs(possible - base) / base * 100.0 > spike:
            anomaly = 1
            emit_audit(None, "jackpot_projection.delta_spike",
                       possible=possible, previous=base,
                       pct=round(abs(possible - base) / base * 100.0, 2))
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO jackpot_projection (possible_future_jackpot,"
            " referral_seed, current_jackpot, count_of_users_included,"
            " anomaly, source) VALUES (?, ?, ?, ?, ?, ?)",
            (possible, seed, current_usd, processed, anomaly, source))
    emit_audit(None, "jackpot_projection.updated",
               source=source, possible=possible, seed=seed,
               users=processed, errors=errors, anomaly=anomaly)
    return {"mode": mode, "source": source, "processed": processed,
            "unmapped": unmapped, "errors": errors,
            "referral_seed": seed, "current_jackpot": current_usd,
            "possible_future_jackpot": possible, "anomaly": anomaly,
            "batch_size": batch_size,
            "duration_s": round(time.time() - started, 3),
            "current_jackpot_xmr": stats.get("jackpot_xmr")}


def latest_projection():
    """The display read: latest jackpot_projection row (TTL-guarded)."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM jackpot_projection "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    d = dict(row)
    d["fresh"] = (time.time() - float(d["updated_at"])) <= _ttl()
    return d
