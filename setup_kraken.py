"""
One-time setup script to store Kraken API credentials in Windows
Credential Manager and provision the referral-projection endpoint.
Run: python setup_kraken.py
"""
import getpass
import keyring

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
    print()
    print("You can now run: python miner.py --kraken")


if __name__ == "__main__":
    main()
