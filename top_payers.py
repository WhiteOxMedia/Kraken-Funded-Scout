"""THE TOP-10 EXPECTED-PAYOFF EXPORT (the Sep-28 late checklist):
the highest-expected-payoff buried candidates of a rotation, with the
OHLCV evidence — the sheet Ops reads to decide targeted manual replays
or one-off overrides. Payoff is the burial study's own definition (the
pct run above the entry the gate refused), fill-adjusted like the
console panel: the tradeable gain after spread, round-trip fee, and
zero-capture under the floor. Read-only on every source."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "artifacts" / "top_payers"
HOURS = 7

SPREAD = 0.001
FEE_RT_PCT = 0.26


def top_expected(rotation_id: str, k: int = 10,
                 supp_path: Path | None = None,
                 cache_path: Path | None = None,
                 goal_pct: float = 1.5) -> list[dict]:
    """The rotation's k best payers by peak run, fill-adjusted."""
    import per_gate_export as pge
    supp = supp_path or ROOT / "popup_suppressions.jsonl"
    cache = cache_path or ROOT / "ohlcv_cache.db"
    rows = pge.dedup_first(pge.load_rotation_rows(supp, rotation_id))
    if not cache.exists():
        return []
    con = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True,
                          timeout=2.0)
    out = []
    try:
        for r in rows:
            try:
                t = datetime.fromisoformat(str(r.get("ts") or ""))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=pge._now_mst().tzinfo)
                ms = int(t.timestamp() * 1000)
            except Exception:
                continue
            pre = con.execute(
                "SELECT close FROM ohlcv WHERE pair=? AND ts<? "
                "ORDER BY ts DESC LIMIT 1", (r["pair"], ms)).fetchone()
            hi = con.execute(
                "SELECT MAX(high), ts FROM ohlcv WHERE pair=? AND ts>=? "
                "AND ts<=?", (r["pair"], ms,
                              ms + int(HOURS * 3_600_000))).fetchone()
            if not pre or not pre[0] or not hi or not hi[0]:
                continue
            peak = (float(hi[0]) / float(pre[0]) - 1.0) * 100.0
            if peak < goal_pct:
                continue
            fill_entry = float(pre[0]) * (1.0 + SPREAD * 0.5)
            fill_exit = float(hi[0]) * (1.0 - SPREAD * 0.5)
            fill = (fill_exit / fill_entry - 1.0) * 100.0 - FEE_RT_PCT
            out.append({
                "pair": r["pair"], "gate": r.get("gate") or "?",
                "ts": str(r.get("ts") or ""),
                "at": str(r.get("ts") or "")[11:16],
                "quick": r.get("quick"),
                "expected_payoff_pct": round(peak, 2),
                "fill_adjusted_pct": round(max(fill, 0.0), 2),
                "peak_ts": hi[1],
            })
    finally:
        con.close()
    out.sort(key=lambda s: -s["expected_payoff_pct"])
    return out[:k]


def export_csv(rows: list[dict], path: Path) -> Path:
    """The CSV Ops attaches to the decision ticket."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair", "gate", "ts", "at", "quick",
                    "expected_payoff_pct", "fill_adjusted_pct",
                    "peak_ts"])
        for r in rows:
            w.writerow([r["pair"], r["gate"], r["ts"], r["at"],
                        r.get("quick", ""), r["expected_payoff_pct"],
                        r["fill_adjusted_pct"], r["peak_ts"]])
    return path


def main() -> int:
    import sys
    rot = sys.argv[1] if len(sys.argv) > 1 else \
        __import__("per_gate_export").default_rotation_id()
    rows = top_expected(rot)
    stamp = rot.replace(":", "").replace(" ", "_")
    art = export_csv(rows, OUT_DIR / f"top_payers_{stamp}.csv")
    (OUT_DIR / f"top_payers_{stamp}.json").write_text(
        json.dumps({"rotation_id": rot, "rows": rows}, indent=2),
        encoding="utf-8")
    print(f"rotation {rot}: {len(rows)} top payer(s)")
    for r in rows:
        print(f"  {r['at']} {r['pair']:<12} {r['gate']:<16} "
              f"peak {r['expected_payoff_pct']:+.1f}% "
              f"fill {r['fill_adjusted_pct']:+.1f}%")
    print(f"csv: {art}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
