"""diag_climbouthindsight.py — THE CLIMB-OUT BRIDGE'S HINDSIGHT (Sep 20).

WHY THIS EXISTS: the climb-out bridge (Sep 19, user: "Thin book = low
risk, low reward but a good 'Climb out' candidate") re-evaluates rejected
thin-book candidates on BOUNCE merit and seeds them into the bounce
bracket — when one fires, _climbout_credit stamps the fire into
_climbout.jsonl. A fire is a claim: "this thin book will pay." This
study prices that claim in dollars by joining every fire to its SOLD
close in contest_trades.jsonl:

  fire (pair, fired_at)  ->  the pair's first close with sold_at >=
  fired_at (closes consumed in order, so a repeat fire waits for its
  own close). The bridge's dollars are the realized pnl_net of its
  joined closes; a fire with no close yet stays PENDING — never priced
  before it exists. A close BEFORE the fire is a different trade and
  never joins (the fire cannot claim a trade it did not open).

Family conventions (diag_outliverhindsight shape): run_study() core,
_climbouthindsight.txt report, _climbouthindsight_monthly.jsonl
append-only re-pricing ledger, monthly_driver_loop daemon (never
raises; dies with the scanner). Hermetic: every path is a module
constant the suite rebinds — no store IO, no production reads.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

FIRST_DELAY_S = 15 * 60 + 25      # family stagger: just after the outliver's
MONTHLY_PERIOD_S = 30 * 24 * 3600.0

FIRES_FILE = Path(__file__).parent / "_climbout.jsonl"
TRADES_FILE = Path(__file__).parent / "contest_trades.jsonl"
LEDGER = Path(__file__).parent / "_climbouthindsight_monthly.jsonl"
OUT = Path(__file__).parent / "_climbouthindsight.txt"


def _say(line: str, lines: list[str]) -> None:
    lines.append(line)


def _read_jsonl(path: Path) -> list[dict]:
    """Every parseable dict row; junk lines never kill the tail."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for ln in raw:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _ts(v) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v))
        if t.tzinfo is None:
            t = t.astimezone()
        return t
    except (ValueError, TypeError):
        return None


def join_fires_to_closes(fires: list[dict], closes: list[dict]) -> list[dict]:
    """The join: per pair, fires in fire order, closes in close order;
    a fire takes the pair's first close AT OR AFTER its fired_at that no
    earlier fire of that pair consumed. Returns one row per fire:
    {pair, fired_at, sold_at, pnl_net, held_min, goal_pct, verdict} —
    verdict None = pending (no close yet)."""
    out = []
    by_pair_fires: dict[str, list[dict]] = {}
    for f in fires:
        pair = str(f.get("pair") or "")
        ft = _ts(f.get("fired_at"))
        if not pair or ft is None:
            continue
        by_pair_fires.setdefault(pair, []).append((ft, f))
    by_pair_closes: dict[str, list[dict]] = {}
    for c in closes:
        pair = str(c.get("pair") or "")
        st = _ts(c.get("sold_at"))
        if not pair or st is None:
            continue
        by_pair_closes.setdefault(pair, []).append((st, c))
    for pair in sorted(by_pair_fires):
        fts = sorted(by_pair_closes.get(pair, []), key=lambda t: t[0])
        used = [False] * len(fts)
        for ft, _f in sorted(by_pair_fires[pair], key=lambda t: t[0]):
            row = {"pair": pair, "fired_at": ft.isoformat(),
                   "sold_at": None, "pnl_net": None, "held_min": None,
                   "goal_pct": None, "verdict": None}
            for i, (st, c) in enumerate(fts):
                if used[i] or st < ft:
                    continue
                used[i] = True
                row.update({
                    "sold_at": st.isoformat(),
                    "pnl_net": round(float(c.get("pnl_net") or 0.0), 2),
                    "held_min": c.get("held_min"),
                    "goal_pct": c.get("goal_pct"),
                    "verdict": ("PAY" if float(c.get("pnl_net") or 0.0) > 0
                                else "COST")})
                break
            out.append(row)
    return out


def run_study() -> dict:
    """One pass: join the fires to the closes, price the bridge, write
    the report + the monthly ledger record. Never raises on data —
    missing/junk files price honest zeros."""
    lines: list[str] = []
    _say("THE CLIMB-OUT BRIDGE'S HINDSIGHT (Sep 20)", lines)
    _say("fires: _climbout.jsonl | closes: contest_trades.jsonl "
         "(pnl_net realized)", lines)
    _say("a fire prices its pair's first SOLD close at-or-after the "
         "fire; earlier closes never join\n", lines)

    fires = _read_jsonl(FIRES_FILE)
    closes = _read_jsonl(TRADES_FILE)
    rows = join_fires_to_closes(fires, closes)

    graded = [r for r in rows if r["verdict"]]
    pending = [r for r in rows if not r["verdict"]]
    wins = [r for r in graded if r["verdict"] == "PAY"]
    net = round(sum(r["pnl_net"] or 0.0 for r in graded), 2)
    avg = round(net / len(graded), 2) if graded else 0.0
    # THE SAMPLE RUNG (Sep 20): the bridge's paid-vs-cost verdict is
    # advisory (it argues for or against the bounce bracket), so it
    # answers to the ladder's SAMPLE_BARS['CLIMBOUT_BRIDGE'] rung.
    # Below the bar the fires, the net, and the per-row record all
    # render; only the verdict names its thinness instead.
    try:
        from evidence_ladder import SAMPLE_BARS as _sb, \
            samples_mature as _sm
        _rung, _sm_ok = _sb["CLIMBOUT_BRIDGE"], \
            _sm("CLIMBOUT_BRIDGE", len(graded))
    except Exception:
        _rung, _sm_ok = 8, len(graded) >= 8

    _say(f"fires: {len(rows)}  graded: {len(graded)}  pending: "
         f"{len(pending)}", lines)
    if _sm_ok:
        _say(f"BRIDGE VERDICT: {len(wins)} paid, {len(graded) - len(wins)} "
             f"cost, net {net:+.2f} USD"
             + (f" ({avg:+.2f}/graded fire)" if graded else ""), lines)
    else:
        _say(f"BRIDGE SAMPLE: {len(wins)} paid, {len(graded) - len(wins)} "
             f"cost, net {net:+.2f} USD — TOO THIN TO VERDICT: "
             f"{len(graded)} graded close(s), the ladder's "
             f"CLIMBOUT_BRIDGE rung needs {_rung}", lines)
    if rows:
        _say("", lines)
        for r in rows:
            if r["verdict"]:
                _say(f"  {r['pair']:12s} fired {r['fired_at'][:16]} -> sold "
                     f"{r['sold_at'][:16]}  {r['pnl_net']:+8.2f} USD  "
                     f"[{r['verdict']}]  held {r['held_min']} min", lines)
            else:
                _say(f"  {r['pair']:12s} fired {r['fired_at'][:16]} -> "
                     f"PENDING (no SOLD close yet)", lines)
    else:
        _say("  no climb-out fires yet — the bridge has not spoken; "
             "the first fire lands in _climbout.jsonl and the next "
             "pass prices it", lines)

    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fires_total": len(rows), "graded": len(graded),
        "pending": len(pending), "wins": len(wins),
        "losses": len(graded) - len(wins),
        "net_usd": net, "avg_usd": avg, "rows": rows,
    }
    try:
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        _say(f"\nledger append failed: {exc}", lines)
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return record


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one pass shortly after boot (the ledger record
    seeds the history), then monthly. Never raises; dies with the
    scanner. The append-only ledger is the re-pricing history — each
    month's record prices the fires AS THEY STOOD, and pending fires
    flip to graded as their closes land."""
    import logging
    log = logging.getLogger("scanner")
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            log.info("CLIMB-OUT HINDSIGHT: monthly re-price — %d/%d "
                     "fire(s) graded, %d pending, net %+.2f USD",
                     s["graded"], s["fires_total"], s["pending"],
                     s["net_usd"])
        except Exception as exc:
            log.info("CLIMB-OUT HINDSIGHT: pass failed (%s) — this "
                     "month's re-price skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    s = run_study()
    print(json.dumps({k: v for k, v in s.items() if k != "rows"},
                     indent=1))
