"""timing_shadow.py — the trend-ride engine running LIVE on real bars, no orders.

What "live" means here, precisely: it fetches real 5m Kraken candles, makes real
entry and exit decisions with overwatch_timing's rules, and writes every decision to
`contest_timing_shadow.jsonl`. It never places, cancels or closes an order and never
touches the balance. That is deliberate — see overwatch_timing.STUDY: the shape wins
82.3% of the time and only survives the -3% death line at 1% of the balance, so it
prices every ticket with `overwatch_timing.ticket_usd()` (which is
`contest_account.sized_entry_usd`) and it still has to prove itself on live data.

The scorecard is the punish/reward loop:
  * every closed shadow trade updates the running record;
  * a losing trade closes the window and the engine records it as punishment;
  * a losing *streak* (>=8) or a rolling win rate below 70% suspends new entries for
    SUSPEND_BARS bars, and the suspension is logged with its reason;
  * the suspension lifts only when the rolling window recovers, so the engine polices
    itself instead of being hand-tuned after a bad day.

Run once:   python timing_shadow.py --once
Run live:   python timing_shadow.py            (every 5m close, until stopped)
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
NOTIONAL = 2000.0         # paper size, only to express drawdown in dollars


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
            "last_bar_ts": 0, "cycles": 0, "errors": 0}


def save_state(s: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, STATE)


def pairs():
    try:
        with open(TAPE, encoding="utf-8") as fh:
            keys = list(json.load(fh)["pairs"])
        return [f"{k}/USD" for k in keys]
    except (OSError, ValueError, KeyError):
        return ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "PEPE/USD"]


def get_exchange():
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True, "timeout": 30000})
    return ex


def fetch(ex, pair, limit=320):
    return ex.fetch_ohlcv(pair, timeframe="5m", limit=limit)


def scorecard(s: dict) -> dict:
    closed = [c["net_pct"] for c in s["closed"][-ROLL_WINDOW:]]
    ctx = ot.death_context()
    if not closed:
        return {"trades": len(s["closed"]), "win_pct": 0.0, "mean_net_pct": 0.0,
                "min_headroom_usd": round(ctx["headroom"], 2), "rolling_n": 0}
    wins = sum(1 for x in closed if x > 0)
    # the room is the balance above the HALT line, walking the paper path from the
    # live balance with the same 1% ticket: the lowest it ever gets is the real risk
    bal = ctx["balance"]
    low = bal
    for c in s["closed"]:
        bal += ot.ticket_usd(bal, ctx["start"]) * c["net_pct"] / 100.0
        low = min(low, bal)
    return {
        "trades": len(s["closed"]),
        "win_pct": round(100.0 * wins / len(closed), 1),
        "mean_net_pct": round(sum(closed) / len(closed), 4),
        "min_headroom_usd": round(low - ctx["halt_line"], 2),
        "paper_balance_usd": round(bal, 2),
        "rolling_n": len(closed),
    }


def handle_bar(s: dict, pair: str, bars, now_ts: float) -> None:
    """Manage an open shadow position, or look for an entry, on the last closed bar."""
    if len(bars) < ot.TREND_SPAN + 5:
        return
    o = [float(b[1]) for b in bars]
    h = [float(b[2]) for b in bars]
    lo = [float(b[3]) for b in bars]
    c = [float(b[4]) for b in bars]
    ts = bars[-1][0]

    pos = s["positions"].get(pair)
    if pos:
        peak, stop, exit_px = ot.manage(entry=pos["entry"], peak=pos["peak"],
                                        stop=pos["stop"], bar_high=h[-1],
                                        bar_low=lo[-1], bar_close=c[-1])
        pos["peak"], pos["stop"], pos["bars"] = peak, stop, pos["bars"] + 1
        if exit_px is not None:
            net = ot.net_pct(pos["entry"], exit_px)
            rec = {"ts": now_ts, "pair": pair, "entry": pos["entry"],
                   "exit": exit_px, "net_pct": round(net, 4),
                   "bars": pos["bars"], "opened": pos["opened"],
                   "reason": "stop" if exit_px == stop else "trail"}
            s["closed"].append(rec)
            s["streak"] = s["streak"] + 1 if net <= 0 else 0
            del s["positions"][pair]
            with open(JOURNAL, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            log(f"CLOSE {pair} net {net:+.3f}% after {pos['bars']} bars ({rec['reason']})")
        return

    if now_ts < s.get("suspend_until", 0):
        return
    ok, why = ot.entry_ok(c, h, lo, o)
    if not ok:
        return
    s["positions"][pair] = {"entry": c[-1], "peak": h[-1],
                            "stop": ot.initial_stop(c[-1]), "bars": 0,
                            "opened": now_ts}
    log(f"OPEN  {pair} @ {c[-1]} stop {ot.initial_stop(c[-1]):.8f} ({why})")


def cycle(s: dict, ex) -> None:
    now = time.time()
    s["cycles"] = s.get("cycles", 0) + 1
    for pair in pairs():
        try:
            bars = fetch(ex, pair)
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

    # punish / reward
    sc = scorecard(s)
    if sc["rolling_n"] >= 20 and sc["win_pct"] < MIN_ROLL_WIN:
        if now >= s.get("suspend_until", 0):
            s["suspend_until"] = now + SUSPEND_BARS * 300
            log(f"SUSPEND: rolling win {sc['win_pct']}% < {MIN_ROLL_WIN}% "
                f"over {sc['rolling_n']} trades -- no entries for 24h")
    if s.get("streak", 0) >= MAX_STREAK:
        s["suspend_until"] = now + SUSPEND_BARS * 300
        s["streak"] = 0
        log(f"SUSPEND: {MAX_STREAK} losers in a row -- no entries for 24h")

    arm, why = ot.should_arm(sc)
    ctx = ot.death_context()
    log(f"cycle {s['cycles']}: open {len(s['positions'])} closed {sc['trades']} "
        f"win {sc['win_pct']}% net {sc['mean_net_pct']:+.4f}% "
        f"buffer ${ctx['buffer']:.2f} (${sc['min_headroom_usd']:.0f} above halt) "
        f"| armed={arm} ({why})")
    save_state(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--pairs", type=int, default=0)
    a = ap.parse_args()

    log(f"timing_shadow starting (orders: NEVER; armed={ot.ARMED})")
    s = load_state()
    try:
        ex = get_exchange()
        if a.pairs:
            global pairs
            allp = pairs()
            pairs = lambda: allp[:a.pairs]
        if a.once:
            cycle(s, ex)
            return 0
        while True:
            try:
                cycle(s, ex)
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
