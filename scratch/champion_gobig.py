"""champion_gobig.py — champion movers + the GO BIG sizing ladder, three ways.

The rule being tested (user, Nov 15): a champion mover always fires; GO BIG is a FLAG
on it, never a veto. So the ladder is

    all-in   when the book carries the whole account and conviction/tide clear the bar
    half     when the book carries half
    normal   1% when it carries neither -- the champion still takes its popup

Three runs, each the "again" of something already measured:

  R1  the go_big_half_study population (bounce + PEPE crosses, 183d tape), with the
      ladder added as a new variant beside the shipped all-in and the half variants.
      Here every PEPE cross plays the champion mover's part: it is never skipped.
  R2  today's champion fires (contest_gate_log route == "champion"), priced on live 5m
      candles, carry = 20% of the entry bar's quote volume, one slot, trend-ride exit.
  R3  the contest maths re-run across the ladder's sizes: theta = 200e/(s*sigma^2), so
      all-in divides the pass odds by 100x against a 1% ticket.

    python scratch/champion_gobig.py             # R1+R3 (R1 needs the tape)
    python scratch/champion_gobig.py --no-tape   # R2+R3 only
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import contest_account as ca
import overwatch_timing as ot
import scratch.go_big_half_study as G
import scratch.pepe_engine as eng

START, DEATH, TARGET = 10_000.0, 9_700.0, 11_200.0
OUT = os.path.join(HERE, "_champion_gobig.json")


# ── R1: the ladder inside the study's event model ──────────────────────────
def ladder(tp, events, lo: int = 0, hi: int | None = None):
    """GO BIG -> all-in, half-carry -> half, else the 1% ticket. Never skip."""
    hi = tp.N if hi is None else hi
    eq, busy, taken = G.START, -1, []
    for e in events:
        if not (lo <= e["i"] < hi) or e["i"] <= busy:
            continue
        if e["kind"] == "bounce":
            if e["carry"] is not None and e["carry"] < eq:
                continue
            ret, exit_i, tag = e["ret"], e["exit_i"], "bounce"
        else:
            k = e["k"]
            carry = float(tp.FP["qv"][k]) * 0.20 if "qv" in tp.FP else 0.0
            if G._support(tp, k, carry, eq):
                size, take_u, stop_u, tag = eq, G.TAKE, G.STOP, "GO BIG"
            elif G._support(tp, k, carry, eq / 2.0):
                size, take_u, stop_u, tag = eq / 2.0, G.TAKE, G.STOP, "half"
            else:
                size = eq * 0.01
                take_u, stop_u = G.TAKE * 0.01, G.STOP * 0.01
                tag = "normal"
            r = G.eng.one_trade(tp, k, size, take_usd=take_u, stop_usd=stop_u)
            ret, exit_i = r["pnl"] / eq, r["exit_i"]
        pnl = eq * ret
        eq += pnl
        busy = exit_i
        taken.append({"tag": tag, "pnl": pnl})
        if eq <= DEATH:
            break
    low = min([G.START] + [G.START + sum(t["pnl"] for t in taken[:i + 1])
                           for i in range(len(taken))])
    return taken, eq, low, 0


def run_r1(tape_path):
    tp = eng.Tape(tape_path)
    events = G._events(tp)
    carries = [float(tp.FP["qv"][e["k"]]) * 0.20 for e in events
               if e["kind"] != "bounce" and "qv" in tp.FP]
    sup = 0
    for e in events:
        if e["kind"] == "bounce":
            continue
        if G._support(tp, e["k"], float(tp.FP["qv"][e["k"]]) * 0.20, G.START):
            sup += 1
    print(f"    book carry at the {len(carries)} signals: max "
          f"${max(carries) if carries else 0:,.0f}, median "
          f"${sorted(carries)[len(carries)//2] if carries else 0:,.0f} "
          f"-> GO BIG supported {sup} times against a ${G.START:,.0f} account")
    print(f"\nR1  {os.path.basename(str(tape_path))}  ({tp.N:,} bars, "
          f"{len(events)} events)")
    rows = {}
    for name, kind, marks in G.VARIANTS:
        res = G.market(tp, events, kind, marks)
        taken, final, low, _ = res
        rows[name.strip()] = {
            "trades": len(taken), "final": round(final, 2),
            "ret_pct": round((final / G.START - 1) * 100, 2),
            "low": round(low, 2), "dead": low < DEATH,
        }
        print(f"   {name} {len(taken):3d} trades  ${final:>10,.2f} "
              f"{(final/G.START-1)*100:+7.2f}%  low ${low:>9,.2f}  "
              f"{'DEATH' if low < DEATH else 'alive'}")
    taken, final, low, _ = ladder(tp, events)
    by = {}
    for t in taken:
        by.setdefault(t["tag"], [0, 0.0])
        by[t["tag"]][0] += 1
        by[t["tag"]][1] += t["pnl"]
    rows["champion ladder"] = {
        "trades": len(taken), "final": round(final, 2),
        "ret_pct": round((final / G.START - 1) * 100, 2),
        "low": round(low, 2), "dead": low < DEATH,
        "by_tag": {k: {"n": v[0], "pnl": round(v[1], 2)} for k, v in by.items()},
    }
    print(f"   champion ladder    {len(taken):3d} trades  ${final:>10,.2f} "
          f"{(final/G.START-1)*100:+7.2f}%  low ${low:>9,.2f}  "
          f"{'DEATH' if low < DEATH else 'alive'}")
    tags = rows["champion ladder"]["by_tag"]
    print("      " + "  ".join(f"{k} {v['n']} (${v['pnl']:+,.0f})"
                               for k, v in tags.items()))
    return rows


# ── R2: today's champion fires, ladder sizing, trend-ride exit ─────────────
def run_r2():
    rows = [json.loads(l) for l in
            open(os.path.join(ROOT, "contest_gate_log.jsonl"), encoding="utf-8")
            if l.strip()]
    champ = [r for r in rows if r.get("decision") == "fire"
             and str(r.get("route", "")).lower() == "champion"]
    if not champ:
        return {"error": "no champion fires in the log"}

    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    pairs = sorted({r["pair"] for r in champ})
    series = {}
    for p in pairs:
        try:
            o = ex.fetch_ohlcv(p, timeframe="5m", limit=300)
        except Exception:
            continue
        if o:
            series[p] = {"ts": [b[0] / 1000.0 for b in o],
                         "o": [b[1] for b in o], "h": [b[2] for b in o],
                         "l": [b[3] for b in o], "c": [b[4] for b in o],
                         "v": [b[5] for b in o]}

    def breadth_at(t):
        up = tot = 0
        for s in series.values():
            i = bisect.bisect_right(s["ts"], t) - 1
            if i >= 12:
                tot += 1
                up += 1 if s["c"][i] > s["c"][i - 12] else 0
        return (100.0 * up / tot) if tot else None

    equity = ca.load_state()["current_balance"]
    held_until, taken, skipped = -1.0, [], {"open": 0, "no_bar": 0}
    import collections
    declines = collections.Counter()
    for r in sorted(champ, key=lambda x: x["ts"]):
        s = series.get(r["pair"])
        t = dt.datetime.fromisoformat(r["ts"]).timestamp()
        if not s:
            skipped["no_bar"] += 1
            continue
        i = bisect.bisect_right(s["ts"], t)
        if i >= len(s["ts"]) or t < held_until:
            skipped["open"] += 1
            continue
        entry = s["o"][i]
        carry = 0.20 * s["v"][i] * s["c"][i]
        conv = float(r.get("confidence") or 0.0)
        pct = breadth_at(t)
        tide = "RISING" if (pct or 0) >= 75 else "FLAT"
        ok, why = ca.go_big_supported(action="buy", tide_state=tide, tide_pct=pct,
                                      carry_usd=carry, balance=equity,
                                      conviction=conv, slot_open=False)
        half_ok, _w2 = ca.go_big_supported(action="buy", tide_state=tide, tide_pct=pct,
                                           carry_usd=carry, balance=equity / 2.0,
                                           conviction=conv, slot_open=False)
        if ok:
            size, tag = equity, "GO BIG"
        elif half_ok:
            size, tag = equity / 2.0, "half"
        else:
            size, tag = equity * 0.01, "normal"
        declines[why.split("(")[0].strip()[:52]] += 1
        peak, stop = s["h"][i], ot.initial_stop(entry)
        exit_px, exit_i = None, None
        for k in range(i, len(s["ts"])):
            peak, stop, exit_px, reason = ot.manage(entry=entry, peak=peak, stop=stop,
                                                    bar_high=s["h"][k],
                                                    bar_low=s["l"][k])
            if exit_px is not None:
                exit_i = k
                break
        if exit_px is None or size <= 0:
            skipped["open"] += 1
            continue
        net = ot.net_pct(entry, exit_px)
        pnl = size * net / 100.0
        equity += pnl
        held_until = s["ts"][exit_i] + 300
        taken.append({"pair": r["pair"], "tag": tag, "net": net, "pnl": pnl,
                      "carry": round(carry), "conv": conv, "breadth": pct})

    by = {}
    for x in taken:
        by.setdefault(x["tag"], [0, 0.0])
        by[x["tag"]][0] += 1
        by[x["tag"]][1] += x["pnl"]
    nets = [x["net"] for x in taken]
    low = ca.load_state()["current_balance"] + min(
        [0.0] + [sum(y["pnl"] for y in taken[:i + 1]) for i in range(len(taken))])
    out = {
        "champion_fires": len(champ), "priced": len(taken), "skipped": skipped,
        "start_equity": round(ca.load_state()["current_balance"], 2),
        "final_equity": round(equity, 2), "low_equity": round(low, 2),
        "ret_pct": round((equity / ca.load_state()["current_balance"] - 1) * 100, 2),
        "died": low <= DEATH, "won": equity >= TARGET,
        "by_tag": {k: {"n": v[0], "pnl": round(v[1], 2)} for k, v in by.items()},
        "win_pct": round(100 * sum(1 for n in nets if n > 0) / len(nets), 1) if nets else None,
        "mean_net_pct": round(st.fmean(nets), 4) if nets else None,
        "gobig_declines": dict(declines.most_common(6)),
    }
    print(f"\nR2  champion fires today: {len(champ)} advertised, {len(taken)} priced "
          f"({skipped})")
    print(f"    equity ${out['start_equity']:,.2f} -> ${out['final_equity']:,.2f} "
          f"({out['ret_pct']:+.2f}%), low ${out['low_equity']:,.2f}, "
          f"{'DEATH' if out['died'] else 'alive'}")
    for k, v in out["by_tag"].items():
        print(f"      {k:<7} n={v['n']:>2}  ${v['pnl']:+,.2f}")
    if nets:
        print(f"      win {out['win_pct']}%  net/trade {out['mean_net_pct']:+.4f}%")
    for k, v in out["gobig_declines"].items():
        print(f"      GO BIG declined {v:>2}x  {k}")
    return out


# ── R3: the contest maths across the ladder's sizes ────────────────────────
def run_r3():
    e_pct, sigma_e = 0.0269, 0.958      # measured, scratch/contest_math.py
    bal = ca.load_state()["current_balance"]
    a = math.log(TARGET / bal)
    b = math.log(bal / DEATH)
    rows = {}
    for s in (0.01, 0.02, 0.05, 0.50, 1.00):
        mu = math.log(1 + s * e_pct / 100.0)
        var = (s * sigma_e / 100.0) ** 2
        theta = 2 * mu / var
        p = ((1 - math.exp(-theta * b)) / (1 - math.exp(-theta * (a + b)))
             if abs(theta) > 1e-12 else b / (a + b))
        rows[f"{s*100:g}%"] = {
            "theta": round(theta, 1),
            "pass_prob": round(p, 3),
            "trades_to_target": int(a / mu) if mu > 0 else None,
            "worst_trade_pct_of_balance": round(s * 2.079, 3),
            "buffer_pct_of_balance": round(100 * (bal - DEATH) / bal, 2),
        }
    print(f"\nR3  contest maths by ticket size (measured edge {e_pct}%, sd {sigma_e}%)")
    print(f"    {'size':>6} {'theta':>8} {'P(pass)':>8} {'trades':>8} "
          f"{'worst trade':>12} {'buffer':>8}")
    for k, v in rows.items():
        print(f"    {k:>6} {v['theta']:>8.0f} {v['pass_prob']*100:>7.1f}% "
              f"{(v['trades_to_target'] or 0):>8,} "
              f"{v['worst_trade_pct_of_balance']:>11.2f}% "
              f"{v['buffer_pct_of_balance']:>7.2f}%")
    return rows


def main():
    out = {}
    tapes = [a for a in sys.argv[1:] if a.endswith(".json")]
    if not tapes:
        tapes = [os.path.join(HERE, "kraken_5m_long_183d.json")]
    if "--no-tape" not in sys.argv:
        out["R1"] = {}
        for t in tapes:
            if not os.path.exists(t):
                continue
            print("")
            print("== R1 on " + os.path.basename(t))
            try:
                out["R1"][os.path.basename(t)] = run_r1(t)
            except Exception as exc:                   # noqa: BLE001
                out["R1"][os.path.basename(t)] = {"error": f"{type(exc).__name__}: {exc}"}
                print("R1 failed:", out["R1"][os.path.basename(t)]["error"])
    try:
        out["R2"] = run_r2()
    except Exception as exc:                           # noqa: BLE001
        out["R2"] = {"error": f"{type(exc).__name__}: {exc}"}
        print("R2 failed:", out["R2"]["error"])
    out["R3"] = run_r3()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
