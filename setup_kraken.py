"""
One-time setup script to store Kraken API credentials in Windows
Credential Manager and provision the referral-projection endpoint.
Run: python setup_kraken.py
"""
import getpass
import json
import keyring
import time
import urllib.request

from pathlib import Path
import sys

# Import pool/ directly from this repo so the settings writes hit the
# same jackpot.db the pool server reads — DB_PATH resolves from
# database.py's __file__, so this is cwd-independent.
sys.path.insert(0, str(Path(__file__).resolve().parent / "pool"))

import database as db            # noqa: E402
import kraken_referral as kr     # noqa: E402

ENDPOINT_KEY = "KRAKEN_REFERRAL_ENDPOINT"


def _ask_endpoint(current):
    """Ask for the referral endpoint. Blank keeps the current value
    (mirroring the admin panel's write-only convention); input must be
    an http(s) URL, same rule the panel enforces."""
    if current:
        hint = f"current: {current}"
    else:
        hint = "e.g. https://.../0/private/ReferralStatus (operator's own referral source)"
    while True:
        ep = input(f"Enter KRAKEN_REFERRAL_ENDPOINT ({hint}): ").strip()
        if not ep:
            if current:
                print("  keeping existing endpoint.")
                return current
            print("  no endpoint given — projection stays fail-closed.")
            return ""
        if ep.startswith(("http://", "https://")) and len(ep) <= 300:
            return ep
        print("  endpoint must be an http(s) URL (<=300 chars) — try again.")


def _print_posture(p):
    """The post-store posture: where credentials live (tier), whether
    the endpoint is provisioned, and whether the projection is live.
    Tails only — the script never echoes secret material."""
    print()
    print("─" * 50)
    print("  Provider posture")
    print("─" * 50)
    tier = p["credentials_tier"]
    tier_txt = tier if tier else "none (no complete key+secret pair)"
    print(f"  credentials: {tier_txt}", end="")
    if p["settings_partial_pair"]:
        print("  [INCOMPLETE settings pair: half a key/secret on file]")
    else:
        print()
    if p["keyring_pair"]:
        print("  keyring pair (Windows Credential Manager): present")
    if p["settings_key_set"]:
        print(f"  api key tail (settings): {p['settings_key_tail']}")
    if p["settings_secret_set"]:
        print(f"  api secret tail (settings): {p['settings_secret_tail']}")
    if p["endpoint_set"]:
        print(f"  endpoint: {p['endpoint']}")
    else:
        print("  endpoint: not set (settings only; keyring never holds URLs)")
    print(f"  mode: {p['mode']}  "
          f"({'projection is LIVE' if p['projection_live'] else 'projection FAIL-CLOSED'})")
    if p["missing"]:
        print("  missing for live mode: " + ", ".join(p["missing"]))
    cc = p.get("credential_check")
    if cc:
        age = cc.get("age_s")
        age_txt = f"{int(age)}s ago" if age is not None else "age unknown"
        print(f"  last credential check: {cc.get('verdict', '?').upper()} "
              f"({age_txt}) — the proof the pair WORKS, not just exists; "
              "re-run the admin panel's key check to refresh")
    else:
        print("  last credential check: none yet — validity is unproven "
              "(the admin panel's key check performs one read-only "
              "Balance call)")
    if p["mode"] == "live":
        print("  Projection is LIVE — Kraken referral data will be "
              "fetched per-user and audited.")
    elif p["mode"] == "simulate":
        print("  Mode is SIMULATE — same pipeline on deterministic "
              "fixtures; nothing shown as live.")
    else:
        print("  Projection is FAIL-CLOSED: the worker refuses to "
              "fabricate referral numbers until the missing items "
              "are provisioned.")


def _fetch_projection(base_url):
    """The one network read of the cross-check: GET the server's
    PUBLIC projection read (no session exists at setup time — the
    admin config read is out of reach, and this seam needs no
    secrets). Returns its parsed JSON or raises on any server/HTTP
    problem; the caller owns the fail-soft decision."""
    with urllib.request.urlopen(base_url + "/api/jackpot/projection",
                                timeout=4) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def _crosscheck_server(base_url="http://127.0.0.1:3094"):
    """Final step: compare what the RUNNING pool server resolves as
    provider mode (its /api/jackpot/projection echoes
    kr.provider_mode() live, evaluated in the server process) with
    what this script sees through the same pool/ modules locally.
    They read the SAME settings DB, so a divergence is the signal of
    a real deployment seam: the server was started before the keys
    landed (env-tier shadowing a settings pair), a different DB, or
    an unreachable keyring. No secrets cross the wire — posture and
    response carry flags, tails, and a mode string only.

    The projection's `source` and `fresh` fields describe the
    PERSISTED worker row (last computed at the previous scheduler
    tick) and are never compared as posture — only the live `mode`
    is. Returns True when the verdict printed is DIVERGENT (so a
    caller can surface it), and never raises: an unreachable or
    non-JSON server prints a notice and returns False."""
    print()
    print("─" * 50)
    print("  Cross-check vs the running pool server")
    print("─" * 50)
    try:
        proj = _fetch_projection(base_url)
    except Exception as e:
        print(f"  pool server not reachable at {base_url} "
              f"({type(e).__name__}: {e}) —")
        print("  start it (start_pool.bat) and re-run this script to "
              "cross-check.")
        return False
    srv_mode = str(proj.get("mode") or "")
    loc_mode = kr.provider_mode()
    src = str(proj.get("source") or "none")
    fresh = bool(proj.get("fresh"))
    stamp = proj.get("updated_at")
    if stamp:
        try:
            age = max(0, int(time.time() - float(stamp)))
            src_txt = f"{src}, {age}s old (last worker tick)"
        except Exception:
            src_txt = src
    else:
        src_txt = "none persisted yet (projection still current-only)"
    print(f"  server mode: {srv_mode or '(absent)'}")
    print(f"  local mode:  {loc_mode}")
    print(f"  projection source: {src_txt}, fresh={fresh}")
    print("  (source/fresh describe the persisted worker row — mode is "
          "read live)")
    if srv_mode == loc_mode:
        print(f"  OK: local and running server agree — mode {loc_mode}.")
        if loc_mode == "unconfigured":
            print("  (both fail-closed: provision the remaining "
                  "posture items above, then restart the server so it "
                  "picks the keys up)")
        return False
    print(f"  DIVERGENT: the running server resolves {srv_mode or 'nothing'} "
          f"while this box resolves {loc_mode}.")
    print("  Both read the same settings DB — if this is unexpected, "
          "restart the server (an env-tier pair it started with can "
          "shadow the pair just stored) or check its DB_PATH.")
    return True


def main():
    print("=" * 50)
    print("  Kraken API Key Setup")
    print("=" * 50)
    print()
    print("Get your API keys at: https://www.kraken.com/u/security/api")
    print("Required permissions: Query Funds + Deposit Funds")
    print()

    api_key = input("Enter your Kraken API Key: ").strip()
    api_secret = getpass.getpass("Enter your Kraken API Secret (hidden): ").strip()

    if not api_key or not api_secret:
        print("Error: Both key and secret are required.")
        _print_posture(kr.provider_posture())
        return

    keyring.set_password("kraken", "kraken_api_key", api_key)
    keyring.set_password("kraken", "kraken_api_secret", api_secret)

    print()
    print("Keys stored in Windows Credential Manager.")
    print()

    ep = _ask_endpoint(kr.provider_posture()["endpoint"])

    if ep:
        # idempotent schema check so endpoint storage never fails on a
        # box the server has not booted on yet (CREATE TABLE IF NOT
        # EXISTS — the same DB the pool server uses)
        db.init_db()
        db.set_setting(ENDPOINT_KEY, ep)
        print(f"Endpoint stored in settings ({ENDPOINT_KEY}).")

    _print_posture(kr.provider_posture())
    _crosscheck_server()
    print()
    print("You can now run: python miner.py --kraken")


if __name__ == "__main__":
    main()
