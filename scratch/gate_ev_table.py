"""GATE EV TABLE (Oct 1, the burial hindsight's EV lens — the Sep 30
study's ask, made monthly): the gate EV the operator asked for —
expected value per buried candidate BY GATE FAMILY, ranked, using the
same fill model and the same data the panel's burial hindsight speaks:

  · candidates = the burial ledger (popup_suppressions.jsonl), the
    37-candidate funnel window (default: last 7 days, first-seen per
    pair+gate — one candidate, not one suppression line),
  · outcome   = the fill-modeled run within 7h from ohlcv_cache.db —
    entry at ask (mid + half spread), exit at the peak bid (peak −
    half spread), round-trip taker fee netted (fills.FEE_RT_PCT),
    floored at 0.0 — the SAME currency the capture panel speaks,
  · EV        = payer_rate × medfill_pay + (1 − payer_rate) ×
    medfill_dud — the probability-weighted median blend: what one
    buried candidate is worth if taken, in % net per trade. $/100
    equals the percentage (the fee drag ~0.52/$100 is already inside
    every fill_run).

Ranked by EV. A gate family with EV <= 0 buries money worth less than
the fees it would pay — those are correct burials; a family with
positive EV is where the missed money lives. Two honest caveats: the
EV is a DECISION-VALUE estimate, not a textbook expectation (fills are
floored at 0 — a real stop would cap losses below the floor — and the
meds are conditional medians, not means), and candidates whose 7h
horizon has not fully elapsed yet are CENSORED OUT of the scoring (a
half-watched candidate reads as a dud it may not be).

Read-only: opens ohlcv_cache.db in URI readonly mode, never writes
anything but its own report. Scout-side only.

Usage: python scratch/gate_ev_table.py [--window 48h|7d|panel400] [--hours 7]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SCOUT = Path(__file__).resolve().parent.parent

# The panel's spread + capture-floor constants (app.py ~L2654) — read
# from source rather than importing the streamlit app. The FEE is NOT
# read from the panel: the panel models 0.26% RT but the bot's real
# taker cost (fills.py FEE_RT_PCT) and the user's observed drag are
# 0.52% — the default here. A uniform fee shift moves every gate's EV
# by the same amount (ranking is invariant); the absolute per-candidate
# EV should price the REAL fee.
def _model_constants() -> tuple[float, float]:
    import re
    try:
        src = (SCOUT / "app.py").read_text(encoding="utf-8")
        spread = float(re.search(r"^SPREAD = ([0-9.]+)", src, re.M).group(1))
        floor = float(re.search(
            r"^FILL_CAPTURE_FLOOR = ([0-9.]+)", src, re.M).group(1))
        return spread, floor
    except Exception:
        return 0.001, 0.10

SPREAD, FILL_CAPTURE_FLOOR = _model_constants()
DEFAULT_FEE_RT_PCT = 0.52   # the bot's real taker RT (fills.py); panel models 0.26

SUPP = SCOUT / "popup_suppressions.jsonl"
DB = SCOUT / "ohlcv_cache.db"
OUT = SCOUT / "scratch" / "gate_ev_table.txt"
GOAL = 1.5          # the study's payer goal (run >= 1.5% peaks)
DUD_ASSUMED = 0.0   # dud medfill floor — real duds rarely beat fees


def load_candidates(window: str) -> tuple[list[dict], str]:
    """First-seen candidates from the burial ledger.

    Windows: '48h' (the live funnel, default), '7d' (the week),
    'panel400' (the panel's exact recipe: last 400 ledger lines,
    first-seen per PAIR — one candidate per pair regardless of gate).
    The time windows dedup per PAIR+GATE (first-seen per tuple): each
    gate family is charged for ITS OWN burials of a pair, not the
    first gate that happened to speak — the panel's by_gate join makes
    the same choice. Timestamps are PARSED, never lexically compared
    (ISO strings with mixed UTC offsets misorder — the Copilot catch;
    here the scanner writes a fixed -06:00 offset, but parse anyway
    so the convention does not depend on it).
    """
    lines = [l for l in SUPP.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if window == "panel400":
        rows = [json.loads(l) for l in lines][-400:]
        rows = [r for r in rows if r.get("pair")]
        first: dict = {}
        for r in rows:
            first.setdefault(str(r["pair"]), r)
        # re-key the panel recipe to (pair, gate) tuples so the one
        # candidates list downstream stays uniform (Copilot catch: the
        # string keys flowed into tuple unpacking as one-char pairs)
        first = {(k, str(v.get("gate") or "?")): v
                 for k, v in first.items()}
    else:
        cutoff = (datetime.now().astimezone()
                  - (timedelta(hours=48) if window == "48h"
                     else timedelta(days=7)))
        rows = [json.loads(l) for l in lines if l.strip()]
        first = {}
        for r in rows:
            ts = r.get("ts")
            if not r.get("pair") or not isinstance(ts, str):
                continue
            try:
                r["_t"] = datetime.fromisoformat(ts)   # parsed, not compared
            except ValueError:
                continue
            if r["_t"] < cutoff:
                continue
            first.setdefault((str(r["pair"]), str(r.get("gate") or "?")), r)
    out = []
    for key, r in first.items():
        t = r.get("_t")
        if t is None:      # panel400 rows still need their parse
            try:
                t = datetime.fromisoformat(r.get("ts"))
            except (TypeError, ValueError):
                continue
        out.append({"pair": key[0], "gate": key[1],
                    "ts": r.get("ts"), "quick": r.get("quick"), "t": t})
    return out, f"{len(first)} first-seen candidates ({window})"


def outcomes(cands: list[dict], hours: int, fee_rt: float) -> list[dict]:
    """Fill-modeled outcome per candidate (readonly OHLCV).

    The cache is HOURLY, so a burial lands mid-candle. The fill models
    the candle-boundary convention: ENTER at the burial candle's CLOSE
    (a real traded price, strictly post-burial — no pre-burial movement
    can leak into the window, the Copilot catch on the panel's
    last-close-before rule), then hunt the peak in the candles AFTER
    it. A window with fewer than `hours` candles (cache holes) is
    scored as no-coverage, never as a flat dud (the second Copilot
    catch: missing candles can hide the actual peak)."""
    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=2.0)
    try:
        for c in cands:
            t = c["t"]
            t_c = t.replace(minute=0, second=0, microsecond=0)
            entry_ms = int(t_c.timestamp() * 1000) + 3_600_000
            end_ms = entry_ms + hours * 3_600_000
            pre = con.execute(
                "SELECT close FROM ohlcv WHERE pair=? AND ts=?",
                (c["pair"], entry_ms - 3_600_000)).fetchone()
            if not pre or not pre[0]:
                c["run"] = c["fill"] = c["capture"] = None
                continue
            hi = con.execute(
                "SELECT MAX(high) FROM ohlcv WHERE pair=? AND ts>=? "
                "AND ts<?", (c["pair"], entry_ms, end_ms)).fetchone()
            n_c = con.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE pair=? AND ts>=? "
                "AND ts<?", (c["pair"], entry_ms, end_ms)).fetchone()[0]
            if not hi or not hi[0] or n_c < hours:
                c["run"] = c["fill"] = c["capture"] = None
                continue
            entry = float(pre[0])
            peak = (float(hi[0]) / entry - 1.0) * 100.0
            fill_entry = entry * (1.0 + SPREAD * 0.5)
            fill_exit = float(hi[0]) * (1.0 - SPREAD * 0.5)
            fill = (fill_exit / fill_entry - 1.0) * 100.0 - fee_rt
            c["run"] = round(peak, 2)
            c["fill"] = round(max(fill, 0.0), 2)
            c["capture"] = round(max(fill, 0.0) / peak, 2) if peak > 0 else 0.0
    finally:
        con.close()
    return cands


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=("48h", "7d", "panel400"),
                    default="48h",
                    help="candidate window: 48h = the live funnel "
                         "(default), 7d = the week, panel400 = the "
                         "panel's exact burial-hindsight recipe")
    ap.add_argument("--hours", type=int, default=7,
                    help="outcome horizon (default 7h)")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE_RT_PCT,
                    help="round-trip taker fee %% (default 0.52 = the bot's "
                         "real cost; the panel models 0.26 — ranking is "
                         "invariant, absolute EV is not)")
    args = ap.parse_args()

    cands, scope = load_candidates(args.window)
    cands = outcomes(cands, args.hours, args.fee)
    # CENSORING (the Copilot catch, boundary corrected): entry is the
    # burial candle's CLOSE (= t_c + 1h), so the window's LAST candle
    # starts at t_c + hours and only CLOSES at t_c + hours + 1 — before
    # that, scoring would lean on a partial final candle. Held out,
    # never half-scored.
    now = datetime.now().astimezone()
    scored, censored = [], 0
    for c in cands:
        if c.get("fill") is None:
            continue                      # no OHLCV coverage at all
        if now < c["t"].replace(minute=0, second=0, microsecond=0) \
                + timedelta(hours=args.hours + 1):
            censored += 1
            continue                      # the window's last candle is still open
        scored.append(c)

    by_gate: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "pay": 0, "pay_fills": [], "dud_fills": [],
                 "captures": [], "runs": []})
    for c in scored:
        g = by_gate[c["gate"]]
        g["n"] += 1
        g["runs"].append(c["run"])
        if c["run"] >= GOAL:
            g["pay"] += 1
            g["pay_fills"].append(c["fill"])
            g["captures"].append(c["capture"])
        else:
            g["dud_fills"].append(c["fill"])

    rows = []
    for gate, g in by_gate.items():
        rate = g["pay"] / g["n"]
        med_pay = (statistics.median(g["pay_fills"])
                   if g["pay_fills"] else DUD_ASSUMED)
        med_dud = (statistics.median(g["dud_fills"])
                   if g["dud_fills"] else DUD_ASSUMED)
        med_cap = (statistics.median(g["captures"])
                   if g["captures"] else 0.0)
        ev = rate * med_pay + (1 - rate) * med_dud
        rows.append({"gate": gate, "n": g["n"], "pay": g["pay"],
                     "rate": rate, "med_pay": med_pay, "med_dud": med_dud,
                     "med_cap": med_cap, "ev": ev})

    n_all = len(scored)
    pay_all = sum(r["pay"] for r in rows)
    caps = [c["capture"] for c in scored if c["run"] >= GOAL]
    med_cap_all = statistics.median(caps) if caps else 0.0
    fills_all = [c["fill"] for c in scored]
    med_fill_all = statistics.median(fills_all) if fills_all else 0.0
    rate_line = f"{pay_all / n_all:.0%}" if n_all else "n/a"

    fmt = ("{gate:<22} {n:>3} {pay:>4} {rate:>6.0%} {med_pay:>7.2f} "
           "{med_dud:>6.2f} {med_cap:>6.0%} {ev:>6.2f}")
    lines = [
        f"THE GATE EV TABLE — {datetime.now():%Y-%m-%d %H:%M}",
        "=" * 78,
        f"window: {scope} from popup_suppressions.jsonl, "
        f"outcome horizon {args.hours}h",
        f"fill model: entry mid+{SPREAD/2:.3%}, exit peak-{SPREAD/2:.3%}, "
        f"fee {args.fee:.2f}% round trip netted (the bot's real cost; "
        f"the panel models 0.26), floor 0 — capture floor "
        f"{FILL_CAPTURE_FLOOR:.0%}",
        f"candidates scored: {n_all} | payers (run>={GOAL}%): {pay_all} "
        f"({rate_line})",
        f"censored (horizon still open, held out): {censored}",
        f"median capture of payers: {med_cap_all:.0%} | "
        f"median fill-modeled run (all): {med_fill_all:+.2f}%",
        "",
        f"{'GATE':<22} {'n':>3} {'pay':>4} {'rate':>6} {'medfill*':>8} "
        f"{'dud-med':>7} {'cap':>6} {'EV%':>6}",
        "-" * 78,
    ]
    for r in sorted(rows, key=lambda r: (-r["ev"], -r["rate"], r["gate"])):
        lines.append(fmt.format(**r))
    lines += [
        "-" * 78,
        "* medfill = median fill-modeled run of the gate's PAYERS only "
        "(duds separately);",
        "  EV% = rate × medfill_pay + (1−rate) × dud-med — the worth of "
        "one buried",
        "  candidate if taken, net of fees, per $100. Ranked by EV.",
        "  EV ≤ 0: the burial was right (the run never beat the drag).",
        "  EV > 0 with low rate: selective relaxation territory "
        "(canary cohort first).",
    ]
    report = "\n".join(lines) + "\n"
    OUT.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
