"""timing_shadow.py — the trend-ride engine running LIVE on real bars, no orders.

What "live" means, precisely: real 5m Kraken candles, real entry and exit decisions
from overwatch_timing, every decision journalled to `contest_timing_shadow.jsonl`.
It never places, cancels or closes an order and never writes the balance. The shape
wins 82.3% and only survives the death line at 1% of the balance, so tickets are
priced by `overwatch_timing.ticket_usd()` (i.e. `contest_account.sized_entry_usd`)
and the shadow record is what earns the right to spend money.

The punish/reward loop:
  * every closed shadow trade updates the running record;
  * a losing streak (>= MAX_STREAK) or a rolling win rate under MIN_ROLL_WIN
    suspends new entries for SUSPEND_BARS bars, logged with its reason;
  * nothing un-suspends it by hand — the window has to recover on its own.

Run once:   python timing_shadow.py --once
Run live:   python timing_shadow.py            (every minute, new bars only)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import overwatch_timing as ot

STATE = os.path.join(HERE, "timing_shadow_state.json")
JOURNAL = os.path.join(HERE, "contest_timing_shadow.jsonl")
TAPE = os.path.join(HERE, "scratch", "kraken_5m_long_183d.json")
LOG = os.path.join(HERE, "timing_shadow.log")

ROLL_WINDOW = 50          # rolling trades for the scorecard
MIN_ROLL_WIN = 70.0       # below this, the engine suspends itself
MAX_STREAK = 8            # this many losers in a row also suspends
SUSPEND_BARS = 288        # 24h of 5m bars
BAR_SECONDS = 300         # one 5m bar


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"positions": {}, "closed": [], "streak": 0, "suspend_until": 0,
            "cycles": 0, "errors": 0}


def save_state(s: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, STATE)


def pairs(limit: int = 0) -> list[str]:
    try:
        with open(TAPE, encoding="utf-8") as fh:
            keys = list(json.load(fh)["pairs"])
    except (OSError, ValueError, KeyError):
        keys = ["BTC", "ETH", "SOL", "XRP", "PEPE"]
    return [f"{k}/USD" for k in keys[:limit or None]]


def get_exchange():
    import ccxt
    return ccxt.kraken({"enableRateLimit": True, "timeout": 30000})


def scorecard(s: dict, ctx: dict) -> dict:
    """The record, plus the lowest point the paper path reaches above the halt line."""
    closed = [c["net_pct"] for c in s["closed"]]
    if not closed:
        return {"trades": 0, "win_pct": 0.0, "mean_net_pct": 0.0,
                "min_headroom_usd": round(ctx["headroom"], 2), "rolling_n": 0}
    bal, low = ctx["balance"], ctx["balance"]
    for c in closed:
        bal += ot.ticket_usd(bal, ctx["start"]) * c["net_pct"] / 100.0
        low = min(low, bal)
    rolling = closed[-ROLL_WINDOW:]
    return {"trades": len(closed),
            "win_pct": round(100.0 * sum(1 for x in rolling if x > 0) / len(rolling), 1),
            "mean_net_pct": round(sum(rolling) / len(rolling), 4),
            "min_headroom_usd": round(low - ctx["halt_line"], 2),
            "paper_balance_usd": round(bal, 2),
            "rolling_n": len(rolling)}


def handle_bar(s: dict, pair: str, bars, now: float) -> None:
    """Manage an open shadow position, or look for an entry, on the last closed bar."""
    if len(bars) < ot.TREND_SPAN + 5:
        return
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
        if exit_px is None:
            return
        net = ot.net_pct(pos["entry"], exit_px)
        rec = {"ts": now, "pair": pair, "entry": pos["entry"], "exit": exit_px,
               "net_pct": round(net, 4), "bars": pos["bars"], "opened": pos["opened"],
               "reason": reason}
        s["closed"].append(rec)
        s["streak"] = s["streak"] + 1 if net <= 0 else 0
        del s["positions"][pair]
        with open(JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        log(f"CLOSE {pair} net {net:+.3f}% after {pos['bars']} bars ({reason})")
        return

    if now < s.get("suspend_until", 0):
        return
    ok, why = ot.entry_ok(c, h, lo, o)
    if not ok:
        return
    stop = ot.initial_stop(c[-1])
    s["positions"][pair] = {"entry": c[-1], "peak": h[-1], "stop": stop,
                            "bars": 0, "opened": now}
    log(f"OPEN  {pair} @ {c[-1]:.8f} stop {stop:.8f} ({why})")


def cycle(s: dict, ex, pair_list: list[str]) -> None:
    now = time.time()
    s["cycles"] = s.get("cycles", 0) + 1
    for pair in pair_list:
        try:
            bars = ex.fetch_ohlcv(pair, timeframe="5m", limit=320)
        except Exception as exc:                     # network, rate limit, delisting
            s["errors"] = s.get("errors", 0) + 1
            log(f"FETCH FAIL {pair}: {exc}")
            continue
        if not bars:
            continue
        try:
            handle_bar(s, pair, bars, now)
        except Exception:
            s["errors"] = s.get("errors", 0) + 1
            log(f"BAR FAIL {pair}: {traceback.format_exc().splitlines()[-1]}")

    ctx = ot.death_context()
    sc = scorecard(s, ctx)

    # punish / reward
    if sc["rolling_n"] >= 20 and sc["win_pct"] < MIN_ROLL_WIN \
            and now >= s.get("suspend_until", 0):
        s["suspend_until"] = now + SUSPEND_BARS * BAR_SECONDS
        log(f"SUSPEND: rolling win {sc['win_pct']}% < {MIN_ROLL_WIN}% "
            f"over {sc['rolling_n']} trades -- no entries for 24h")
    if s.get("streak", 0) >= MAX_STREAK:
        s["suspend_until"] = now + SUSPEND_BARS * BAR_SECONDS
        s["streak"] = 0
        log(f"SUSPEND: {MAX_STREAK} losers in a row -- no entries for 24h")

    arm, why = ot.should_arm(sc, ctx)
    log(f"cycle {s['cycles']}: open {len(s['positions'])} closed {sc['trades']} "
        f"win {sc['win_pct']}% net {sc['mean_net_pct']:+.4f}% "
        f"buffer ${ctx['buffer']:.2f} (${sc['min_headroom_usd']:.0f} above halt) "
        f"| armed={arm} ({why})")
    save_state(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--pairs", type=int, default=0, help="limit pairs, for a smoke test")
    a = ap.parse_args()

    log(f"timing_shadow starting (orders: NEVER; armed={ot.ARMED})")
    s, pair_list = load_state(), pairs(a.pairs)
    try:
        ex = get_exchange()
        if a.once:
            cycle(s, ex, pair_list)
            return 0
        while True:
            try:
                cycle(s, ex, pair_list)
            except Exception:
                s["errors"] = s.get("errors", 0) + 1
                log("CYCLE FAIL " + traceback.format_exc().splitlines()[-1])
                save_state(s)
            time.sleep(60)
    except KeyboardInterrupt:
        log("stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
