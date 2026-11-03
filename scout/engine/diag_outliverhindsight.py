"""diag_outliverhindsight.py — THE OUTLIVER'S HINDSIGHT (Sep 20).

WHY THIS EXISTS: the rotation-outliver gate (Sep 19) fires the early
Get Out when a coin's own book says dips this deep usually miss the
rotation AND the median base time outlasts the minutes left. Her
question: "did the early Get Outs save or cost versus holding to the
boundary, in dollars?" Every fire is journaled at decision time
(outliver_fires.jsonl — once per rotation boundary per pair; the 60s
repeats are hearings, not decisions). This study prices each fire:

  REALIZED   — exit at the fire price.
  COUNTERFACTUAL — hold to the rotation boundary the gate judged
  (fire ts + remaining_min), priced on the pair's 1h store.

  saved_usd = (fire_price - boundary_close) x (size / fire_price)
  > 0 the early exit SAVED dollars; < 0 it COST (the rebound came).

Also graded: the book's own thesis — did price re-touch the pre-dip
high within 24 bars after the fire (the book predicted it would NOT)?

THE MONTHLY EVIDENCE DRIVER: re-prices monthly as fires accumulate
(the journal accrues only when the gate fires — a $0 month stays
comparable with the month the gate finally speaks). Appends
_outliverhindsight_monthly.jsonl; _outliverhindsight.txt carries the
full report. Read-only over the deep stores; stdlib + pandas only.
Run: .venv\\Scripts\\python.exe -X utf8 diag_outliverhindsight.py
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
FIRES_FILE = BASE / "outliver_fires.jsonl"
OUT = BASE / "_outliverhindsight.txt"
LEDGER = BASE / "_outliverhindsight_monthly.jsonl"

RETOUCH_BARS = 24        # the book's own recovery window (one rotation)

MONTHLY_PERIOD_S = 30 * 86400.0
FIRST_DELAY_S = 900.0            # first pass ~15 min after boot
logger = logging.getLogger("outliverhindsight")


def _read_fires(path: Path = FIRES_FILE) -> list:
    """The journaled fires, oldest first; junk lines skipped."""
    rows = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("pair"):
            rows.append(d)
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    return rows


def _load_store(path: Path):
    """(highs, closes, epochs) from a 1h feather store; raises when
    unreadable — the caller skips. Epochs are true UTC seconds,
    computed by delta from the epoch (unit-proof across pandas
    datetime64 resolutions)."""
    import pandas as pd
    df = pd.read_feather(path)
    col = ("date" if "date" in df.columns
           else ("time" if "time" in df.columns else df.columns[0]))
    df = df.sort_values(col)
    ts = pd.to_datetime(df[col])
    if getattr(ts.dt, "tz", None) is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")
    delta = ts - pd.Timestamp("1970-01-01", tz="UTC")
    ep = delta.dt.total_seconds().to_numpy().astype("int64")
    return (df["high"].to_list(), df["close"].to_list(), ep)


def _fire_epoch(ts_iso: str) -> float:
    from datetime import datetime as _dt
    t = _dt.fromisoformat(str(ts_iso))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def grade_fire(rec: dict, highs, closes, ep, now: float | None = None) -> dict:
    """One fire's hindsight against its own store. Returns a row:
    state graded | pending (boundary in the future) | skipped (no bar
    at the fire hour). Pure over its inputs."""
    import numpy as np
    now = now if now is not None else _fire_epoch(
        datetime.now(timezone.utc).isoformat())
    t0 = _fire_epoch(rec["ts"])
    live = float(rec.get("live") or 0.0)
    size = float(rec.get("size") or 0.0)
    i = int(np.searchsorted(ep, t0, side="right")) - 1
    if i < 0 or abs(int(ep[i]) - t0) > 3600:
        return {"pair": rec.get("pair"), "ts": rec.get("ts"),
                "state": "skipped", "reason": "no bar at the fire hour"}
    fire_px = float(closes[i])
    units = (size / live) if live > 0 else 0.0
    b_ts = t0 + float(rec.get("remaining_min") or 0.0) * 60.0
    out = {"pair": rec.get("pair"), "ts": rec.get("ts"),
           "dip_pct": rec.get("dip_pct"), "bucket": rec.get("bucket"),
           "usd_at_fire": rec.get("usd"), "fire_px": round(fire_px, 8),
           "units": round(units, 6)}
    # the book's thesis: a re-touch of the pre-dip high within 24 bars
    retouch = False
    if i + 1 < len(highs):
        retouch = max(highs[i + 1:i + 1 + RETOUCH_BARS],
                      default=0.0) >= float(rec.get("high") or 0.0) > 0
    out["retouched"] = bool(retouch)
    if b_ts > now:
        out.update({"state": "pending",
                    "reason": "the judged boundary has not closed yet"})
        return out
    j = int(np.searchsorted(ep, b_ts, side="right")) - 1
    if j < i or j >= len(closes):
        out.update({"state": "skipped",
                    "reason": "store too thin to price the boundary"})
        return out
    b_px = float(closes[j])
    saved = (fire_px - b_px) * units
    pct = (b_px / fire_px - 1.0) * 100.0 if fire_px > 0 else 0.0
    out.update({"state": "graded", "boundary_px": round(b_px, 8),
                "move_pct": round(pct, 3),
                "saved_usd": round(saved, 2),
                "verdict": ("SAVED" if saved > 0
                            else ("COST" if saved < 0 else "WASH"))})
    return out


def run_study(stores_dir=None, fires_path=None, now=None) -> dict:
    """One full hindsight pass over the fire journal. Writes
    _outliverhindsight.txt and appends _outliverhindsight_monthly.jsonl
    (each month's record prices the fires AS THEY STOOD). Returns the
    summary; never raises to the caller."""
    lines = []

    def say(s=""):
        lines.append(s)

    sdir = Path(stores_dir) if stores_dir is not None else STORES
    fires = _read_fires(Path(fires_path) if fires_path is not None
                        else FIRES_FILE)
    now = now if now is not None else _fire_epoch(
        datetime.now(timezone.utc).isoformat())
    say("THE OUTLIVER'S HINDSIGHT — did the early Get Out beat holding "
        "to the boundary?")
    say(f"universe: {len(fires)} journaled fire(s)\n")
    graded, pending, skipped = [], 0, 0
    for rec in fires:
        base = str(rec.get("pair") or "").split("/")[0]
        sp = sdir / f"{base}_USD-1h.feather"
        try:
            highs, closes, ep = _load_store(sp)
        except Exception:
            skipped += 1
            continue
        row = grade_fire(rec, highs, closes, ep, now=now)
        if row["state"] == "graded":
            graded.append(row)
        elif row["state"] == "pending":
            pending += 1
        else:
            skipped += 1
    saved = round(sum(r["saved_usd"] for r in graded), 2)
    n_saved = sum(1 for r in graded if r["saved_usd"] > 0)
    n_cost = sum(1 for r in graded if r["saved_usd"] < 0)
    n_retouch = sum(1 for r in graded if r.get("retouched"))
    net = saved
    # THE SAMPLE RUNG (Sep 20): the SAVED/COST reading can move the
    # rotation-outliver gate's confidence, so it answers to the
    # ladder's SAMPLE_BARS['OUTLIVER_HINDSIGHT'] rung. Below the bar
    # the per-fire rows and the net still render (they are the
    # record), but the verdict names its thinness — one deep dip
    # cannot declare the whole gate saved or broke.
    try:
        from evidence_ladder import SAMPLE_BARS as _sb, \
            samples_mature as _sm
        _rung, _sm_ok = _sb["OUTLIVER_HINDSIGHT"], \
            _sm("OUTLIVER_HINDSIGHT", len(graded))
    except Exception:
        _rung, _sm_ok = 8, len(graded) >= 8
    say("FLEET VERDICT (exit-at-fire vs hold-to-boundary, per fire):")
    say(f"  graded {len(graded)} · pending {pending} · skipped {skipped} "
        f"(no store / no bar)")
    if _sm_ok:
        say(f"  net: ${net:+,.2f} — the early exits "
            f"{'SAVED' if net > 0 else ('COST' if net < 0 else 'WASHED')} "
            f"versus holding to the boundary")
    else:
        say(f"  net: ${net:+,.2f} — TOO THIN TO VERDICT: {len(graded)} "
            f"graded fire(s), the ladder's OUTLIVER_HINDSIGHT rung "
            f"needs {_rung} (the rows below are the record; the "
            "SAVED/COST verdict waits for the sample)")
    say(f"  fires that saved: {n_saved} · fires that cost: {n_cost} · "
        f"the book's re-touch thesis held in "
        f"{len(graded) - n_retouch}/{len(graded)} graded fire(s)")
    for r in graded:
        say(f"    {str(r['pair']):12s} {str(r['ts'])[:16]} "
            f"{r['dip_pct']}% deep -> boundary {r['move_pct']:+.2f}% "
            f"= ${r['saved_usd']:+,.2f} "
            f"({r['verdict']}{' — RE-TOUCHED' if r.get('retouched') else ''})")
    if not fires:
        say("  the gate has not fired yet — the journal accrues when it "
            "does, and this study re-prices monthly.")
    say()
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fires_total": len(fires), "graded": len(graded),
        "pending": pending, "skipped": skipped,
        "saved_usd": round(sum(r["saved_usd"] for r in graded
                               if r["saved_usd"] > 0), 2),
        "cost_usd": round(sum(-r["saved_usd"] for r in graded
                              if r["saved_usd"] < 0), 2),
        "net_usd": net, "n_saved": n_saved, "n_cost": n_cost,
        "n_retouch": n_retouch,
        "rows": [{k: r[k] for k in ("pair", "ts", "dip_pct", "bucket",
                                    "fire_px", "boundary_px", "move_pct",
                                    "saved_usd", "verdict", "retouched")}
                 for r in graded],
    }
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("outliver hindsight txt write failed (%s) — the "
                       "ledger still carries the verdict", exc)
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("outliver hindsight ledger append failed (%s) — "
                       "the txt still carries the verdict", exc)
    return summary


def main() -> int:
    s = run_study()
    print(f"wrote {OUT.name}: {s['graded']}/{s['fires_total']} fire(s) "
          f"graded — net ${s['net_usd']:+,.2f} vs holding to the boundary")
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
    scanner. The append-only ledger (_outliverhindsight_monthly.jsonl)
    is the re-pricing history — each month's record prices the fires AS
    THEY STOOD, and pending fires flip to graded as their boundaries
    close."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("OUTLIVER HINDSIGHT: monthly re-price — %d/%d "
                        "fire(s) graded, net %+.2f USD vs holding to the "
                        "boundary", s["graded"], s["fires_total"],
                        s["net_usd"])
        except Exception as exc:
            logger.info("OUTLIVER HINDSIGHT: pass failed (%s) — this "
                        "month's re-price skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    raise SystemExit(main())
