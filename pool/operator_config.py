"""pool/operator_config.py — OPERATOR-CONFIGURABLE VALUES, IN ONE PLACE (Oct 29).

Five knobs the operator sits behind, five chains that used to read the
same env keys and settings in triplicate (server, generated installers,
the engine, the checkout, the build scripts — each its own copy of the
resolution order, kept in lockstep by review alone):

    vrsc_stratum()          the Verus relay lane        (env)
    vrsc_territories()      who receives the VRSC branch (db > env > built-in)
    pro_price()             the Scout Pro price         (setting, XMR derived)
    house_wallet()          the pool's XMR wallet       (setting, env-seeded)
    kraken_deposit_address() the house-tax payout target (setting, env fallback)
    miner_wallet()          the address the local miner mines for (env)
    xmrig_pin()             the xmrig release pin       (this module's constants)

THE ONE READER RULE: each resolution order lives in exactly one function
here, and `test_operator_config_contract.py` fails as soon as any other
file reads those env keys or settings directly (or restates the pin).

Import shape: this module is STDLIB-ONLY at module level — `import
operator_config` must stay cheap enough for miner.py and the contract
test to load it without the pool's FastAPI stack. The database and the
rate reader are imported lazily, inside the functions that need them,
and always as Flat imports under the pool directory (the same style
pool/server.py already uses: `import database`, `import xmr_price`).

Defaults: an unset knob must mean an EMPTY value the caller can refuse
loudly (payout refuses with no Kraken address; no territories means the
built-in US,CA; no relay lane means the public surface advertises no
Verus entries), never a silent value somebody else chose.
"""
from __future__ import annotations

import os

# ── 1. THE VRSC RELAY LANE ──────────────────────────────────────────────
# XMRJ_VRSC_STRATUM set (non-blank) means a relay lane exists and Verus
# hashrate can reach this pool's ledger (jackpot entries are real);
# unset/blank means no lane, and the public surface must never promise
# entries for Verus hashrate. Until the relay names itself, the branch
# mines to the public LuckPool endpoint (same semantics as the Monero
# branch pointing at the pool's own relay: an upstream, an entry unit,
# receipts) — decided by the caller with the truthiness of this.
VRSC_STRATUM_ENV = "XMRJ_VRSC_STRATUM"
VRSC_STRATUM_DEFAULT = "na.luckpool.net:3956"


def vrsc_stratum():
    """THE ONE READER of the relay lane (moved here from server.py's
    vrsc_relay_up of Oct 29 — same contract, one home now).

    Returns the cleaned stratum string ("" when no lane), so callers
    that need the ADDRESS use it verbatim and its truthiness is the
    flag /api/stats publishes.
    """
    return os.environ.get(VRSC_STRATUM_ENV, "").strip()


# ── 2. WHO RECEIVES THE VERUS BRANCH ────────────────────────────────────
# The branch decision (download route), the generated installers, the
# admin read model and the tests all resolve through vrsc_territories(),
# so the order of authority lives in exactly one place and cannot drift:
#
#     db VRSC_TERRITORIES  >  XMRJ_VRSC_TERRITORIES env  >  built-in US,CA
VRSC_TERRITORIES_SETTING = "VRSC_TERRITORIES"
VRSC_TERRITORIES_ENV = "XMRJ_VRSC_TERRITORIES"
VRSC_TERRITORIES_DEFAULT = "US,CA"


def vrsc_territories():
    """THE ONE READER of the territory chain (moved here from server.py).

    Returns (source, raw): ``source`` is which tier won ("db", "env" or
    "default" — the admin card shows it so the operator knows which knob
    holds the answer), ``raw`` is the winning comma string stripped; the
    set view below splits it into the codes the callers resolve
    per-country.
    """
    try:
        import database as db
        raw_db = db.get_setting(VRSC_TERRITORIES_SETTING)
    except Exception:
        raw_db = None
    if isinstance(raw_db, str) and raw_db.strip():
        return "db", raw_db.strip()
    raw_env = os.environ.get(VRSC_TERRITORIES_ENV, "").strip()
    if raw_env:
        return "env", raw_env
    return "default", VRSC_TERRITORIES_DEFAULT


def vrsc_territories_db_value():
    """The DB tier RAW, for the one write path that diffs against it (the
    admin territory card reads the stored value to show the operator
    what a save will change). Not part of the resolution — callers solve
    WHO RECEIVES through vrsc_territories, never through this."""
    try:
        import database as db
        return db.get_setting(VRSC_TERRITORIES_SETTING, "") or ""
    except Exception:
        return ""


def vrsc_territory_set():
    """The set view for the callers that resolve per-country — the
    resolver above stays the single reader; this wrapper only strips the
    bookkeeping."""
    _src, raw = vrsc_territories()
    return {cc.strip().upper() for cc in raw.split(",") if cc.strip()}


# ── 3. THE SCOUT PRO PRICE ──────────────────────────────────────────────
# The product is sold at a DOLLAR price (the operator set it at $95;
# the old copy advertised 0.01 XMR, about $3), so USD is the stored
# source and the XMR amount is DERIVED at the tracked rate each time a
# quote is rendered or an order is opened. One reader for the homepage,
# the dashboard, the download page and the checkout, so none of them can
# quote a different sum than the one the wallet is asked for.
SCOUT_PRO_PRICE_SETTING = "scout_pro_price_usd"
SCOUT_PRO_DEFAULT_PRICE_USD = 95.0


def pro_price():
    """THE ONE READER of the Pro price chain (merges server._pro_price and
    scout_endpoints.scout_pro_price, both Oct 26).

    Returns (usd, xmr). A stored price of <= 0 falls back to the default
    rather than charging zero. The rate comes through
    jackpot_funding.xmr_usd_rate() — the pool's single never-raises rate
    reader (the live rate or the operator's pin, never the old 323
    constant as a *fetched* figure; its documented default is the
    floor).
    """
    try:
        import database as db
        usd = float(db.get_setting(SCOUT_PRO_PRICE_SETTING,
                                   str(SCOUT_PRO_DEFAULT_PRICE_USD)))
    except (ImportError, TypeError, ValueError):
        usd = SCOUT_PRO_DEFAULT_PRICE_USD
    if usd <= 0:
        usd = SCOUT_PRO_DEFAULT_PRICE_USD
    rate = 0.0
    try:
        import jackpot_funding
        rate = float(jackpot_funding.xmr_usd_rate())
    except Exception:
        pass
    xmr_amt = round(usd / rate, 8) if rate > 0 else 0.0
    return round(usd, 2), xmr_amt


# The two product knobs beside the price: the grant length (days) and
# how long a checkout window stays open (minutes). The same ONE-READER
# rule as the price: the settings live here, and the consumers
# (scout_endpoints' grant + checkout open, server.py's download-page
# offer) resolve through these functions, so no surface can quote a
# different duration than the one a paid-for unlock actually grants.
SCOUT_PRO_DAYS_SETTING = "scout_pro_days"
SCOUT_PRO_DEFAULT_DAYS = 30
SCOUT_PRO_CHECKOUT_MINUTES_SETTING = "scout_pro_checkout_minutes"
SCOUT_PRO_DEFAULT_CHECKOUT_MIN = 30


def pro_days():
    """THE ONE READER of the Pro grant-days chain - how long a paid
    Scout Pro unlock lasts, and the "N days" figure the download page
    quotes beside the price.

    An unset or unreadable setting falls back to the default; a stored
    value is floored at 1 day rather than granting zero.
    """
    try:
        import database as db
        v = int(float(db.get_setting(SCOUT_PRO_DAYS_SETTING,
                                     str(SCOUT_PRO_DEFAULT_DAYS))))
    except (ImportError, TypeError, ValueError):
        v = SCOUT_PRO_DEFAULT_DAYS
    return max(1, v)


def pro_checkout_minutes():
    """THE ONE READER of the Pro checkout-TTL chain - how many minutes a
    freshly opened checkout's Monero subaddress stays valid (the expiry
    db.create_scout_purchase receives). A closed window makes the wallet
    quote stale mid-checkout, so the checkout's own 5-minute floor is
    applied HERE and every caller of an expiry sees the same clamp; an
    unreadable setting falls back to the default 30.
    """
    try:
        import database as db
        v = int(float(db.get_setting(SCOUT_PRO_CHECKOUT_MINUTES_SETTING,
                                     str(SCOUT_PRO_DEFAULT_CHECKOUT_MIN))))
    except (ImportError, TypeError, ValueError):
        v = SCOUT_PRO_DEFAULT_CHECKOUT_MIN
    return max(5, v)


# ── 4. THE WALLETS ──────────────────────────────────────────────────────
# Operator addresses COME FROM THE DEPLOYMENT (Oct 25): an address is an
# operator identifier and must not be hard-coded in a repo that can be
# cloned or packaged. The pool's two receive addresses live as settings
# (seeded from env at database init, a WRITE this module allows), and
# the local miner's own address arrives by env at runtime.
HOUSE_WALLET_SETTING = "house_wallet"
HOUSE_WALLET_ENV = "XMRJ_HOUSE_WALLET"
KRAKEN_DEPOSIT_SETTING = "kraken_deposit_address"
KRAKEN_DEPOSIT_ENV = "XMRJ_KRAKEN_DEPOSIT_ADDRESS"
MINER_WALLET_ENV = "XMRJ_MINER_WALLET"


def house_wallet():
    """THE ONE READER of the pool's own wallet address. Empty means
    unset — payout refuses loudly rather than a silent default."""
    try:
        import database as db
        return (db.get_setting(HOUSE_WALLET_SETTING, "") or "").strip()
    except Exception:
        return ""


def kraken_deposit_address():
    """THE ONE READER of the house-tax payout target. Resolution:
    the stored setting WINS, the deployment env (XMRJ_KRAKEN_
    DEPOSIT_ADDRESS) is the same-boot fallback the engine used to read
    inline, and empty means unset (payout refuses loudly)."""
    try:
        import database as db
        addr = (db.get_setting(KRAKEN_DEPOSIT_SETTING, "") or "").strip()
        if addr:
            return addr
    except Exception:
        addr = ""
    return os.environ.get(KRAKEN_DEPOSIT_ENV, "").strip()


def miner_wallet():
    """THE ONE READER of the local miner's own receive address (miner.py,
    Oct 25): unset means the miner has nothing to mine for and says so."""
    return (os.environ.get(MINER_WALLET_ENV) or "").strip()


# ── 5. THE XMRIG PIN ────────────────────────────────────────────────────
# THE RELEASE ASSET THAT EXISTS. The URL used to point at
# xmrig-6.26.0-msvc-win64.zip, which GitHub answers with 404, so the
# fallback could only ever fail after the user opted in. The SHA256 is
# the exact asset named above, taken by downloading that URL and hashing
# it on Oct 24 2026 (2,720,812 bytes) — not inherited from the 404 asset.
# The PowerShell installer chain keeps the SAME literals (installer/
# install.ps1 — it must stay standalone in the shipped bundle, no Python
# at read time); the contract test parses both and fails if they drift.
# The BUILD scripts (build_bundle.ps1, generate_release_manifest.py,
# check_release_hygiene.py) stage the LAYOUT under this version and read
# the release manifest — they are the layout chain, not re-pinners.
XMRIG_VERSION = "6.26.0"
XMRIG_URL = ("https://github.com/xmrig/xmrig/releases/download/"
             f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-windows-x64.zip")
XMRIG_SHA256 = ("bba8097cb37d9b458a1cb1137876b27cde6740d17fe4ccbc"
                "086ba07d87d9e147")
XMRIG_LAYOUT = ("xmrig", XMRIG_VERSION)   # <install root>/xmrig/xmrig-<v>


def xmrig_pin():
    """THE ONE READER of the pin for every Python chain (server.py's
    generated installer and miner.py's layout/banner; the PowerShell
    chain stays literal, bound to this by the contract test).
    Returns a dict snapshot so a consumer can never mutate the module
    constants by accident."""
    return {
        "version": XMRIG_VERSION,
        "url": XMRIG_URL,
        "sha256": XMRIG_SHA256,
        "layout_dir": XMRIG_LAYOUT[0] + "\\" + XMRIG_LAYOUT[1],
    }
