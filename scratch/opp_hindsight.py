"""CHAMPION-OPPORTUNISTIC HINDSIGHT (Oct 2, the accumulation study): the
opp Hail Mary door (121b62c, joined by the QC in 11b6824) fires popups
stamped route="champion-opportunistic" into signal_snapshots.jsonl —
this study grades that class ONCE OUTCOMES ACCUMULATE and prints a
KEEP/KILL recommendation under rules fixed BEFORE the data arrived.

  · fires     = fired snapshots by route (champion-opportunistic vs its
    classic siblings: champion, champion-fast — the door shares the
    rotation slot with them, so they are the comparison cohort),
  · outcome   = the SAME fill model every hindsight tool speaks
    (gate_ev_table / the panel): entry at the fire candle's CLOSE, peak
    of the candles strictly after it within the fire's OWN window clock
    (win_min — each class graded on the clock it actually trades),
    spread + 0.52% RT taker fee netted, floored at 0,
  · payer     = the fill-modeled run reached the fire's OWN stamped goal
    (goal_pct, default 1.5) — a fire graded against what it promised,
  · EV        = payer_rate × medfill_pay + (1 − payer_rate) × med_dud —
    the decision-value blend, % net per fire; at the $300 hard cap the
    $-per-fire read is EV × 3.

THE PRE-REGISTERED VERDICT (fixed Oct 2, before any opp fire exists —
the rules do not move to fit the data):
  KEEP-PROVISIONAL  n_opp < 10 graded fires (cold start included):
                    the door stays open while the ledger accumulates —
                    a strict-capped mode is cheap to be wrong about.
  KILL              n_opp >= 10 AND (EV <= 0, or payer_rate == 0 with
                    n >= 20): a mode that cannot beat fees — or never
                    pays across 20 tries — is not opportunism, it is
                    donation.
  RETAIN-TIGHTEN    EV > 0 but < half the classic cohort's EV (classic
                    n >= 5): the door earns its slot only while it is
                    not clearly worse than the champion bar it shares
                    with; tighten (raise the floor, trim the cap).
  KEEP              everything else with EV > 0.

Caveats carried from the EV tool: hourly cache granularity under-
resolves a 1h window's peak (the peak is ONE candle's high — the real
exit watch would have sold differently); EV is decision-value, not a
textbook expectation; fires whose window has not fully elapsed are
CENSORED, never half-scored.

Read-only: opens ohlcv_cache.db in URI readonly mode, never writes
anything but its own report. Scout-side only.

Usage: python scratch/opp_hindsight.py [--fee 0.52]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SCOUT = Path(__file__).resolve().parent.parent


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
DEFAULT_FEE_RT_PCT = 0.52   # the bot's real taker RT (fills.py)

SNAPS = SCOUT / "signal_snapshots.jsonl"
DB = SCOUT / "ohlcv_cache.db"
OUT = SCOUT / "scratch" / "opp_hindsight.txt"

OPP_ROUTE = "champion-opportunistic"
CLASSIC_ROUTES = ("champion", "champion-fast")
MIN_GOAL = 1.0        # a degenerate 0-goal never grades as a payer
THIN_N = 10           # the pre-registered thin-data bound
DONATION_N = 20       # the payer-rate kill bound
CLASSIC_FLOOR_N = 5   # classic cohort size before its EV is a yardstick
REL_KEEP = 0.5        # opp EV must clear half the classic EV to KEEP


def load_fires() -> tuple[list[dict], dict[str, int]]:
    """Fired snapshots by route, timestamps parsed (never compared as
    strings — mixed offsets misorder)."""
    rows: list[dict] = []
    census: dict[str, int] = defaultdict(int)
    for line in SNAPS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "fired":
            continue
        route = str(r.get("route") or "?")
        census[route] += 1
        if route not in (OPP_ROUTE,) + CLASSIC_ROUTES:
            continue
        try:
            r["_t"] = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        rows.append(r)
    return rows, dict(census)


def outcomes(fires: list[dict], fee_rt: float) -> list[dict]:
    """Fill-modeled outcome per fire over its OWN window clock.

    The cache is HOURLY: entry at the fire candle's CLOSE (strictly
    post-fire — no pre-fire movement leaks in), peak over the candles
    that open strictly after the entry AND CLOSE WITHIN the window
    (win_min, used in minutes — the Copilot P2 catch: rounding up to
    whole hours graded a 90-min fire on a second candle whose HIGH
    could land past the window's promise; a candle that has not closed
    inside the window is invisible at this resolution, the SAME
    blindness for every class, so the comparison stays fair and
    conservative)."""
    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=2.0)
    try:
        for f in fires:
            win_ms = int(round(float(f.get("win_min") or 60.0) * 60_000))
            t_c = f["_t"].replace(minute=0, second=0, microsecond=0)
            entry_ms = int(t_c.timestamp() * 1000) + 3_600_000
            end_ms = entry_ms + win_ms
            last_full_ms = end_ms - 3_600_000   # a candle here closes by end_ms
            pre = con.execute(
                "SELECT close FROM ohlcv WHERE pair=? AND ts=?",
                (f.get("pair"), entry_ms - 3_600_000)).fetchone()
            if not pre or not pre[0]:
                f["fill"] = None
                continue
            hi = con.execute(
                "SELECT MAX(high) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
                (f.get("pair"), entry_ms, last_full_ms)).fetchone()
            n_c = con.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
                (f.get("pair"), entry_ms, last_full_ms)).fetchone()[0]
            if not hi or not hi[0] or n_c < 1:
                f["fill"] = None
                continue
            entry = float(pre[0])
            peak = (float(hi[0]) / entry - 1.0) * 100.0
            fill_entry = entry * (1.0 + SPREAD * 0.5)
            fill_exit = float(hi[0]) * (1.0 - SPREAD * 0.5)
            fill = (fill_exit / fill_entry - 1.0) * 100.0 - fee_rt
            f["run"] = round(peak, 2)
            f["fill"] = round(max(fill, 0.0), 2)
            f["goal"] = max(float(f.get("goal_pct") or 1.5), MIN_GOAL)
    finally:
        con.close()
    return fires


def grade(fires: list[dict]) -> dict:
    """Class stats on the fully-elapsed fires only."""
    g = {"n": 0, "pay": 0, "pay_fills": [], "dud_fills": [], "spends": []}
    for f in fires:
        if f.get("fill") is None:
            continue
        g["n"] += 1
        if f.get("spend") is not None:
            g["spends"].append(float(f["spend"]))
        if f["run"] >= f.get("goal", MIN_GOAL):
            g["pay"] += 1
            g["pay_fills"].append(f["fill"])
        else:
            g["dud_fills"].append(f["fill"])
    g["rate"] = g["pay"] / g["n"] if g["n"] else 0.0
    g["med_pay"] = statistics.median(g["pay_fills"]) if g["pay_fills"] else 0.0
    g["med_dud"] = statistics.median(g["dud_fills"]) if g["dud_fills"] else 0.0
    g["ev"] = g["rate"] * g["med_pay"] + (1 - g["rate"]) * g["med_dud"]
    g["med_spend"] = statistics.median(g["spends"]) if g["spends"] else 0.0
    return g


def verdict(opp: dict, classic: dict) -> tuple[str, str]:
    """THE PRE-REGISTERED MATRIX — see the module docstring. The rules
    were fixed Oct 2, before the class's first fire; they do not move."""
    n, ev, rate = opp["n"], opp["ev"], opp["rate"]
    if n < THIN_N:
        if n == 0:
            return ("KEEP-PROVISIONAL",
                    f"cold start — no {OPP_ROUTE} fire graded yet; the door "
                    f"stays open while the ledger accumulates "
                    f"(re-run after every 10 new fires or 7 days)")
        return ("KEEP-PROVISIONAL",
                f"too thin ({n} graded fire(s) < {THIN_N}) — nothing to act "
                f"on yet; re-run as fires accumulate")
    if ev <= 0 or (rate == 0.0 and n >= DONATION_N):
        why = f"EV {ev:+.2f}% <= 0" if ev <= 0 else \
              f"0 payers across {n} fires"
        return ("KILL",
                f"{why} — a strict-capped mode that cannot beat the drag "
                f"is donation, not opportunism (close the door, keep the "
                f"classic bar)")
    if classic["n"] >= CLASSIC_FLOOR_N and classic["ev"] > 0 \
            and ev < REL_KEEP * classic["ev"]:
        return ("RETAIN-TIGHTEN",
                f"EV {ev:+.2f}% > 0 but under {REL_KEEP:.0%} of the classic "
                f"cohort's {classic['ev']:+.2f}% — the door earns its slot "
                f"only while it is not clearly worse than the champion bar "
                f"it shares with; tighten (raise the floor, trim the cap)")
    return ("KEEP",
            f"EV {ev:+.2f}% with payer rate {rate:.0%} over {n} fires — "
            f"viable upside at a strict cap, exactly the mode's job")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE_RT_PCT,
                    help="round-trip taker fee %% (default 0.52)")
    args = ap.parse_args()

    fires, census = load_fires()
    fires = outcomes(fires, args.fee)
    now = datetime.now().astimezone()
    scored, censored, nocov = [], 0, 0
    for f in fires:
        if f.get("fill") is None:
            nocov += 1
            continue
        # CENSOR: the last full candle inside the window closes at most
        # one hour past the window's end — until then the grade could
        # still change (the EV tool's never-half-scored rule).
        if now < f["_t"] + timedelta(
                minutes=float(f.get("win_min") or 60.0), hours=1):
            censored += 1
            continue
        scored.append(f)

    opp = grade([f for f in scored if f.get("route") == OPP_ROUTE])
    classic = grade([f for f in scored if f.get("route") in CLASSIC_ROUTES])
    verdict_txt, verdict_why = verdict(opp, classic)

    def _cls_row(name: str, g: dict) -> str:
        return (f"  {name:<24} n={g['n']:<3} payers={g['pay']:<3} "
                f"rate={g['rate']:.0%}  medfill_pay={g['med_pay']:+.2f}%  "
                f"dud-med={g['med_dud']:+.2f}%  EV={g['ev']:+.2f}%")

    lines = [
        f"THE CHAMPION-OPPORTUNISTIC HINDSIGHT — {datetime.now():%Y-%m-%d %H:%M}",
        "=" * 78,
        f"ledger: signal_snapshots.jsonl (fired popups) | fill model: entry "
        f"fire-candle close, peak over the full candles INSIDE the fire's "
        f"own win_min (a candle counts only if it CLOSES within the window), "
        f"spread "
        f"±{SPREAD / 2:.3%}, fee {args.fee:.2f}% RT netted, floor 0 | payer "
        f"= the fire's own goal_pct",
        f"fire census (all routes, ledger-wide): " + ", ".join(
            f"{k}={v}" for k, v in sorted(census.items())),
        f"graded (window fully elapsed): {len(scored)} | censored (window "
        f"still open): {censored} | no OHLCV coverage: {nocov}",
        "",
        "CLASS GRADES",
        _cls_row(OPP_ROUTE, opp),
        _cls_row("classic siblings (combined)", classic),
        f"  (classic median spend ${classic['med_spend']:,.0f} vs the opp "
        f"hard cap $300 — the mode's strictness, on its own fires)",
        "",
        "THE VERDICT",
        f"  >>> {verdict_txt}",
        f"  {verdict_why}",
        "  (rules pre-registered Oct 2 before the class's first fire: "
        "<10 graded = provisional; EV<=0 or 0-payers-at-n>=20 = KILL; "
        "EV>0 but <50% of classic's = RETAIN-TIGHTEN; else KEEP)",
        "",
        "* hourly-cache caveat: a 60-min window is ONE candle's high — the",
        "  real exit watch (trend sell, profit guard, the 2% hard stop on",
        "  hm_mode rows) would have exited differently. EV is decision-",
        "  value, not a textbook expectation. At the $300 cap, $-per-fire",
        f"  = EV x 3 = ${opp['ev'] * 3:+.2f}.",
    ]
    report = "\n".join(lines) + "\n"
    OUT.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
