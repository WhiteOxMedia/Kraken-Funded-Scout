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
One reader, three tiers (Oct 26): the operator's pinned XMR_USD_RATE
wins outright; otherwise the LIVE Kraken ticker rate from xmr_price
(TTL-cached, so a page read never waits on the network); otherwise the
engine's long-standing 323 constant as the last resort. It used to be
the 323 constant full stop, which priced a 593-dollar XMR at 323 and
made every dollar figure on the site wrong by that factor. Every
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


def _positive(raw):
    """A float from a setting string when it is a real positive number,
    else None (the house "never trust a blank or a zero" helper)."""
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None
DEFAULT_XMR_USD = 323.0       # jackpot_engine's own approximate rate

# THE VRSC BRIDGE (Oct 28): users on the Verus download branch mine
# VerusCoin, not Monero — the relays that would feed Monero to the pool
# are inaccessible from their regions, which is WHY the branch exists.
# Verus-run share counts still earn entries, but Verus currency itself
# cannot fund a jackpot pool without a conversion leg, so it does: the
# rate it converts at is the VRSC answer to xmr_price (see vrsc_price),
# and the pinned rate below is ONLY its fallback tier — never a quote
# that can be trusted as fresh. It is deliberately not the engine's
# 323: proving one coin's fallback leaked into the other's reader is
# what this constant is for.
DEFAULT_VRSC_USD = 1.0


def vrsc_usd_rate():
    """The VRSC/USD rate for every Verus-side payout conversion.

    The single VRSC reader, structured exactly like xmr_usd_rate: the
    kill-switchable vrsc_price tier first (override -> last fetched ->
    fallback, never raising), then the pinned VRSC_USD_RATE key as a
    last resort. See vrsc_price for the caching contract.
    """
    try:
        import vrsc_price
        return float(vrsc_price.current(fallback=DEFAULT_VRSC_USD)["rate"])
    except Exception:
        pass
    try:
        return float(db.get_setting("VRSC_USD_RATE", str(DEFAULT_VRSC_USD)))
    except (TypeError, ValueError):
        return DEFAULT_VRSC_USD

# A Scout Pro donation is a different animal from the two formula
# categories: it is money already received, not an inflow to be
# projected into a 14-day average, so it never enters ReferralVelocity
# or MinerYield (the two functions filter by category explicitly) and
# the daily accrual can never double-count it. It lands in the base
# immediately through credit_donation() below.
DONATION_CATEGORY = "scout_pro_donation"

# The operator's split of a Scout Pro unlock: the jackpot gets 70%, the
# operator keeps 30% (Oct 26). One constant so the ledger, the copy and
# the tests cannot disagree about the rule.
JACKPOT_SHARE_PCT = 70

CATEGORIES = ("referral_bonus", "mining_yield", DONATION_CATEGORY)
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


def lifetime_accrued_xmr():
    """Every XMR the daily compute has ever put into the base.

    Read by the engine's source attribution (Oct 26) ONLY to bootstrap
    the admin card's split on a box that predates it — the accrual is
    already stamped row by row here, so the history does not have to be
    guessed. This is not a balance and nothing draws on it: claim and
    spend math stays in the engine.
    """
    total = 0.0
    for r in _load_ledger():
        if r.get("action") != ACTION_ACCRUE or r.get("status") != "accrued":
            continue
        try:
            total += float(r.get("amount_xmr") or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total, 12)


def xmr_usd_rate():
    """The XMR/USD rate every USD <-> XMR conversion in the pool uses.

    The single reader: the engine's hero pot, the funding formula, the
    Scout Pro price and the projection card all come through here, so
    they can never quote different dollars. See xmr_price for the
    caching contract; this function never raises and always returns a
    usable positive number.
    """
    try:
        import xmr_price
        return float(xmr_price.current(fallback=DEFAULT_XMR_USD)["rate"])
    except Exception:
        pass
    try:
        return float(db.get_setting(RATE_KEY, str(DEFAULT_XMR_USD)))
    except (TypeError, ValueError):
        return DEFAULT_XMR_USD# ── THE STALE-RATE GUARD (Oct 28) ────────────────────────────
# A same-coin VRSC payout converts REAL treasury money at
# (XMR/USD) / (VRSC/USD). Stale is harmless on a hero card and
# disgraceful on a payout: the claim is the one place in the pool where
# an old rate SILENTLY MISPRICES a prize. So the payout conversion
# checks both legs' snapshot ages: inside the warn window it still
# pays but the audit row records exactly how old the numbers were,
# and inside the block window it refuses — nothing is paid on a
# number nobody would sign for. Every refusal is retriable: the
# claim path returns before any db mutation, so refreshing the rate
# and re-claiming is the whole recovery, within the claim window.
VRSC_RATE_STALE_KEY = "VRSC_RATE_STALE_S"
VRSC_RATE_STALE_S = 900              # warn: a snapshot older than this
VRSC_RATE_BLOCK_MULT = 3             # block: older than warn x this


def _leg_freshness(mod, is_xmr=False, now=None):
    """One leg's (rate, age): age 0.0 = operator-vouched (a pin or the
    bridge's own db key), age None = no snapshot at all, so the caller
    would silently quote a DEFAULT constant."""
    try:
        if mod.override() is not None:
            return None, 0.0
        rate, ts, _src = mod.last_good()
        if ts:
            return float(rate), max(0.0, float(now) - float(ts))
        if is_xmr:
            # The bridge's db-pinned XMR rate key: when the operator
            # values is present and positive it vouches for the leg.
            v = _positive(db.get_setting(RATE_KEY, ""))
            if v is not None and v != DEFAULT_XMR_USD:
                return None, 0.0
    except Exception:
        pass
    return None, None


def vrsc_conversion_guard(warn_s=None, block_s=None):
    """Freshness gate for the same-coin conversion (Oct 28). NEVER raises.

    Returns {"ok", "reason", "warn", "xmr_age_s", "vrsc_age_s"}:
    ok False = DO NOT convert (the reason name both legs' ages);
    warn is a human sentence when a leg is old-but-payable, else None.

    Thresholds: warn at VRSC_RATE_STALE_S (operator-tunable through the
    db setting VRSC_RATE_STALE_KEY); block at warn x VRSC_RATE_BLOCK_MULT.
    """
    out = {"ok": True, "reason": None, "warn": None,
           "xmr_age_s": None, "vrsc_age_s": None}
    if warn_s is None:
        warn_s = _positive(str(db.get_setting(
            VRSC_RATE_STALE_KEY, str(VRSC_RATE_STALE_S))) or VRSC_RATE_STALE_S)
    if not warn_s or warn_s <= 0:
        warn_s = VRSC_RATE_STALE_S
    if block_s is None:
        block_s = warn_s * float(VRSC_RATE_BLOCK_MULT)
    now = time.time()

    try:
        import vrsc_price as _vp
        out["vrsc_age_s"] = _leg_freshness(_vp, now=now)[1]
    except Exception:
        out["vrsc_age_s"] = None
    try:
        import xmr_price as _xp
        out["xmr_age_s"] = _leg_freshness(_xp, is_xmr=True, now=now)[1]
    except Exception:
        out["xmr_age_s"] = None

    blocks, warns = [], []
    for name, age in (("VRSC", out["vrsc_age_s"]),
                      ("XMR", out["xmr_age_s"])):
        if age is None:
            blocks.append("%s rate has no snapshot — the conversion "
                          "would silently price at a default constant; "
                          "pull the rate or pin an operator override "
                          "before paying out" % name)
        elif age >= block_s:
            blocks.append("%s rate snapshot is %.0f s old (block threshold "
                          "%.0f s)" % (name, age, block_s))
        elif age >= warn_s:
            warns.append("%s rate snapshot is %.0f s old (warn threshold "
                         "%.0f s)" % (name, age, warn_s))

    if blocks:
        out["ok"] = False
        out["reason"] = "VRSC payout blocked by the stale-rate guard: " \
                         + "; ".join(blocks)
    elif warns:
        out["warn"] = "Stale-rate guard warning: " + "; ".join(warns) \
                       + " — the conversion proceeded on this snapshot"
    return out


SOURCE_LABELS = { "override": "operator-set", "kraken_ticker": "Kraken",
                 "coingecko": "CoinGecko",
                 "default": "fallback"}


def _age_text(seconds):
    """A short age for a visitor: '4 min ago', not an epoch."""
    s = max(0, int(seconds or 0))
    if s < 90:
        return "%d s ago" % s
    if s < 5400:
        return "%d min ago" % int(round(s / 60.0))
    if s < 172800:
        return "%d h ago" % int(round(s / 3600.0))
    return "%d d ago" % int(round(s / 86400.0))


def rate_info():
    """THE RATE AND ITS PROVENANCE, for every surface that quotes USD.

    The hero plate, the dashboard plate, the projection card and the
    admin pot card all convert at xmr_usd_rate(); this returns the same
    number WITH where it came from, how old it is and whether it is a
    fallback — so a visitor is told what the dollars were converted at
    instead of being handed a number of unknown vintage.

    Returns {"rate", "source", "as_of", "stale", "label"}. `label` is
    display-ready and built HERE, once: every surface says the same
    sentence, and a test can read it without a browser.
    """
    try:
        import xmr_price
        info = dict(xmr_price.current(fallback=DEFAULT_XMR_USD))
    except Exception:
        info = {"rate": DEFAULT_XMR_USD, "source": "default",
                "as_of": None, "stale": True}
    try:
        rate = float(info.get("rate") or DEFAULT_XMR_USD)
    except (TypeError, ValueError):
        rate = float(DEFAULT_XMR_USD)
    if rate <= 0:
        rate = float(DEFAULT_XMR_USD)
    source = str(info.get("source") or "default")
    as_of = info.get("as_of")
    try:
        as_of = float(as_of) if as_of else None
    except (TypeError, ValueError):
        as_of = None
    stale = bool(info.get("stale"))
    price = "$%.2f/XMR" % rate
    if source == "override":
        label = "converted at the operator-set " + price
    elif source == "default" or not as_of:
        # THE BAD CASE, SAID OUT LOUD: nothing was ever fetched (or the
        # cache chain broke) and this is the caller's constant.
        stale = True
        label = ("converted at the FALLBACK " + price
                 + " \u2014 no live rate on this box")
    else:
        venue = SOURCE_LABELS.get(source, source)
        if as_of:
            label = "converted at %s \u00b7 %s \u00b7 %s" % (
                price, venue, _age_text(time.time() - as_of))
        else:
            # a rate with no fetch time cannot be called fresh
            stale = True
            label = "converted at %s \u00b7 %s (fetch time unknown)" % (
                price, venue)
        if stale:
            label += " \u2014 stale, refresh overdue"
    return {"rate": round(rate, 8), "source": source, "as_of": as_of,
            "stale": stale, "label": label}


def record_funding(category, amount_usd, sponsor_id=None, source="",
                   amount_xmr=None, jackpot_xmr=None, treasury_xmr=None):
    """Record one real funding inflow in USD. Returns the row id, or
    None when the category/amount is invalid. This is a RECEIPT — the
    money enters the base only through accrue_daily_funding, except
    for a Scout Pro donation (credit_donation), whose XMR amount is
    already in hand and is stamped on the row that way."""
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
    row = {
        "id": rid,
        "category": category,               # see CATEGORIES
        "amount_usd": amount_usd,           # USD ONLY — the ledger's unit
        "sponsor_id": sponsor_id,           # opaque sponsor id (no addresses)
        "source": str(source or "")[:120],  # e.g. kraken challenge payout
        "ts": time.time(),
    }
    for extra, value in (("amount_xmr", amount_xmr),
                         ("jackpot_xmr", jackpot_xmr),
                         ("treasury_xmr", treasury_xmr)):
        if value is not None:
            try:
                row[extra] = round(float(value), 12)
            except (TypeError, ValueError):
                pass
    rows.append(row)
    _save_ledger(rows[-LEDGER_MAX_ROWS:])
    return rid


def _receipt_row(key):
    """The pre-Oct-26 protection: a receipt in the settings ledger."""
    for r in _load_ledger():
        if isinstance(r, dict) and r.get("source") == key:
            return r
    return None


def _already(credit, reason="already credited"):
    """The replay answer: the ORIGINAL split, and no money moved."""
    return {"credited": False, "reason": reason,
            "amount_xmr": float(credit.get("amount_xmr") or 0.0),
            "jackpot_xmr": float(credit.get("jackpot_xmr") or 0.0),
            "treasury_xmr": float(credit.get("treasury_xmr") or 0.0)}


def credit_donation(order_id, amount_xmr, sponsor_id=None, source=""):
    """A paid Scout Pro unlock is SPLIT 70/30 (Oct 26).

    The operator's rule: the jackpot gets 70% of the price, the operator
    keeps 30%. Raise the base by the jackpot's share NOW (the money
    already left the buyer's wallet; waiting for the 14-day formula
    would sit on it for a fortnight) and leave one ledger receipt that
    records the whole payment plus both halves, so the split is
    auditable per order.

    IDEMPOTENT PER ORDER, AND THE KEY IS A ROW (Oct 26): the check used
    to be a scan of this module's ledger, which is a RECEIPT BOOK capped
    at LEDGER_MAX_ROWS and rewritten as one JSON value — a replayed poll
    for an order whose receipt had aged out of that cap funded the pot a
    second time. Now one `donation_credits` row per order id IS the
    credit, and the INSERT plus the base raise run in ONE transaction:
    a concurrent poller loses the insert and moves nothing, and a crash
    between them rolls both back instead of leaving money with no key or
    a key with no money. The ledger receipt is still written afterwards
    as the audit trail — it is no longer what makes this safe.

    Returns {"credited": bool, "amount_xmr": float, "jackpot_xmr":
    float, "treasury_xmr": float, "base_xmr": float}.
    """
    try:
        amount_xmr = round(float(amount_xmr or 0), 12)
    except (TypeError, ValueError):
        return {"credited": False, "reason": "invalid amount", "amount_xmr": 0.0}
    if amount_xmr <= 0:
        return {"credited": False, "reason": "invalid amount", "amount_xmr": 0.0}

    jackpot_xmr = round(amount_xmr * JACKPOT_SHARE_PCT / 100.0, 12)
    treasury_xmr = round(amount_xmr - jackpot_xmr, 12)

    key = "scout_pro_donation:%s" % (order_id,)

    # 1. ALREADY KNOWN? then the answer is the original split and nothing
    #    moves. An order credited before this table existed is adopted on
    #    sight, so its protection stops being prunable.
    credit = db.get_donation_credit(order_id)
    if credit is None:
        legacy = _receipt_row(key)
        if legacy is not None:
            credit = db.adopt_donation_credit(
                order_id, legacy.get("amount_xmr") or amount_xmr,
                legacy.get("jackpot_xmr") or 0.0,
                legacy.get("treasury_xmr") or 0.0,
                sponsor_id=sponsor_id, source=key)
    if credit is not None:
        return _already(credit)

    # The rate first: xmr_price may reach the network, which must never
    # happen while a write transaction is open.
    rate = xmr_usd_rate()
    import jackpot_engine as engine                  # the one balance writer

    # 2. THE CLAIM AND THE MONEY, IN ONE TRANSACTION. The engine stays the
    #    only balance writer, exactly as the daily accrual does it — same
    #    hook, same rounding, just handed this transaction. Only the
    #    jackpot's 70% enters the pot; the treasury's 30% never touches
    #    the base. Tagged RECEIVED (Oct 26): this XMR arrived from a
    #    buyer, so the admin pot card must not call it formula accrual.
    with db.get_db() as conn:
        if not db.claim_donation_credit(
                conn, order_id, amount_xmr, jackpot_xmr, treasury_xmr,
                sponsor_id=sponsor_id, source=key):
            # a concurrent poller won the insert between step 1 and here
            return _already(db.get_donation_credit(order_id, conn=conn) or {})
        base = engine.add_funding_base(jackpot_xmr,
                                       source=engine.SRC_RECEIVED, conn=conn)
        db.set_donation_base(conn, order_id, base)

    # 3. THE RECEIPT (audit only): filed after the money is safe, so a
    #    ledger write can never be mistaken for the credit itself.
    rid = None
    try:
        rid = record_funding(
            DONATION_CATEGORY, amount_xmr * rate, sponsor_id=sponsor_id,
            source=key, amount_xmr=amount_xmr,
            jackpot_xmr=jackpot_xmr, treasury_xmr=treasury_xmr,
        )
    except Exception as e:          # the credit stands; the receipt retries
        print(f"[Funding] donation {key} credited; receipt not filed: {e}")
    return {"credited": True, "amount_xmr": amount_xmr,
            "jackpot_xmr": jackpot_xmr, "treasury_xmr": treasury_xmr,
            "amount_usd": round(amount_xmr * rate, 2),
            "jackpot_usd": round(jackpot_xmr * rate, 2),
            "treasury_usd": round(treasury_xmr * rate, 2),
            "split_pct": JACKPOT_SHARE_PCT,
            "receipt_id": rid, "base_xmr": base}


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
        try:            # donations are receipts, not referral velocity
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
