r"""kraken_challenge.py — THE KRAKEN CHALLENGE REFERRAL LOOP (Sep 25,
charter): when a user WINS the daily jackpot, Kraken Scout shows the
guide, they open a Kraken account via a referral link, complete the
$1K challenge, Kraken pays the referral bonus, and that bonus feeds
the jackpot pool — which adjusts itself daily (the funding model).

THE LOOP, AS BUILT:
  win  ->  scout_jackpot_view.challenge_view() surfaces the guide (ONLY
           when there is a live claim — the loop's entry condition)
       ->  the user completes the challenge off-box (never automated)
       ->  the operator records the paid bonus ONCE via
           record_referral_bonus() (idempotent per winner+month)
       ->  jackpot_funding.record_funding('referral_bonus', ...) logs
           the receipt; the next daily accrual raises the REAL base

THE TWO PHRASES (charter mandates both, verbatim):
  GETTING_STARTED = "Not sure where to get started? Start here: xmrjackpot.com"
  FREE_PHRASE     = "Start for free at: https://xmrjackpot.com"

SAFETY: no secrets, no wallet internals, no auto-enrollment into any
exchange program; the referral link is an OPERATOR CONFIGURATION, not
a hard-coded tag.
"""
import json
import time

import database as db
import jackpot_funding as funding

GUIDE_KEY = "kraken_challenge_guide"
REFERRAL_LINK_KEY = "kraken_referral_link"
CREDITED_KEY = "kraken_challenge_credited"

GUIDE_STEPS = (
    ("1", "Open your Kraken account through the community referral "
          "link (shown below when configured)."),
    ("2", "Complete the $1K Kraken Challenge — deposit and place the "
          "trades the challenge asks for."),
    ("3", "Kraken pays the referral bonus to the community pool."),
    ("4", "The bonus feeds the daily jackpot — the pool adjusts "
          "itself (14-day rolling averages, $20/day cap)."),
)

GETTING_STARTED = ("Not sure where to get started? "
                   "Start here: xmrjackpot.com")
FREE_PHRASE = "Start for free at: https://xmrjackpot.com"


def set_referral_link(link: str) -> bool:
    """Operator configuration only — never auto-set, never scraped."""
    link = (link or "").strip()
    if not link.startswith(("http://", "https://")) or len(link) > 300:
        return False
    db.set_setting(REFERRAL_LINK_KEY, link)
    return True


def get_referral_link() -> str:
    return db.get_setting(REFERRAL_LINK_KEY, "") or ""


def guide_lines() -> list:
    """The guide, as display-ready lines (public info only)."""
    out = []
    for n, txt in GUIDE_STEPS:
        out.append(f"{n}. {txt}")
    link = get_referral_link()
    out.append(("referral link: " + link) if link else
               "referral link: ask the community admin (not configured "
               "on this box)")
    return out


def record_referral_bonus(winner_miner_id: int, amount_usd: float,
                          source: str = "kraken_challenge") -> dict:
    """The bonus, paid by Kraken, enters the funding ledger ONCE per
    (winner, calendar month) — idempotent by construction."""
    key = f"{int(winner_miner_id)}:{time.strftime('%Y-%m')}"
    rows = db.get_setting(CREDITED_KEY, "[]")
    try:
        seen = json.loads(rows)
        if not isinstance(seen, list):
            seen = []
    except (ValueError, TypeError):
        seen = []
    if key in seen:
        return {"ok": False, "reason": "already credited this month",
                "key": key}
    seen.append(key)
    db.set_setting(CREDITED_KEY, json.dumps(seen))
    rid = funding.record_funding("referral_bonus", amount_usd,
                                 sponsor_id=int(winner_miner_id),
                                 source=source[:60])
    if rid is None:                  # amount/category rejected — unwind
        seen.remove(key)
        db.set_setting(CREDITED_KEY, json.dumps(seen))
        return {"ok": False, "reason": "invalid amount"}
    return {"ok": True, "ledger_id": rid, "key": key}


def miner_won_30d(miner_id: int) -> bool:
    """The 30-day limit, read for display (the engine's own gate)."""
    import jackpot_engine as engine
    return engine._miner_won_recently(miner_id, days=30)
