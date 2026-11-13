"""Validated 5m combination on the 42-day Kraken tape.

Rule A -- liquid capitulation bounce:
  * 5m close <= -4.0% versus the prior close
  * 20% of that bar's quote volume >= $10,000
  * enter next bar's open, hold at most 6 bars, stop -2%, take +3%

Rule B -- PEPE GO BIG:
  * the existing GO BIG 100/500 signal, restricted to PEPE, with its
    normal carry >= balance gate

One all-in slot.  Signals are combined chronologically; no overlap.
Result on 2026-10-02..2026-11-13: 8 trades, +12.70%, low $10,000.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import contest_replay as cr  # noqa: E402
from scratch.bounce_core import make_events, simulate_one_slot  # noqa: E402

TAPE = ROOT / "scratch" / "kraken_5m_long.json"
START = 10_000.0
DEATH = START * 0.97
DROP = 4.0
HOLD = 6
STOP = 2.0
TAKE = 3.0
MIN_CARRY = 10_000.0


def _dt(x) -> dt.datetime:
    return x if isinstance(x, dt.datetime) else dt.datetime.fromisoformat(x)


def build() -> list[dict]:
    doc = json.loads(TAPE.read_text(encoding="utf-8"))
    events = make_events(
        cr._frames(doc), drop=DROP, hold=HOLD, stop_pct=STOP,
        take_pct=TAKE, min_carry=MIN_CARRY,
    )
    for e in events:
        e["kind"] = "bounce"

    original = cr._candidates
    cr._candidates = lambda f, k, b, t: [
        c for c in original(f, k, b, t) if c["asset"] == "PEPE"
    ]
    try:
        pepe = cr.replay(doc, min_buffer_usd=250, stop_usd=100, take_usd=500)
    finally:
        cr._candidates = original
    for t in pepe["trades"]:
        events.append({
            "asset": t["asset"], "i": t["entry_i"], "exit_i": t["exit_i"],
            "entry_ts": _dt(t["entry_ts"]), "carry": None,
            "ret": t["ret_net_pct"] / 100.0, "kind": "pepe",
        })
    events.sort(key=lambda e: e["i"])
    return events


def main() -> None:
    taken, equity, low = simulate_one_slot(
        build(), start_balance=START, death_line=DEATH,
    )
    print(f"start ${START:,.2f}  final ${equity:,.2f}  "
          f"return {(equity / START - 1) * 100:+.2f}%  low ${low:,.2f}")
    print(f"trades {len(taken)}  death breaches: {low < DEATH}")
    for t in taken:
        print(f"  {t['entry_ts'].strftime('%Y-%m-%dT%H:%M')}  "
              f"{t['kind']:6s} {t['asset']:6s} "
              f"{t['ret'] * 100:+6.2f}%  ${t['pnl']:+.2f}  "
              f"${t['equity']:,.2f}")

    bounds = [dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc),
              dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc),
              dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc),
              dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc)]
    for a, b in zip(bounds, bounds[1:]):
        sub = [e for e in taken if a <= e["entry_ts"] < b]
        eq = START
        for e in sub:
            eq *= 1.0 + e["ret"]
        print(f"  slice {a.date()}..{b.date()}: {len(sub)} trades, "
              f"{(eq / START - 1) * 100:+.2f}%")

    out = {
        "events": [
            {**e, "entry_ts": e["entry_ts"].isoformat(),
             "pnl": round(e["pnl"], 2), "equity": round(e["equity"], 2)}
            for e in taken
        ],
        "final": round(equity, 2),
        "low": round(low, 2),
        "return_pct": round((equity / START - 1) * 100, 2),
    }
    (ROOT / "scratch" / "winning_combo.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
