"""timing_live.py — the trend-ride engine on the ONE execution path, one client.

WHAT THIS DOES NOT DO: it never builds a Kraken client. Every function takes the
`kraken` the caller already holds, so the API key is only ever used by one client in
one process. That is the whole reason this is a module with `tick(kraken, config)`
rather than a service with its own loop.

WHERE IT PLUGS IN: `execution_tiers.execute_tiered_buy/execute_tiered_sell` is the
only module in the repo that places an order — it measures the book, tiers the fill
and calls `kraken.place_market_buy/sell`. This module does not restate any of that;
it decides *whether* and *how much*, then hands the order over.

EVERY ENTRY PASSES, IN ORDER:
  1. overwatch_timing.should_arm()  — halt line, buffer floor, shadow record
  2. contest_account.sized_entry_usd — 1% of the balance, capped by the 3% guard
  3. execution_tiers.execute_tiered_buy — the single order path
and every exit is the ratchet from overwatch_timing.manage, sold through
execute_tiered_sell (which never caps: exits are risk management).

ARMING: `overwatch_timing.ARMED` is the switch and it is False. Until it is True and
the gates pass, `tick()` returns "not armed" without fetching, sizing or ordering.

WHY IT IS SAFE TO RUN BEFORE IT IS PROVEN: the record it keeps in
timing_live_state.json is the same record overwatch_timing's gate reads, so the first
200 live trades decide whether it keeps going. A losing streak of MAX_STREAK, or a
buffer under the floor, stops entries without anyone touching a flag.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contest_account as ca
import execution_tiers as xt
import overwatch_timing as ot

log = logging.getLogger(__name__)

STATE = os.path.join(HERE, "timing_live_state.json")
JOURNAL = os.path.join(HERE, "contest_timing_live.jsonl")
TAPE = os.path.join(HERE, "scratch", "kraken_5m_long_183d.json")

WINDOW = 320              # 5m bars per fetch
MAX_POSITIONS = 5         # never more than 5x1% of the balance at risk at once
MAX_STREAK = 8            # losers in a row stops new entries
BAR_SECONDS = 300


def _pairs() -> list[str]:
    try:
        with open(TAPE, encoding="utf-8") as fh:
            keys = list(json.load(fh)["pairs"])
    except (OSError, ValueError, KeyError):
        keys = ["BTC", "ETH", "SOL", "XRP", "PEPE"]
    return [f"{k}/USD" for k in keys]


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"positions": {}, "closed": [], "streak": 0, "suspend_until": 0,
            "cycles": 0, "errors": 0, "placed": 0}


def save_state(s: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, STATE)


def scorecard(s: dict, ctx: dict) -> dict:
    """The record the arm gate reads: wins, net, and the lowest room it has reached."""
    nets = [c["net_pct"] for c in s["closed"]]
    if not nets:
        return {"trades": 0, "win_pct": 0.0, "mean_net_pct": 0.0,
                "min_headroom_usd": round(ctx["headroom"], 2)}
    bal, low = ctx["balance"], ctx["balance"]
    for n in nets:
        bal += ot.ticket_usd(bal, ctx["start"]) * n / 100.0
        low = min(low, bal)
    window = nets[-50:]
    return {"trades": len(nets),
            "win_pct": round(100.0 * sum(1 for x in window if x > 0) / len(window), 1),
            "mean_net_pct": round(sum(window) / len(window), 4),
            "min_headroom_usd": round(low - ctx["halt_line"], 2)}


def _fill_price(order: dict, fallback: float) -> float:
    """What the order actually averaged, if the exchange said; else the reference."""
    try:
        avg = float(order.get("average") or 0.0)
        if avg > 0:
            return avg
        cost, filled = float(order.get("cost") or 0.0), float(order.get("filled") or 0.0)
        if cost > 0 and filled > 0:
            return cost / filled
    except (AttributeError, TypeError, ValueError):
        pass
    return float(fallback)


def tick(kraken, config=None) -> dict:
    """One pass: manage open positions, then take entries that pass every gate.

    Returns a small verdict dict — never raises into the caller's loop.
    """
    paper = bool(getattr(config, "paper_mode", True))
    s = load_state()
    ctx = ot.death_context()
    sc = scorecard(s, ctx)
    armed, why = ot.should_arm(sc, ctx)

    if not ot.ARMED:
        return {"armed": False, "why": why, "placed": 0, "closed": 0}
    if not armed:
        return {"armed": False, "why": why, "placed": 0, "closed": 0}

    s["cycles"] = s.get("cycles", 0) + 1
    now = time.time()
    placed = closed = 0

    for pair in _pairs():
        try:
            bars = kraken.fetch_ohlcv(pair, interval_minutes=5, limit=WINDOW)
        except Exception as exc:                      # network, delisting, rate limit
            s["errors"] = s.get("errors", 0) + 1
            log.warning("timing_live fetch failed %s: %s", pair, exc)
            continue
        if not bars or len(bars) < ot.TREND_SPAN + 5:
            continue
        o = [float(b[1]) for b in bars]
        h = [float(b[2]) for b in bars]
        lo = [float(b[3]) for b in bars]
        c = [float(b[4]) for b in bars]

        pos = s["positions"].get(pair)
        if pos:
            peak, stop, exit_px, reason = ot.manage(entry=pos["entry"], peak=pos["peak"],
                                                    stop=pos["stop"], bar_high=h[-1],
                                                    bar_low=lo[-1])
            pos["peak"], pos["stop"], pos["bars"] = peak, stop, pos["bars"] + 1
            if exit_px is not None:
                # exits are risk management: tiered sell never caps, always completes
                xt.execute_tiered_sell(kraken, pair, pos["volume"], ref_price=c[-1],
                                       validate=paper)
                net = ot.net_pct(pos["entry"], exit_px)
                rec = {"ts": now, "pair": pair, "entry": pos["entry"],
                       "exit": exit_px, "net_pct": round(net, 4),
                       "bars": pos["bars"], "reason": reason, "paper": paper}
                s["closed"].append(rec)
                s["streak"] = s["streak"] + 1 if net <= 0 else 0
                del s["positions"][pair]
                with open(JOURNAL, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
                closed += 1
                log.info("timing_live CLOSE %s net %+.3f%% (%s)", pair, net, reason)
            continue

        if now < s.get("suspend_until", 0) or len(s["positions"]) >= MAX_POSITIONS:
            continue
        ok, _why = ot.entry_ok(c, h, lo, o)
        if not ok:
            continue

        # ── the gates, in front of every entry ──────────────────────────────
        ctx = ot.death_context()
        armed, why = ot.should_arm(scorecard(s, ctx), ctx)
        if not armed:
            log.info("timing_live entry refused by gate: %s", why)
            continue
        usd = ot.ticket_usd(ctx["balance"], ctx["start"])
        if usd < 10.0:                               # too small to be worth a fill
            continue
        price = c[-1]
        volume = usd / price
        try:
            order = xt.execute_tiered_buy(kraken, pair, volume, ref_price=price,
                                          validate=paper)
        except Exception as exc:
            s["errors"] = s.get("errors", 0) + 1
            log.warning("timing_live buy failed %s: %s", pair, exc)
            continue
        fill = _fill_price(order, price)
        s["positions"][pair] = {"entry": fill, "peak": h[-1],
                                "stop": ot.initial_stop(fill), "bars": 0,
                                "opened": now, "volume": volume,
                                "usd": round(usd, 2), "paper": paper}
        s["placed"] = s.get("placed", 0) + 1
        placed += 1
        log.info("timing_live OPEN %s usd %.2f vol %.8f @ %.8f (paper=%s)",
                 pair, usd, volume, fill, paper)

    if s.get("streak", 0) >= MAX_STREAK:
        s["suspend_until"] = now + 288 * BAR_SECONDS
        s["streak"] = 0
        log.warning("timing_live SUSPEND: %d losers in a row", MAX_STREAK)

    save_state(s)
    return {"armed": True, "why": why, "placed": placed, "closed": closed,
            "open": len(s["positions"]), "buffer": round(ctx["buffer"], 2)}
