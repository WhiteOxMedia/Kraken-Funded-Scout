"""jackpot_funding.py — THE SELF-ADJUSTING FUNDING MODEL (Sep 25).

THE CHARTER'S FORMULA, byte for byte:

    DailyJackpot = min(20 USD, ReferralVelocity + MinerYield)
    ReferralVelocity = rolling 14-day average of referral bonuses paid
    MinerYield = rolling 14-day average of XMR mined by active Miner Bot users

WHAT THIS MODULE OWNS
---------------------
The funding LEDGER (jackpot_funding table) and the amount COMPUTE.
The engine calls compute_daily_jackpot() at draw time and
accrue_daily_funding() from its own daily tick; this module never
touches drawings, claims, or the earmark model — one brain per layer.

WHERE THE MONEY ACTUALLY IS (no phantom funds, no double-count)
---------------------------------------------------------------
- Funding events are recorded in USD ONLY (the ledger's unit). XMR
  amounts are derived at compute time from the tracked rate.
- The DAILY COMPUTE (accrue_daily_funding, once per day) is the ONE
  place funding turns into base XMR: db.add_funding_base(amount_xmr)
  raises the real base by the amount the formula names, and the
  accrual is stamped 'accrued' so it can never land twice. A ledger
  row is a RECEIPT, not money.
- Claims/expiry keep working untouched: a claim subtracts the paid
  payout from the base (real funds leaving), an expiry releases the
  earmark only. Funding raises the base with real backing by
  construction — the referral bonus and mined XMR are real inflows.

THE RATE
--------
XMR_USD is a setting (XMR_USD_RATE) the server may refresh; the
default is the engine's own long-standing constant (323). Every
derived amount carries the rate and mode it was converted at.

PRIVACY
-------
The ledger stores the bonus CATEGORY and amount, never addresses,
keys, or miner identities beyond an opaque sponsor id.
"""
import json
import time

import database as db

DAILY_CAP_USD = 20.0          # the charter's ceiling, byte for byte
WINDOW_DAYS = 14              # the charter's rolling window
LEDGER_MAX_ROWS = 2000        # the receipt book is bounded (review
                              # triage, Sep 25): the 14-day window only
                              # ever reads ~30 days of rows, so the cap
                              # keeps every compute O(1)-ish without
                              # losing a day of history
LEDGER_KEY = "jackpot_funding_ledger"
RATE_KEY = "XMR_USD_RATE"
DEFAULT_XMR_USD = 323.0       # jackpot_engine's own approximate rate

CATEGORIES = ("referral_bonus", "mining_yield")
ACTION_ACCRUE = "funding_accrual"


# ── the ledger (settings-backed — no schema owner on this box) ────────
def _load_ledger():
    raw = db.get_setting(LEDGER_KEY, "[]")
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, list) else []
    except (ValueError, TypeError):
        return []


def _save_ledger(rows):
    db.set_setting(LEDGER_KEY, json.dumps(rows, separators=(",", ":")))


def xmr_usd_rate():
    try:
        return float(db.get_setting(RATE_KEY, str(DEFAULT_XMR_USD)))
    except (TypeError, ValueError):
        return DEFAULT_XMR_USD


def record_funding(category, amount_usd, sponsor_id=None, source=""):
    """Record one real funding inflow in USD. Returns the row id, or
    None when the category/amount is invalid. This is a RECEIPT — the
    money enters the base only through accrue_daily_funding."""
    if category not in CATEGORIES:
        return None
    try:
        amount_usd = round(float(amount_usd), 2)
    except (TypeError, ValueError):
        return None
    if amount_usd <= 0:
        return None
    rows = _load_ledger()
    rid = 1 + max((int(r.get("id", 0)) for r in rows), default=0)
    rows.append({
        "id": rid,
        "category": category,               # 'referral_bonus' | 'mining_yield'
        "amount_usd": amount_usd,           # USD ONLY — the ledger's unit
        "sponsor_id": sponsor_id,           # opaque sponsor id (no addresses)
        "source": str(source or "")[:120],  # e.g. kraken challenge payout
        "ts": time.time(),
    })
    _save_ledger(rows[-LEDGER_MAX_ROWS:])
    return rid


def _window_rows(now_ts=None):
    now_ts = now_ts if now_ts is not None else time.time()
    cutoff = now_ts - WINDOW_DAYS * 86400
    return [r for r in _load_ledger() if float(r.get("ts", 0)) > cutoff]


def referral_rewards_last24h(now_ts=None):
    """The pool's honest 'claims' figure for the site's referral block:
    how many referral bonuses were RECORDED into the funding ledger in
    the trailing 24h, plus their USD sum. Read-only over the existing
    ledger — no Kraken scraping, no estimate posing as a fact. The
    browser block renders this count as 'rewards claimed today'.
    Malformed rows are SKIPPED, not fatal (CodeRabbit minor on 8ffbfde):
    a missing/None amount_usd, a non-numeric ts, or a non-dict row must
    not abort the whole figure — one bad row zeroing the display for
    every visitor until it left the ledger is the failure mode we do
    not ship. Valid rows keep counting exactly as before."""
    now_ts = now_ts if now_ts is not None else time.time()
    cutoff = now_ts - 86400.0
    rows = []
    total = 0.0
    for r in _load_ledger():
        try:
            if (r.get("category") != "referral_bonus"
                    or not float(r.get("ts", 0) or 0) > cutoff):
                continue
            amt = float(r["amount_usd"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        rows.append(r)
        total += amt
    return {
        "count": len(rows),
        "total_usd": round(total, 2),
    }


def referral_velocity_usd(now_ts=None):
    """Rolling 14-day average of referral bonuses paid (per day)."""
    rows = _window_rows(now_ts)
    total = sum(float(r["amount_usd"]) for r in rows
                if r.get("category") == "referral_bonus")
    return total / WINDOW_DAYS


def miner_yield_usd(now_ts=None):
    """Rolling 14-day average of XMR mined by active Miner Bot users,
    valued at the tracked rate (per day)."""
    rows = _window_rows(now_ts)
    total_xmr = sum(float(r["amount_usd"]) / xmr_usd_rate() for r in rows
                    if r.get("category") == "mining_yield")
    return (total_xmr * xmr_usd_rate()) / WINDOW_DAYS


def compute_daily_jackpot(now_ts=None):
    """THE CHARTER'S FORMULA. Returns the full decision record: the
    components, the cap, the chosen amount in USD and in XMR at the
    tracked rate. No IO beyond reads — safe to call for display."""
    now_ts = now_ts if now_ts is not None else time.time()
    ref = referral_velocity_usd(now_ts)
    mng = miner_yield_usd(now_ts)
    raw = ref + mng
    daily_usd = min(DAILY_CAP_USD, raw)
    capped = raw > DAILY_CAP_USD
    return {
        "referral_velocity_usd": round(ref, 4),
        "miner_yield_usd": round(mng, 4),
        "raw_usd": round(raw, 4),
        "daily_jackpot_usd": round(daily_usd, 2),
        "daily_jackpot_xmr": round(daily_usd / xmr_usd_rate(), 8),
        "capped": capped,
        "cap_usd": DAILY_CAP_USD,
        "xmr_usd": xmr_usd_rate(),
    }


def accrue_daily_funding(now_ts=None, engine=None):
    """The DAILY COMPUTE — the one place funding becomes base XMR.

    Idempotent per calendar day: a day with a completed accrual is
    skipped (returns accrued=False), a partial-day re-run completes
    the day's accrual once. Lands as REAL base XMR via the db layer's
    funding-base hook (add_funding_base; shimmed by the engine here so
    the engine module stays the only balance writer)."""
    now_ts = now_ts if now_ts is not None else time.time()
    rows = _load_ledger()
    today = time.strftime("%Y-%m-%d", time.localtime(now_ts))
    for r in reversed(rows):                     # newest first
        if (r.get("action") == ACTION_ACCRUE
                and str(r.get("day", "")).startswith(today)
                and r.get("status") == "accrued"):
            return {"accrued": False, "reason": "already accrued today",
                    "day": today}

    plan = compute_daily_jackpot(now_ts)
    amount_xmr = plan["daily_jackpot_xmr"]
    rows.append({
        "id": 1 + max((int(r.get("id", 0)) for r in rows), default=0),
        "action": ACTION_ACCRUE,
        "day": today,
        "status": "accrued",
        "amount_xmr": amount_xmr,
        "plan": plan,
        "ts": time.time(),
    })
    _save_ledger(rows)
    if engine is None:
        import jackpot_engine as engine          # the one balance writer
    engine.add_funding_base(amount_xmr)
    return {"accrued": True, "day": today,
            "amount_xmr": amount_xmr, "plan": plan}
