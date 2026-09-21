"""diag_basegrade.py — THE FIXED-HOUR CLAIM, GRADED (Sep 19).

WHY THIS EXISTS: the old base call told her "give it about an hour to
base" — one number for 100+ coins with wildly different rhythms. The
Sep 19 rebuild replaced it with each coin's own book (coin_wiggle_profile:
median bars-to-base among recovered dips per depth bucket). This study
grades the RETIRED claim fleet-wide: replay every coin's 1h store with
the book's exact walk and price how wrong "about an hour" (60 min) was
per coin, in median base minutes.

METHOD — mirrors coin_wiggle_profile's walk exactly (validated against
the live book at boot): rolling 24h high, dip = (hh - close)/hh, dips
>= 0.5% only, a dip RECOVERS when a future high re-touches hh within
24 bars (one rotation), bars-to-base = first bar whose high >= hh,
minutes = bars * 60 (1h bars). The grade is honest about the universe:
recovered dips only — a dip that never based within the rotation is a
GET OUT case, not a base-time sample.

THE MONTHLY EVIDENCE DRIVER (Sep 20): the study re-prices itself every
month as the deep stores grow — a coin's median base time sharpens with
every recovered dip its store accrues, and the fleet verdict that set
the "about an hour" retirement deserves fresh evidence. Appends
_basegrade_monthly.jsonl (each month's record prices the stores AS THEY
STOOD); _basegrade_check.txt carries the full report. Read-only over
the deep stores; stdlib + pandas only.
Run: .venv\\Scripts\\python.exe -X utf8 diag_basegrade.py
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
STORES = BASE / "surething_data" / "kraken"
OUT = BASE / "_basegrade_check.txt"
LEDGER = BASE / "_basegrade_monthly.jsonl"
FIXED_CLAIM_MIN = 60.0          # the retired claim: "about an hour"
LOOKBACK_DAYS = 30              # WIGGLE_LOOKBACK_DAYS, mirrored
MIN_DIP_PCT = 0.5               # the book's dip threshold
THIN_N = 20                     # below this many samples: thin, medians unreliable
BUCKETS = ((0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0),
           (3.0, 4.5), (4.5, 100.0))   # the book's exact edges (drift-guarded)

# ── the monthly driver's clock (the family convention: one pass
# shortly after boot to seed the history, then monthly) ──
MONTHLY_PERIOD_S = 30 * 86400.0
FIRST_DELAY_S = 900.0            # first pass ~15 min after boot
logger = logging.getLogger("basegrade")


def bucket_of(dip: float):
    for lo, hi in BUCKETS:
        if lo <= dip < hi:
            return (lo, hi)
    return None


def walk_store(path: Path):
    """The book's walk on one store. Returns (raw_minutes, bucket_minutes,
    bucket_totals, bucket_rec4) — raw_minutes: one base-duration in minutes
    per recovered dip >= 0.5%; bucket_minutes: {bucket: [minutes]} for
    buckets with >= 5 samples; bucket_totals: {bucket: n} counting EVERY
    dip that deep (recovered or not) and bucket_rec4: {bucket: k} how
    many recovered within 4 bars — together the fleet's rec-rate leg for
    the outliver's fleet fallback. None when the store is unreadable or
    too thin to speak."""
    try:
        import pandas as pd
        df = pd.read_feather(path)
        col = ("date" if "date" in df.columns
               else ("time" if "time" in df.columns else df.columns[0]))
        df = df.sort_values(col)
        highs = df["high"].to_list()
        closes = df["close"].to_list()
    except Exception as exc:  # narrow: unreadable store -> skip, logged
        print(f"  skip {path.name}: {exc}")
        return None
    n = len(closes)
    span = min(n, LOOKBACK_DAYS * 24)
    raw, bmap = [], {}
    btot, brec4 = {}, {}   # every dip per bucket / recovered-within-4-bars
    for i in range(n - span, n - 25):
        if i < 24:
            continue
        hh = max(highs[i - 24:i])
        if hh <= 0:
            continue
        dip = (hh - closes[i]) / hh * 100.0
        if dip < MIN_DIP_PCT:
            continue
        b = bucket_of(dip)
        if b is not None:
            btot[b] = btot.get(b, 0) + 1
        if max(highs[i + 1:i + 25]) < hh:
            continue          # never based inside the rotation: not a sample
        bars = 24
        for _j, _h in enumerate(highs[i + 1:i + 25]):
            if _h >= hh:
                bars = _j + 1
                break
        minutes = bars * 60.0
        raw.append(minutes)
        if b is not None:
            if bars <= 4:     # back inside one rotation = the rec4 numerator
                brec4[b] = brec4.get(b, 0) + 1
            bmap.setdefault(b, []).append(minutes)
    if not raw:
        return None          # no recovered dips this window: the coin has no base-time book
    return raw, bmap, btot, brec4


def med(v):
    return statistics.median(v) if v else None


def run_study(stores_dir=None, anchor: bool = True) -> dict:
    """One full grade over the deep stores. Writes _basegrade_check.txt
    (the full report) and appends the month's summary to
    _basegrade_monthly.jsonl (the re-pricing history — each record
    prices the stores AS THEY STOOD). stores_dir/anchor let tests
    sandbox the walk and skip the live-book anchor. Returns the
    summary the ledger carries; never raises to the caller."""
    lines = []

    def say(s=""):
        lines.append(s)

    sdir = Path(stores_dir) if stores_dir is not None else STORES
    files = sorted(sdir.glob("*_USD-1h.feather"))
    say(f"THE FIXED-HOUR CLAIM, GRADED — 'about an hour' ({FIXED_CLAIM_MIN:.0f} min)"
        f" vs each coin's own median base time")
    say(f"universe: {len(files)} stores, last {LOOKBACK_DAYS} days, "
        f"recovered dips >= {MIN_DIP_PCT}% only (minutes = bars x 60)\n")

    # ── the anchor: the replay must agree with the LIVE book ──
    if anchor:
        try:
            import ast as _ast
            import background_scanner as bs
            for probe in ("BTC/USD", "ZEC/USD"):
                prof = bs.coin_wiggle_profile(probe)
                got = walk_store(bs._WIGGLE_DIR
                                 / f"{probe.split('/')[0]}_USD-1h.feather")
                if not prof or not got:
                    continue
                bmap = got[1]
                for bs_key, book_row in prof.get("buckets", {}).items():
                    lo, hi = _ast.literal_eval(str(bs_key))
                    if not (lo <= 0.75 < hi):   # anchor on the shallow bucket
                        continue
                    if book_row.get("bmed") is None or not bmap.get((lo, hi)):
                        continue
                    rep_h = med(bmap[(lo, hi)]) / 60.0
                    say(f"anchor {probe} [{lo}-{hi}): book bmed="
                        f"{book_row['bmed']}h (n={book_row['n']}) | "
                        f"replay={rep_h:.2f}h -> "
                        f"{'AGREE' if abs(rep_h - book_row['bmed']) < 0.01 else 'DIVERGE'}")
                    break
        except Exception as exc:  # narrow: anchor is validation, not load-bearing
            say(f"anchor skipped: {exc}\n")

    # ── the fleet walk ──
    rows, fleet_raw, bucket_pool = [], [], {}
    bucket_tot, bucket_rec4 = {}, {}   # pooled rec-rate legs (every dip)
    graded = skipped = 0
    for f in files:
        got = walk_store(f)
        if not got:
            skipped += 1
            continue
        raw, bmap, btot, brec4 = got
        graded += 1
        srt = sorted(raw)
        pair = f.name.replace("-1h.feather", "")
        rows.append({"pair": pair, "n": len(raw),
                     "med": med(raw), "p25": srt[len(srt) // 4],
                     "p75": srt[(3 * len(srt)) // 4]})
        fleet_raw.extend(raw)
        for b, v in bmap.items():
            bucket_pool.setdefault(b, []).extend(v)
        for b, k in btot.items():
            bucket_tot[b] = bucket_tot.get(b, 0) + k
        for b, k in brec4.items():
            bucket_rec4[b] = bucket_rec4.get(b, 0) + k

    say(f"graded {graded} coins ({skipped} skipped: thin/unreadable)\n")

    # ── the fleet verdict ──
    fm = med(fleet_raw) or 0.0
    late = sum(1 for m in fleet_raw if m > FIXED_CLAIM_MIN)
    fast = sum(1 for m in fleet_raw if m < FIXED_CLAIM_MIN / 2)
    n_samples = len(fleet_raw)
    say("FLEET VERDICT (all recovered dips pooled):")
    say(f"  fleet median base time: {fm:.0f} min vs the claim's 60 min "
        f"-> the claim was {'LATE' if fm > 60 else 'EARLY'} by "
        f"{abs(fm - 60):.0f} min at the median")
    say(f"  dips basing SLOWER than an hour: {late}/{n_samples} "
        f"({100.0 * late / n_samples if n_samples else 0.0:.0f}%) — times the fixed claim "
        f"said 'ready' while the coin was still basing")
    say(f"  dips basing under 30 min: {fast}/{n_samples} "
        f"({100.0 * fast / n_samples if n_samples else 0.0:.0f}%) — times an hour was "
        f"overcautious\n")

    # ── per depth bucket ──
    say("BY DIP DEPTH (fleet pooled):")
    bucket_summary = {}
    for b in BUCKETS:
        v = bucket_pool.get(b)
        if not v:
            continue
        _tot = bucket_tot.get(b, 0)
        _r4 = (bucket_rec4.get(b, 0) / _tot) if _tot else None
        bucket_summary[f"{b[0]}-{b[1]}"] = {
            "n": len(v), "med_min": round(med(v), 1),
            "rec4": (round(_r4, 3) if _r4 is not None else None)}
        say(f"  [{b[0]}-{b[1]}%): n={len(v):5d}  median "
            f"{med(v):6.0f} min  -> claim off by {med(v) - 60:+.0f} min  "
            f"rec4={(_r4 * 100.0 if _r4 is not None else 0.0):.0f}% "
            f"of {bucket_tot.get(b, 0)} dips")
    say()

    # ── the per-coin table: where one hour was most wrong ──
    solid = [r for r in rows if r["n"] >= THIN_N]
    thin = [r for r in rows if r["n"] < THIN_N]
    solid.sort(key=lambda r: abs(r["med"] - FIXED_CLAIM_MIN), reverse=True)
    say(f"PER-COIN MEDIAN BASE TIME ({len(solid)} coins with >= {THIN_N} "
        f"recovered dips; {len(thin)} thin, unlisted):")
    say(f"  {'coin':10s} {'n':>5s} {'p25':>6s} {'median':>7s} {'p75':>6s}  verdict")
    for r in solid[:20]:
        d = r["med"] - FIXED_CLAIM_MIN
        tag = ("an hour is LATE" if d > 15
               else ("an hour is EARLY" if d < -15 else "an hour is fair"))
        say(f"  {r['pair']:10s} {r['n']:5d} {r['p25']:6.0f} {r['med']:7.0f} "
            f"{r['p75']:6.0f}  {d:+5.0f} min  {tag}")
    if thin:
        ex = ", ".join(r["pair"] for r in sorted(thin, key=lambda r: -r["n"])[:6])
        say(f"  thin (medians unreliable): {ex} ...")

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stores_dir": sdir.name,
        "coins_total": len(files), "graded": graded, "skipped": skipped,
        "samples": n_samples, "claim_min": FIXED_CLAIM_MIN,
        "fleet_median_min": round(fm, 1),
        "late_n": late,
        "late_pct": round(100.0 * late / n_samples, 1) if n_samples else 0.0,
        "fast_n": fast,
        "fast_pct": round(100.0 * fast / n_samples, 1) if n_samples else 0.0,
        "buckets": bucket_summary,
        "solid_coins": len(solid), "thin_coins": len(thin),
        "top_offenders": [{
            "pair": r["pair"], "n": r["n"], "med_min": round(r["med"], 1),
            "p25": round(r["p25"], 1), "p75": round(r["p75"], 1),
            "delta_min": round(r["med"] - FIXED_CLAIM_MIN, 1)}
            for r in solid[:8]],
    }
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("basegrade txt write failed (%s) — the ledger "
                       "still carries the verdict", exc)
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("basegrade ledger append failed (%s) — the txt "
                       "still carries the verdict", exc)
    return summary


def main() -> int:
    s = run_study()
    print(f"wrote {OUT.name}: {s['graded']}/{s['coins_total']} coins graded, "
          f"fleet median {s['fleet_median_min']:.0f} min vs the claim's "
          f"{s['claim_min']:.0f} ({s['samples']} samples)")
    return 0


def _read_ledger() -> list:
    try:
        return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8")
                .splitlines() if l.strip()]
    except Exception:
        return []


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one pass shortly after boot (the ledger record
    seeds the history), then monthly. Never raises; dies with the
    scanner. The append-only ledger (_basegrade_monthly.jsonl) is the
    re-pricing history — each month's record prices the deep stores AS
    THEY STOOD, so the fleet verdict sharpens as the stores accrue
    recovered dips."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("BASE-GRADE: monthly re-price — %d/%d coins graded, "
                        "fleet median %.0f min vs the claim's %.0f "
                        "(%d samples)",
                        s["graded"], s["coins_total"],
                        s["fleet_median_min"], s["claim_min"], s["samples"])
        except Exception as exc:
            logger.info("BASE-GRADE: pass failed (%s) — this month's "
                        "re-price skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    raise SystemExit(main())
