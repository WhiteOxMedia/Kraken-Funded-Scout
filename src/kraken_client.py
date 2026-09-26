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


def _settings_creds():
    """Tier 2 — pool settings, resolved through
    pool/kraken_referral.py's database layer. The project root is
    placed on sys.path deterministically (from this file's own
    location, cwd-independent) because runners like miner.py only put
    src/ on the path. Absent pool tree, absent table, or an
    incomplete pair all mean "not provisioned here" — fall through."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _root = str(_Path(__file__).resolve().parents[1])
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from pool import kraken_referral as _pool_kr
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


def check_credentials():
    """Check if Kraken API credentials are configured."""
    api_key, api_secret = _get_kraken_credentials()
    if not api_key or not api_secret:
        print("\n  [Kraken] API credentials not found in keyring.")
        print("  To enable Kraken integration, add your API keys:")
        print("    1. Go to https://www.kraken.com/u/security/api")
        print("    2. Create an API key with 'Query Funds' + 'Deposit Funds' permissions")
        print("    3. Store them using keyring CLI or the setup script")
        return False
    print("  [Kraken] API credentials found.")
    return True
