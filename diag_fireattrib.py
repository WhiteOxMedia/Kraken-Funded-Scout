"""diag_fireattrib.py — THE FIRE'S SOURCE, PRICED (Sep 17).

WHY THIS EXISTS: the marginal fires now name their mechanism at
fire-time (the MARGINAL FIRE log line; the snapshot's `unlock` stamp),
but a name is not a price. This study joins attributed fires with
their forward outcomes through the deep candle store and reports, per
mechanism — the clamp fix (the personal OUT bar's [80,86) band), the
per-coin score floors, the tide memory, and the defer pass — fires,
run rate (+2% within 4 bars of the fire), and DOLLARS (the fleet
convention: full position 1055 x brain_size_mult x move/100, ran
capped at the goal's ceiling).

Appends _fireattrib_monthly.jsonl (each month's record prices the
attribution as it stood) and writes _fireattrib.txt. Hermetic-friendly:
every path is a parameter (the pins sandbox world and ledger).
"""
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAPSHOTS = ROOT / "signal_snapshots.jsonl"
LEDGER = ROOT / "_fireattrib_monthly.jsonl"
REPORT = ROOT / "_fireattrib.txt"
MONTHLY_PERIOD_S = 30 * 24 * 3600.0
FIRST_DELAY_S = 1500.0           # ~25 min after boot, after the store extender

FULL_POS = 1055.0                # the fleet convention's full position


def _mult_of(rec: dict) -> float:
    m = rec.get("mult")
    try:
        v = float(m)
        return v if v > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def price_fires(rows: list[dict], forward_fn) -> dict:
    """Price attributed fires. rows: fired snapshots carrying `unlock`
    stamps; forward_fn(stamp, pair, price) -> {ran, end_pct} | None.
    Dollars: ran -> goal-ceiling (+2%, the popup contract); else the
    end-move, sized by the fire's own brain mult. Fires with no forward
    outcome (thin store / too recent) are counted and skipped honestly."""
    per = {}
    skipped = 0
    for rec in rows:
        att = rec.get("unlock") or {}
        src = att.get("source") or "none"
        bucket = per.setdefault(
            src, {"fires": 0, "graded": 0, "ran": 0,
                  "usd": 0.0, "mfe_usd": 0.0})
        bucket["fires"] += 1
        out = forward_fn(rec.get("ts") or "", rec.get("pair") or "",
                         rec.get("entry"))
        if out is None:
            skipped += 1
            continue
        bucket["graded"] += 1
        if out.get("ran"):
            bucket["ran"] += 1
            bucket["mfe_usd"] += FULL_POS * _mult_of(rec) * 0.02
        bucket["usd"] += (FULL_POS * _mult_of(rec) * 0.02
                          if out.get("ran")
                          else FULL_POS * _mult_of(rec)
                          * float(out.get("end_pct") or 0.0) / 100.0)
    return {"per_source": per, "skipped": skipped,
            "total_fires": len(rows)}


def run_study(snapshots_path: Path = SNAPSHOTS,
              ledger_path: Path = LEDGER,
              report_path: Path = REPORT) -> dict:
    """One pass: load attributed fires, price them, append the monthly
    ledger, write the report. Never raises (driver contract)."""
    import tidebar_weekly as tbw

    rows = []
    cutoff = time.time() - 45 * 24 * 3600
    try:
        for line in snapshots_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("kind") != "fired" or "unlock" not in rec:
                continue
            ts = rec.get("ts")
            try:
                import pandas as _pd
                if _pd.Timestamp(ts).timestamp() < cutoff:
                    continue
            except Exception:
                continue
            rows.append(rec)
    except OSError:
        pass

    def _fwd(stamp, pair, price):
        return tbw._verdict_forward(stamp, pair, bars=4,
                                    price=price if price else None)

    res = price_fires(rows, _fwd)
    res["generated_at"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())

    lines = ["THE FIRE'S SOURCE — which mechanism is paying? (Sep 17)",
             f"generated {res['generated_at']} | fires {res['total_fires']}"
             f" | ungradeable {res['skipped']} (thin store / too recent)",
             "-" * 64]
    per = res["per_source"]
    order = sorted(per, key=lambda k: -per[k]["usd"])
    for src in order:
        b = per[src]
        rate = (f"{b['ran']}/{b['graded']}"
                if b["graded"] else "0/0")
        lines.append(
            f"  {src:<28} fires {b['fires']:>3}  ran {rate:<7} "
            f"${b['usd']:>8.2f}  (mfe ${b['mfe_usd']:>8.2f})")
    if not per:
        lines.append("  (no attributed fires yet — the stamps accrue "
                     "from the Sep 17 generation on)")
    lines.append("-" * 64)
    lines.append("  unlock_pts = the points the mechanism handed the "
                 "fire; $ = full")
    lines.append("  position x brain mult x move; ran = +2% within 4 bars.")
    try:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass

    try:
        prev = []
        if ledger_path.exists():
            prev = [l for l in ledger_path.read_text(encoding="utf-8")
                    .splitlines() if l.strip()]
        prev.append(json.dumps(res, default=str))
        ledger_path.write_text("\n".join(prev[-60:]) + "\n",
                               encoding="utf-8")
    except OSError:
        pass
    return res


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S,
                        logger=None):
    """Daemon: one pass shortly after boot (seeding the history), then
    monthly. Never raises; dies with the scanner. `logger` is the
    scanner's logger (passed by the boot registration) so the pass's
    verdict lands in scanner.log."""
    import logging as _log
    lg = logger or _log.getLogger("fireattrib")
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            n_att = sum(b["fires"] for src, b in s["per_source"].items()
                        if src != "none")
            lg.info("FIRE-ATTRIB: monthly re-price — %d attributed fires, "
                    "$%.2f across mechanisms", n_att,
                    sum(b["usd"] for b in s["per_source"].values()))
        except Exception as exc:
            lg.info("FIRE-ATTRIB: pass failed (%s) — the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    r = run_study()
    print(REPORT.read_text(encoding="utf-8"))
