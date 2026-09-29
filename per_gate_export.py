"""THE PER-GATE BURIED-PAYER AGGREGATES (task 1, Sep 28 spec).

The machine's version of the spec's SQL export: the live "tables" are
popup_suppressions.jsonl (the fire ledger's refusal side) and
ohlcv_cache.db (the cache), so this module
  1. loads one rotation's refusal rows from the journal,
  2. dedups to FIRST refusal per (candidate, gate) — the same rule the
     verdict study and the console panel use,
  3. computes each candidate's max-run peak over the 7h window from the
     OHLCV cache (entry = last close before the refusal — the same join),
  4. loads BOTH into a throwaway sqlite db and executes the real
     sql/per_gate_ratios.sql against it,
  5. exports the CSV artifact artifacts/per_gate_ratios/rotation_<id>.csv.

Read-only on every production file: the journals are read_text only, the
cache opens mode=ro via URI, nothing is ever written back. Stdlib only.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SQL_PATH = ROOT / "sql" / "per_gate_ratios.sql"
HOURS = 7
DEFAULT_GOAL_PCT = 1.5
MST = None  # resolved lazily via the machine's own tz helper


def _now_mst():
    global MST
    if MST is None:
        try:
            from app import _now_mst as _f
            MST = _f
        except Exception:
            from datetime import timezone, timedelta
            MST = lambda: datetime.now(timezone(timedelta(hours=-7)))
    return MST()


# ── the journal side ────────────────────────────────────────────────
def load_rotation_rows(supp_path: Path, rotation_id: str) -> list[dict]:
    """Every suppression row stamped with this rotation id. Read-only."""
    if not supp_path.exists():
        return []
    rows = []
    for line in supp_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if str(d.get("rot") or "") == rotation_id and d.get("pair"):
            rows.append(d)
    return rows


def dedup_first(rows: list[dict]) -> list[dict]:
    """FIRST refusal per (pair, gate) — the machine's dedup rule."""
    first: dict[tuple, dict] = {}
    for r in rows:
        first.setdefault((str(r.get("pair")), str(r.get("gate") or "?")), r)
    return sorted(first.values(), key=lambda r: str(r.get("ts") or ""))


# ── the peak side (the same join the verdict study / panel use) ─────
def compute_peak(con: sqlite3.Connection, pair: str, ts_ms: int,
                 goal_pct: float) -> dict:
    """max high within HOURS after ts_ms; entry = last close before."""
    end_ms = ts_ms + HOURS * 3_600_000
    pre = con.execute(
        "SELECT close FROM ohlcv WHERE pair=? AND ts<? "
        "ORDER BY ts DESC LIMIT 1", (pair, ts_ms)).fetchone()
    hi = con.execute(
        "SELECT MAX(high) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
        (pair, ts_ms, end_ms)).fetchone()
    if not pre or not pre[0] or not hi or not hi[0]:
        return {"max_run_pct": None, "peak_ts": None}
    run = (float(hi[0]) / float(pre[0]) - 1.0) * 100.0
    return {"max_run_pct": round(run, 4), "peak_ts": end_ms}


def _cache_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    return sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True,
                           timeout=2.0)


# ── the loader: journals + cache -> fixture db -> the real SQL ──────
def build_fixture_db(rows: list[dict], cache_path: Path,
                     goal_pct: float = DEFAULT_GOAL_PCT) -> sqlite3.Connection:
    """Materialize rotation + peaks into an in-memory db shaped for
    sql/per_gate_ratios.sql. The candidate_id is the dedup key."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE popup_suppressions (rotation_id TEXT, "
                "candidate_id TEXT, gate TEXT, ts TEXT, score REAL)")
    con.execute("CREATE TABLE hindsight_peaks (candidate_id TEXT, "
                "max_run_pct REAL, peak_ts INTEGER)")
    cache = _cache_ro(cache_path)
    try:
        for r in rows:
            cid = f"{r.get('pair')}|{r.get('rot')}|{r.get('gate') or '?'}"
            con.execute("INSERT INTO popup_suppressions VALUES (?,?,?,?,?)",
                        (r.get("rot"), cid, str(r.get("gate") or "?"),
                         str(r.get("ts") or ""),
                         float(r.get("quick") or 0.0)))
            if cache is not None:
                try:
                    t = datetime.fromisoformat(str(r.get("ts") or ""))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=_now_mst().tzinfo)
                    peak = compute_peak(cache, str(r.get("pair")),
                                        int(t.timestamp() * 1000), goal_pct)
                except Exception:
                    peak = {"max_run_pct": None, "peak_ts": None}
            else:
                peak = {"max_run_pct": None, "peak_ts": None}
            con.execute("INSERT INTO hindsight_peaks VALUES (?,?,?)",
                        (cid, peak["max_run_pct"], peak["peak_ts"]))
        con.commit()
    finally:
        if cache is not None:
            cache.close()
    return con


def run_ratios_sql(con: sqlite3.Connection,
                   rotation_id: str,
                   goal_pct: float = DEFAULT_GOAL_PCT) -> list[dict]:
    """Execute sql/per_gate_ratios.sql verbatim; list of row dicts."""
    sql = SQL_PATH.read_text(encoding="utf-8")
    cur = con.execute(sql, {"rotation_id": rotation_id,
                            "goal_pct": goal_pct})
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── the CSV artifact ────────────────────────────────────────────────
def export_csv(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["gate", "measurable_B_g", "P_g", "r_g"])
        for r in rows:
            w.writerow([r["gate"], r["measurable_B_g"],
                        r["P_g"] if r["P_g"] is not None else 0,
                        "" if r["r_g"] is None else f"{r['r_g']:.4f}"])
    return out_path


def rotation_summary(rows: list[dict]) -> dict:
    """The rotation-wide B, P, r — the auto-adjust's feed (task 2)."""
    B = sum(r["measurable_B_g"] or 0 for r in rows)
    P = sum(r["P_g"] or 0 for r in rows)
    return {"B": B, "P": P, "r": (P / B) if B else None}


def default_rotation_id(now: datetime | None = None) -> str:
    """The journal's rot stamp for the rotation holding `now` — 4h
    windows, the same shape the scanner stamps (e.g. '12-16')."""
    now = now or _now_mst()
    start = (now.hour // 4) * 4
    return f"{start:02d}-{(start + 4) % 24:02d}"


def main() -> int:
    import sys
    rot = sys.argv[1] if len(sys.argv) > 1 else default_rotation_id()
    goal = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_GOAL_PCT
    rows = dedup_first(load_rotation_rows(
        ROOT / "popup_suppressions.jsonl", rot))
    con = build_fixture_db(rows, ROOT / "ohlcv_cache.db", goal)
    try:
        out = run_ratios_sql(con, rot, goal)
    finally:
        con.close()
    art = ROOT / "artifacts" / "per_gate_ratios" / \
        f"rotation_{rot.replace(' ', '_').replace(':', '')}.csv"
    export_csv(out, art)
    print(f"rotation {rot}: {json.dumps(rotation_summary(out))}")
    print(f"per-gate: {json.dumps(out)}")
    print(f"csv: {art}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
