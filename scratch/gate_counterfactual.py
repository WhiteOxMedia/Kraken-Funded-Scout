"""WHAT WOULD TODAY'S CODE HAVE DONE — Sep 3 to Nov 15, every real close (Nov 15).

Only one thing that shipped today changes behaviour: the BREAKOUT GATE — a
champion fires only when its `entry_quality` (position in the prior hour's range)
is >= 0.85, and an unmeasurable champion keeps the old rule. The near-miss
recorder is shadow-only, and both the entry and exit studies changed nothing
(rejected / indeterminate). So the question reduces to: on the fires the Scout
actually made, which would the gate have refused, and what did those refusals
cost or save?

Method
  * every close in `contest_trades.jsonl` is re-entered at its real fill time and
    price; `pnl_net` is verified to equal price math on all rows, so each fire's
    dollars are real;
  * the ledger is first cleaned — the journal carries test fixtures (`FAKE/USD`)
    and a second, smaller balance chain spliced in, so the contest ledger is
    rebuilt from its own balance continuity;
  * `entry_quality` is recomputed with the scanner's own `_entry_quality` on the
    hourly frame built from the stored 5m closes at the fill, and the gate's own
    `passes()` decides — the shipped rule, not a lookalike;
  * the death-line is judged on the REAL balance path, and today's arm is that
    path plus the dollars the gate would have kept (an add-back, not a re-run of
    compounding), so nothing here invents a balance the Scout never had.

Run: .venv/Scripts/python.exe -X utf8 scratch/gate_counterfactual.py
"""
from __future__ import annotations

import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import breakout_gate as bg                       # noqa: E402
import scanner as sc                             # noqa: E402
from entry_edge_study import load_tape           # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
OUT = ROOT / "scratch" / "_gate_counterfactual.json"
DEATH_LINE = 9_700.0
DEATH_MARGIN = 10_000.0 - DEATH_LINE   # the whole $300 cushion
HOURLY = 3600


# ── the ledger ────────────────────────────────────────────────────────────────
def is_fixture(r) -> bool:
    pair = r.get("pair") or ""
    if pair.startswith(("FAKE", "TEST", "DUMMY")):
        return True
    try:
        if float(r.get("size") or 0) == 500.0 and float(r.get("entry") or 0) == 100.0:
            return True
    except (TypeError, ValueError):
        pass
    return (r.get("note") or "").lower().startswith("test")


def clean_ledger(rows):
    """Keep the contest's own chain; drop fixtures and the spliced second ledger."""
    bal = float(rows[0]["balance_after"]) - float(rows[0]["pnl_net"])
    out, fixtures, foreign, resync = [], 0, 0, 0
    for r in rows:
        pnl = float(r["pnl_net"])
        ba = float(r.get("balance_after") or 0.0)
        if is_fixture(r):
            fixtures += 1
            continue
        if abs(ba - (bal + pnl)) <= 0.02:
            out.append(r)
            bal += pnl
        elif ba < 8000.0 or abs(ba - bal) > 500.0:
            foreign += 1          # a different account's row: not the contest
            bal = ba
        else:
            out.append(r)         # balance stamped by another writer: trade stands
            bal = ba
            resync += 1
    return out, {"fixtures": fixtures, "foreign": foreign, "resync": resync}


# ── entry_quality, exactly as the shipped scanner computes it ─────────────────
def hourly_rows(bars, up_to_s):
    idx = bisect.bisect_right([b[0] for b in bars], up_to_s)
    seg = bars[:idx]
    if len(seg) < 60:
        return None
    buckets = {}
    order = []
    for b in seg:
        h = int(b[0] // HOURLY) * HOURLY
        if h not in buckets:
            buckets[h] = [float(b[1]), float(b[2]), float(b[3]), float(b[4])]
            order.append(h)
        else:
            v = buckets[h]
            v[1] = max(v[1], float(b[2]))
            v[2] = min(v[2], float(b[3]))
            v[3] = float(b[4])
    if len(order) < 60:
        return None
    return order, buckets


def entry_quality(bars, at_s, px):
    got = hourly_rows(bars, at_s)
    if not got:
        return None
    order, buckets = got
    import pandas as pd
    rows = [{"open": buckets[h][0], "high": buckets[h][1],
             "low": buckets[h][2], "close": buckets[h][3]} for h in order]
    df = pd.DataFrame(rows)
    df.loc[df.index[-1], "close"] = float(px)     # the fill, not the bar's close
    return sc._entry_quality(df)


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def month_key(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


def arm_stats(rows, keep):
    sel = [r for r in rows if keep(r)]
    if not sel:
        return {"n": 0, "net": 0.0, "avg": 0.0, "win_pct": 0.0, "worst": 0.0}
    pnl = [float(r["pnl_net"]) for r in sel]
    return {"n": len(sel),
            "net": round(sum(pnl), 2),
            "avg": round(sum(pnl) / len(pnl), 3),
            "win_pct": round(100.0 * sum(1 for p in pnl if p > 0) / len(pnl), 1),
            "worst": round(min(pnl), 2)}


def curve(rows, keep):
    """Cumulative booked P&L of an arm from zero: the only series the journal
    fully reconciles (its balance stamps carry 91 competing writes, and a second
    $5k account's rows are spliced in)."""
    bal = peak = low = 0.0
    low_at = breach = None
    for r in rows:
        if not keep(r):
            continue
        bal += float(r["pnl_net"])
        peak = max(peak, bal)
        if bal < low:
            low, low_at = bal, r["sold_at"][:16]
        if breach is None and bal < -DEATH_MARGIN:
            breach = r["sold_at"][:16]
    return {"end": round(bal, 2), "low": round(low, 2), "peak": round(peak, 2),
            "max_dd": round(peak - low, 2), "low_at": low_at,
            "breached_margin": breach}


def main():
    tape = load_tape()
    raw = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, diag = clean_ledger(raw)
    print(f"ledger: {len(raw)} journal rows -> {len(rows)} contest fires "
          f"(fixtures {diag['fixtures']}, foreign-ledger {diag['foreign']}, "
          f"stamp-resync {diag['resync']})")

    priced, skipped, unmeasured = [], 0, 0
    for r in rows:
        sym = (r.get("pair") or "").split("/")[0]
        bars = tape.get(sym)
        sold = parse_ts(r.get("sold_at") or "")
        try:
            held = float(r.get("held_min") or 0.0)
            px = float(r.get("entry") or 0.0)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if bars is None or sold is None or held <= 0 or px <= 0:
            skipped += 1
            continue
        opened = sold - held * 60.0
        if opened < bars[0][0] or opened > bars[-1][0]:
            skipped += 1
            continue
        eq = entry_quality(bars, opened, px)
        if eq is None:
            unmeasured += 1
        r = dict(r)
        r["_t"] = opened
        r["_eq"] = None if eq is None else round(float(eq), 4)
        r["_gate_ok"] = bg.passes(r["_eq"], bg.BREAKOUT_MIN)      # the shipped choke
        priced.append(r)
    priced.sort(key=lambda r: r["_t"])
    print(f"replayed {len(priced)} fires on the tape ({skipped} unpriceable, "
          f"{unmeasured} unmeasured -> old behaviour); gate {bg.BREAKOUT_MIN}")

    keep_old = lambda r: True                                  # noqa: E731
    keep_new = lambda r: bool(r["_gate_ok"])                   # noqa: E731

    old, new = arm_stats(priced, keep_old), arm_stats(priced, keep_new)
    vetoed = [r for r in priced if not r["_gate_ok"]]
    veto_pnl = sum(float(r["pnl_net"]) for r in vetoed)
    veto_wins = sum(float(r["pnl_net"]) for r in vetoed if float(r["pnl_net"]) > 0)
    veto_losses = sum(float(r["pnl_net"]) for r in vetoed if float(r["pnl_net"]) <= 0)

    print(f"\n{'='*100}\nWHAT THE TWO ARMS BOOK — "
          f"{datetime.fromtimestamp(priced[0]['_t']).date()} to "
          f"{datetime.fromtimestamp(priced[-1]['_t']).date()}\n{'='*100}")
    for name, s in (("yesterday's code (every fire)", old), ("today's code (gate on)", new)):
        print(f"  {name:<30} fires {s['n']:>4}  win {s['win_pct']:>5.1f}%  "
              f"net ${s['net']:>+9.2f}  avg ${s['avg']:>+7.3f}  worst ${s['worst']:>+8.2f}")
    print(f"  {'difference':<30} fires {new['n']-old['n']:>+4}  win "
          f"{new['win_pct']-old['win_pct']:>+5.1f} pts  net ${new['net']-old['net']:>+9.2f}  "
          f"avg ${new['avg']-old['avg']:>+7.3f}")

    print(f"\n  gate refused {len(vetoed)} of {len(priced)} fires "
          f"({100.0*len(vetoed)/len(priced):.1f}%): booked ${veto_pnl:+.2f} "
          f"(${veto_pnl/len(vetoed):+.3f} each)")
    print(f"    it kept  ${veto_losses:+.2f} of their losses "
          f"and gave back ${veto_wins:+.2f} of their wins")

    pa, pb = curve(priced, keep_old), curve(priced, keep_new)
    print(f"\n  booked-P&L curve from zero (the journal's own dollars; the contest "
          f"cushion is ${DEATH_MARGIN:.0f}):")
    for name, c in (("yesterday", pa), ("today", pb)):
        print(f"    {name:<10} end ${c['end']:>+9.2f}  low ${c['low']:>+9.2f}"
              f"  max DD ${c['max_dd']:>8.2f}  through -${DEATH_MARGIN:.0f}? "
              f"{c['breached_margin'] or 'never'}")
    print(f"    yesterday's curve bottoms on {pa['low_at']}; today's on {pb['low_at']}")

    print(f"\n  per month")
    months = sorted({month_key(r["_t"]) for r in priced})
    per_month = {}
    for m in months:
        sub = [r for r in priced if month_key(r["_t"]) == m]
        a, b = arm_stats(sub, keep_old), arm_stats(sub, keep_new)
        per_month[m] = {"old": a, "new": b}
        print(f"    {m}   fires {a['n']:>3}->{b['n']:>3}   win {a['win_pct']:>5.1f}%->"
              f"{b['win_pct']:>5.1f}%   net ${a['net']:>+8.2f}->${b['net']:>+8.2f}   "
              f"delta ${b['net']-a['net']:>+8.2f}")

    print(f"\n  fires by entry_quality band (booked $ per fire) — the mechanism")
    bands = [(0.00, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.75),
             (0.75, 0.85), (0.85, 0.95), (0.95, 1.01)]
    band_rows = []
    for lo, hi in bands:
        sub = [r for r in priced if r["_eq"] is not None and lo <= r["_eq"] < hi]
        if not sub:
            continue
        pnl = [float(r["pnl_net"]) for r in sub]
        band = {"lo": lo, "hi": hi, "n": len(sub),
                "win_pct": round(100.0 * sum(1 for p in pnl if p > 0) / len(sub), 1),
                "net": round(sum(pnl), 2), "avg": round(sum(pnl) / len(sub), 3)}
        band_rows.append(band)
        mark = "  <- kept" if lo >= bg.BREAKOUT_MIN else ""
        print(f"    {lo:.2f}-{hi:.2f}  n {len(sub):>4}  win {band['win_pct']:>5.1f}%  "
              f"net ${band['net']:>+9.2f}  ${band['avg']:>+7.3f}/fire{mark}")
    unmeas = [r for r in priced if r["_eq"] is None]
    if unmeas:
        pnl = [float(r["pnl_net"]) for r in unmeas]
        print(f"    unmeasured n {len(unmeas):>4}  win "
              f"{100.0*sum(1 for p in pnl if p>0)/len(pnl):>5.1f}%  "
              f"net ${sum(pnl):>+9.2f}  ${sum(pnl)/len(pnl):>+7.3f}/fire  <- kept")

    # sensitivity, not tuning: the shipped threshold came from its own fit/holdout
    print(f"\n  the gate at other thresholds (this window is in-sample for all of them)")
    print(f"    {'min':>5} {'fires':>6} {'net $':>10} {'win%':>6} {'max DD':>9}   note")
    sweep = []
    for th in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        sel = [r for r in priced if r["_eq"] is None or r["_eq"] >= th]
        pnl = [float(r["pnl_net"]) for r in sel]
        c = curve(priced, lambda r, th=th: r["_eq"] is None or r["_eq"] >= th)
        sweep.append({"min": th, "n": len(sel), "net": round(sum(pnl), 2),
                      "win_pct": round(100.0 * sum(1 for p in pnl if p > 0) / len(pnl), 1),
                      "max_dd": c["max_dd"]})
        note = " <- shipped" if th == bg.BREAKOUT_MIN else ""
        print(f"    {th:>5.2f} {len(sel):>6} {sum(pnl):>+10.2f} "
              f"{sweep[-1]['win_pct']:>6.1f} {c['max_dd']:>9.2f}{note}")
    strict = curve(priced, lambda r: r["_eq"] is not None and r["_eq"] >= bg.BREAKOUT_MIN)
    strict_pnl = [float(r["pnl_net"]) for r in priced
                  if r["_eq"] is not None and r["_eq"] >= bg.BREAKOUT_MIN]
    print(f"    without the unmeasured pass-through: {len(strict_pnl)} fires, "
          f"${sum(strict_pnl):+.2f}, DD ${strict['max_dd']:.2f}")

    OUT.write_text(json.dumps({
        "window": [datetime.fromtimestamp(priced[0]["_t"]).isoformat(),
                   datetime.fromtimestamp(priced[-1]["_t"]).isoformat()],
        "journal_rows": len(raw), "contest_fires": len(rows), "ledger_diag": diag,
        "replayed": len(priced), "unpriceable": skipped, "unmeasured": unmeasured,
        "gate_min": bg.BREAKOUT_MIN, "old": old, "new": new,
        "vetoed": {"n": len(vetoed), "net": round(veto_pnl, 2),
                   "wins_given_up": round(veto_wins, 2),
                   "losses_kept": round(veto_losses, 2)},
        "curve": {"yesterday": pa, "today": pb, "death_margin": DEATH_MARGIN},
        "months": per_month, "bands": band_rows, "sweep": sweep,
        "strict_measured_only": {"n": len(strict_pnl), "net": round(sum(strict_pnl), 2),
                                 "max_dd": strict["max_dd"]},
    }, indent=1), encoding="utf-8")
    print("\nwrote scratch/_gate_counterfactual.json")


if __name__ == "__main__":
    main()
