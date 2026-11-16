"""carry_and_stop.py — what carry GO BIG needs, and what stop an all-in can survive.

Two questions, both measured on the 183-day tape rather than assumed.

1. CARRY. GO BIG's first gate is `carry_usd >= balance`, carry being 20% of the book's
   quote volume. Invert it: for a balance B, the lane fires only on signals where
   carry >= B (all-in) or >= B/2 (half). So the fire-rate curve over B is the carry
   distribution read backwards. This prints both, so the threshold that would make the
   lane fire often is a number instead of a complaint.

2. STOP. There are NO stop orders on the platform (rules: "NO STOP ORDERS (the alert
   system is the ONLY exit)"), so a stop is a poll: it is checked every N seconds and
   filled at whatever the market is then. An all-in position at size s loses
   s * move% of equity; the death line is 3.00% away at a fresh start. So the largest
   adverse move an all-in can eat is 3.00%, and

       usable_stop_pct  <=  3.00 / s  -  (the move that happens inside one poll)

   Measured here from the tape: the 5m / 15m / 60m adverse move (low vs open, and the
   bar-to-bar gap open vs prior close), pooled and per pair, so the "move inside one
   poll" term is an empirical quantile rather than a guess.

    python scratch/carry_and_stop.py [tape ...]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import contest_account as ca
import contest_replay as cr
import numpy as np
import scratch.go_big_half_study as G
import scratch.pepe_engine as eng

OUT = os.path.join(HERE, "_carry_and_stop.json")
START, DEATH = 10_000.0, 9_700.0
BUFFER_PCT = 3.00            # (start - death)/start, a fresh contest
BALANCES = (1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000)
SIZES = (1.00, 0.50, 0.25, 0.10, 0.01)


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else None


def main():
    tape = (sys.argv[1] if len(sys.argv) > 1
            else os.path.join(HERE, "kraken_5m_long_183d_aligned.json"))
    tp = eng.Tape(tape)
    frames = tp.F
    print(f"tape {os.path.basename(tape)}  {tp.N:,} bars  {len(frames)} pairs")

    # ── 1. carry at the champion signals ───────────────────────────────────
    events = [e for e in G._events(tp) if e["kind"] != "bounce"]
    carry = np.array([float(tp.FP["qv"][e["k"]]) * 0.20 for e in events])
    carry = carry[carry > 0]
    curve = {}
    for b in BALANCES:
        allin = int((carry >= b).sum())
        half = int((carry >= b / 2.0).sum())
        curve[str(b)] = {"allin_fires": allin, "half_fires": half,
                         "allin_pct": round(100.0 * allin / len(carry), 1),
                         "half_pct": round(100.0 * half / len(carry), 1)}
    carry_stats = {"n_signals": int(len(carry)),
                   "p10": round(pct(carry, 10), 0), "p25": round(pct(carry, 25), 0),
                   "median": round(pct(carry, 50), 0), "p75": round(pct(carry, 75), 0),
                   "p90": round(pct(carry, 90), 0), "p99": round(pct(carry, 99), 0),
                   "max": round(float(carry.max()), 0)}

    print(f"\n1. CARRY at {len(carry)} champion signals")
    print(f"   median ${carry_stats['median']:,.0f}   p75 ${carry_stats['p75']:,.0f}   "
          f"p90 ${carry_stats['p90']:,.0f}   p99 ${carry_stats['p99']:,.0f}   "
          f"max ${carry_stats['max']:,.0f}")
    print(f"   {'balance':>9} {'all-in fires':>13} {'%':>6} {'half fires':>11} {'%':>6}")
    for b, v in curve.items():
        print(f"   ${int(b):>8,} {v['allin_fires']:>13} {v['allin_pct']:>5.1f}% "
              f"{v['half_fires']:>11} {v['half_pct']:>5.1f}%")

    # ── 2. the adverse move a stop has to survive ──────────────────────────
    pooled_5 = []
    pooled_gap = []
    worst = []
    for sym, f in frames.items():
        o = np.asarray(f["open"][:tp.N], dtype=float)
        lo = np.asarray(f["low"][:tp.N], dtype=float)
        cl = np.asarray(f["close"][:tp.N], dtype=float)
        if len(o) < 20:
            continue
        intra = (lo / o - 1.0) * 100.0                 # worst inside the bar
        gap = (o[1:] / cl[:-1] - 1.0) * 100.0          # overnight/thin-book gap
        pooled_5.append(intra)
        pooled_gap.append(gap)
        worst.append((sym, float(np.percentile(intra, 99.9)),
                      float(intra.min())))
    all5 = np.concatenate(pooled_5)
    allgap = np.concatenate(pooled_gap)
    # the move over one poll: 1 bar (5m), 2 bars, 3 bars of low-vs-open
    horizon = {}
    for bars in (1, 2, 3):
        xs = []
        for sym, f in frames.items():
            o = np.asarray(f["open"][:tp.N], dtype=float)
            lo = np.asarray(f["low"][:tp.N], dtype=float)
            k = min(len(o), tp.N) - bars
            if k > 10:
                xs.append((lo[bars:bars + k] / o[:k] - 1.0) * 100.0)
        a = np.concatenate(xs)
        horizon[f"{bars * 5}m"] = {"p99": round(float(np.percentile(a, 99)), 2),
                                   "p99.9": round(float(np.percentile(a, 99.9)), 2),
                                   "worst": round(float(a.min()), 2)}

    print(f"\n2. ADVERSE MOVE the stop must absorb (all pairs, {len(all5):,} bars each)")
    print(f"   5m low-vs-open   p99 {pct(all5, 99):.2f}%   p99.9 "
          f"{pct(all5, 99.9):.2f}%   worst {all5.min():.2f}%")
    print(f"   bar-to-bar gap   p99 {pct(allgap, 99):.2f}%   p99.9 "
          f"{pct(allgap, 99.9):.2f}%   worst {allgap.min():.2f}%")
    for k, v in horizon.items():
        print(f"   move in {k:>4}       p99 {v['p99']:>6.2f}%   p99.9 "
              f"{v['p99.9']:>6.2f}%   worst {v['worst']:>7.2f}%")

    # ── 3. what stop each size can actually carry ─────────────────────────
    slip = {"60s poll (5m bar)": horizon["5m"]["p99.9"],
            "15m poll": horizon["15m"]["p99.9"]}
    table = {}
    print(f"\n3. STOP BUDGET at a fresh start (death 3.00% away)")
    print(f"   {'size':>5} {'max loss':>9} {'stop @5m poll':>14} {'stop @15m poll':>15}")
    for s in SIZES:
        max_move = BUFFER_PCT / s
        row = {"max_adverse_move_pct": round(max_move, 2)}
        for label, sl in slip.items():
            row[label] = round(max_move - sl, 2)
        table[f"{s*100:g}%"] = row
        print(f"   {s*100:>4g}% {max_move:>8.2f}% {row['60s poll (5m bar)']:>13.2f}% "
              f"{row['15m poll']:>14.2f}%")

    # where the taper makes all-in survivable: the carry needed to justify a stop
    # big enough to be out of the noise (>= the measured average loss, 2.08%)
    need_stop = 2.08
    print(f"\n   a stop at the measured average loss ({need_stop}%) fits only if "
          f"size <= {BUFFER_PCT/need_stop:.2f} of the account")
    print(f"      -> at 10k that is ${BUFFER_PCT/need_stop*10000:,.0f}, "
          f"i.e. carry >= ${BUFFER_PCT/need_stop*10000:,.0f} for the all-in rung")

    out = {"tape": os.path.basename(tape), "buffer_pct": BUFFER_PCT,
           "carry": {"stats": carry_stats, "curve": curve},
           "moves": {"5m_intrabar": {"p99": round(pct(all5, 99), 2),
                                     "p99.9": round(pct(all5, 99.9), 2),
                                     "worst": round(float(all5.min()), 2)},
                     "gap": {"p99": round(pct(allgap, 99), 2),
                             "p99.9": round(pct(allgap, 99.9), 2),
                             "worst": round(float(allgap.min()), 2)},
                     "horizons": horizon},
           "stop_budget": table,
           "size_implied_by_stop": round(BUFFER_PCT / need_stop, 3),
           "worst_pairs": sorted(worst, key=lambda x: x[1])[:5]}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()


def test_fix(tape: str, cap: float = 0.50, stop_frac: float = 0.015):
    """The proposed rule on the whole 183 days: size capped, stop tightened.

    GO BIG rung -> min(cap * equity, carry); the half rung -> the same cap; stop and
    take scaled with the position so the marks keep their shape. Bounce lanes are
    excluded: it was a full-size bounce trade that killed the 183-day run, and this
    asks whether the capped GO BIG rule survives on its own.
    """
    tp = eng.Tape(tape)
    events = [e for e in G._events(tp) if e["kind"] != "bounce"]
    eq, busy, taken, big, half = G.START, -1, 0, 0, 0
    low = G.START
    for e in events:
        k = e["k"]
        if k <= busy:
            continue
        carry = float(tp.FP["qv"][k]) * 0.20
        if G._support(tp, k, carry, eq):
            size, tag = min(cap * eq, carry), "capped all-in"
            big += 1
        elif G._support(tp, k, carry, eq / 2.0):
            size, tag = min(cap * eq, carry), "half rung"
            half += 1
        else:
            continue
        stop_u = G.START * stop_frac * size / eq
        take_u = G.TAKE * size / eq
        r = eng.one_trade(tp, k, size, take_usd=take_u, stop_usd=stop_u)
        eq += r["pnl"]
        busy = r["exit_i"]
        taken += 1
        low = min(low, eq)
        if eq <= DEATH:
            break
    out = {"cap": cap, "stop_frac": stop_frac, "trades": taken,
           "capped_allin": big, "half_rung": half,
           "final": round(eq, 2), "ret_pct": round((eq / G.START - 1) * 100, 2),
           "low": round(low, 2), "dead": low <= DEATH}
    print(f"FIX cap {cap:.0%} stop {stop_frac:.2%}: {taken} trades "
          f"(all-in {big}, half {half})  final ${eq:,.2f} "
          f"{(eq/G.START-1)*100:+.2f}%  low ${low:,.2f} "
          f"{'DEATH' if low <= DEATH else 'alive'}")
    return out
