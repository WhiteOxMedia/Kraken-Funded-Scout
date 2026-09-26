"""
Kraken API client for Monero mining operations.
Handles deposit address retrieval and deposit status monitoring.

CREDENTIALS: one tiered seam, identical to pool/kraken_referral.py's
kraken_credentials() — COMPLETE OS-environment pair, then pool
settings (the same jackpot.db the pool server reads), then the OS
keyring (Windows Credential Manager, service "kraken", as stored by
setup_kraken.py). The pair must be COMPLETE from ONE tier: nothing is
ever mixed across tiers or half-adopted, and no tier is ever written.
"""
import hashlib
import hmac
import time
import urllib.parse
import urllib.request
import json
import base64
import os


KRAKEN_API_URL = "https://api.kraken.com/0/private/"


def _env_creds():
    """Tier 1 — the OS environment, as a COMPLETE pair only (a lone
    env var is never half-adopted)."""
    key = str(os.environ.get("KRAKEN_API_KEY") or "").strip()
    sec = str(os.environ.get("KRAKEN_API_SECRET") or "").strip()
    if key and sec:
        return key, sec
    return None, None


def _pool_kr_ref():
    """The pool's kraken_referral module, or None when the pool tree
    is unavailable (a standalone miner checkout). BOTH path roots are
    placed on sys.path deterministically (from this file's own
    location, cwd-independent): the project ROOT for the
    pool.kraken_referral package import, AND pool/ itself — the module
    imports its siblings by absolute name (database, jackpot_engine,
    push_service), so root alone is not enough and the import would
    silently degrade to 'unavailable' in miner.py, whose runner only
    ever puts src/ on the path."""
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parents[1])
    _pool = str(_Path(_root) / "pool")
    for _p in (_root, _pool):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    try:
        from pool import kraken_referral as _pool_kr
        return _pool_kr
    except Exception:
        return None


def _settings_creds():
    """Tier 2 — pool settings, resolved through
    pool/kraken_referral.py's database layer (via _pool_kr_ref, which
    fixes up BOTH path roots for runners like miner.py that only put
    src/ on the path). Absent pool tree, absent table, or an
    incomplete pair all mean "not provisioned here" — fall through."""
    _pool_kr = _pool_kr_ref()
    if _pool_kr is None:
        return None, None
    try:
        key = str(_pool_kr.db.get_setting("KRAKEN_API_KEY", "") or "").strip()
        sec = str(_pool_kr.db.get_setting("KRAKEN_API_SECRET", "") or "").strip()
    except Exception:
        return None, None
    try:
        key = str(_pool_kr.db.get_setting("KRAKEN_API_KEY", "") or "").strip()
        sec = str(_pool_kr.db.get_setting("KRAKEN_API_SECRET", "") or "").strip()
    except Exception:
        return None, None
    if key and sec:
        return key, sec
    return None, None


def _keyring_creds():
    """Tier 3 — the OS keyring (Windows Credential Manager), service
    "kraken", the exact store setup_kraken.py writes and
    pool/kraken_referral.py reads. COMPLETE pair or nothing; an
    unavailable backend counts as not provisioned."""
    try:
        import keyring
    except Exception:
        return None, None
    try:
        key = str(keyring.get_password("kraken", "kraken_api_key") or "").strip()
        sec = str(keyring.get_password("kraken", "kraken_api_secret") or "").strip()
    except Exception:
        return None, None
    if key and sec:
        return key, sec
    return None, None


def _get_kraken_credentials():
    """(api_key, api_secret) through the SAME tier order as
    pool/kraken_referral.kraken_credentials(): env pair, then pool
    settings, then the OS keyring — complete pairs only, never mixed
    across tiers."""
    for tier in (_env_creds, _settings_creds, _keyring_creds):
        key, sec = tier()
        if key and sec:
            return key, sec
    return None, None


def _kraken_request(endpoint, data, api_key, api_secret):
    """Sign and send an authenticated Kraken API request."""
    urlpath = "/0/private/" + endpoint
    url = KRAKEN_API_URL + endpoint

    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()

    signature = hmac.new(
        base64.b64decode(api_secret), message, hashlib.sha512
    )
    sigdigest = base64.b64encode(signature.digest())

    headers = {
        "API-Key": api_key,
        "API-Sign": sigdigest.decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    req = urllib.request.Request(url, data=postdata.encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def get_xmr_deposit_address():
    """
    Get the Kraken XMR deposit address.
    Returns (address, payment_id) or (None, None) if unavailable.
    """
    api_key, api_secret = _get_kraken_credentials()
    if not api_key or not api_secret:
        return None, None

    nonce = int(time.time() * 1000)

    # Step 1: Get deposit methods for XMR
    methods = _kraken_request(
        "DepositMethods",
        {"asset": "XMR", "nonce": nonce},
        api_key,
        api_secret,
    )

    if methods.get("error"):
        print(f"  [Kraken] DepositMethods error: {methods['error']}")
        return None, None

    method_name = methods["result"][0].get("method", "Monero")
    nonce += 1

    # Step 2: Get deposit address
    addresses = _kraken_request(
        "DepositAddresses",
        {"asset": "XMR", "method": method_name, "nonce": nonce},
        api_key,
        api_secret,
    )

    if addresses.get("error"):
        print(f"  [Kraken] DepositAddresses error: {addresses['error']}")
        return None, None

    for addr_info in addresses["result"]:
        if addr_info.get("address"):
            return addr_info["address"], addr_info.get("expiretm")

    return None, None


def get_deposit_status(asset="XMR"):
    """Check recent deposit status for an asset."""
    api_key, api_secret = _get_kraken_credentials()
    if not api_key or not api_secret:
        return []

    nonce = int(time.time() * 1000)
    result = _kraken_request(
        "DepositStatus",
        {"asset": asset, "nonce": nonce},
        api_key,
        api_secret,
    )

    if result.get("error"):
        print(f"  [Kraken] DepositStatus error: {result['error']}")
        return []

    return result.get("result", [])


def get_balance(asset="XXMR"):
    """Get Kraken balance for an asset (XXMR is Kraken's internal XMR name)."""
    api_key, api_secret = _get_kraken_credentials()
    if not api_key or not api_secret:
        return None

    nonce = int(time.time() * 1000)
    result = _kraken_request("Balance", {"nonce": nonce}, api_key, api_secret)

    if result.get("error"):
        print(f"  [Kraken] Balance error: {result['error']}")
        return None

    return result.get("result", {}).get(asset)


def posture():
    """THE POOL'S POSTURE, DELEGATED (Sep 26): provider_posture() from
    pool/kraken_referral (via _pool_kr_ref) — the SAME dict the admin
    panel's config read embeds and setup_kraken.py prints — so the
    miner reports identical credential posture instead of a private
    dialect. Returns None when the pool tree/db is unavailable (a
    standalone miner checkout): the launcher then falls back to its own
    tier report and says so. Read-only; never dials Kraken."""
    _pool_kr = _pool_kr_ref()
    if _pool_kr is None:
        return None
    try:
        return _pool_kr.provider_posture()
    except Exception:
        return None


def check_credentials():
    """THE TIER REPORT (Sep 26): WHICH credential tier actually serves
    the pair — env, settings (the pool's jackpot.db), or keyring
    (Windows Credential Manager) — instead of a generic found/not-found
    line. Same tier order and complete-pair rule as
    _get_kraken_credentials(); the FIRST complete tier in that order is
    the one every authenticated call will use, so it is the one named.
    A lower tier also holding a complete pair is called out as SHADOWED
    (a rotated keyring pair silently outranked by a stale env var is
    exactly the trap this report exists to expose). The key's last four
    characters only — secret material never leaves the seam.

    When the pool's posture is reachable it is ALSO reported — mode,
    missing-for-live, partial-pair, and the stored key-check verdict —
    the identical facts the admin panel and setup_kraken.py print from
    provider_posture(). The tier line stays the client's OWN resolution
    (never the posture's), so a divergence between the two seams is a
    loud warning, not a silent copy: the miner and the panel must not
    quietly look at different credentials."""
    served, shadowed, key_tail = None, [], ""
    for name, fn in (("env", _env_creds), ("settings", _settings_creds),
                     ("keyring", _keyring_creds)):
        key, sec = fn()
        if key and sec:
            if served is None:
                served, key_tail = name, "…" + key[-4:]
            else:
                shadowed.append(name)
    p = posture()
    if not served:
        print("\n  [Kraken] No complete API key/secret pair found.")
        print("  Checked, in order:")
        print("    1. OS environment (KRAKEN_API_KEY + KRAKEN_API_SECRET)")
        print("    2. Pool settings (the jackpot.db the pool server reads)")
        print("    3. OS keyring (Windows Credential Manager, service 'kraken')")
        if p and p.get("credentials_tier"):
            print(f"  [Kraken] DIVERGENCE: the pool panel resolves a "
                  f"complete pair (tier: {p['credentials_tier']}) but this "
                  f"client could not — investigate before trusting either.")
        print("  To enable Kraken integration:")
        print("    1. Go to https://www.kraken.com/u/security/api")
        print("    2. Create an API key with 'Query Funds' + 'Deposit Funds' permissions")
        print("    3. Store the pair with: python setup_kraken.py (keyring),")
        print("       or set both env vars, or use the admin panel.")
        return False
    line = f"  [Kraken] API credentials found (tier: {served}, key {key_tail})."
    if shadowed:
        line += ("\n  [Kraken] Note: tier(s) " + ", ".join(shadowed) +
                 " also hold a complete pair — SHADOWED by " + served +
                 " (the first complete tier wins).")
    if p is None:
        line += "\n  [Kraken] Pool posture unavailable (standalone box?) — "
        line += "tier report is this client's own resolution."
    else:
        p_tier = p.get("credentials_tier") or ""
        if p_tier and p_tier != served:
            line += (f"\n  [Kraken] DIVERGENCE: the pool panel resolves "
                     f"tier {p_tier} but this client serves {served} — "
                     f"the miner and the panel are looking at different "
                     f"credentials.")
        line += (f"\n  [Kraken] posture: mode={p.get('mode')}, "
                 f"panel tier={p_tier or 'none'}")
        if p.get("settings_partial_pair"):
            line += "\n  [Kraken] posture: INCOMPLETE settings pair "
            line += "(half a key/secret on file)"
        missing = p.get("missing") or []
        if missing:
            line += "\n  [Kraken] posture: missing for live mode: " + ", ".join(missing)
        cc = p.get("credential_check")
        if cc:
            age = cc.get("age_s")
            age_txt = f"{int(age)}s ago" if age is not None else "age unknown"
            line += (f"\n  [Kraken] posture: last key check "
                     f"{str(cc.get('verdict', '?')).upper()} ({age_txt})")
        else:
            line += ("\n  [Kraken] posture: key validity unproven yet — "
                     "run the admin panel's key check once")
    print(line)
    return True
