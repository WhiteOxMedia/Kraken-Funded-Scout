"""LOOP REPLAY — the live loop's own crown decision, driven on real recorded cycles.

`background_scanner.crown_cycle` is the function the running scanner calls every
full scan (it is where the loop's provisional champion is picked, the deep read
runs and the breakout gate judges). This drives THAT function — not a copy of it
and not a mock of the decision — over real recorded cycles rebuilt from the 5m
tape with the scanner's own `evaluate_strategy`, until it meets a genuine
sub-0.85 champion, and shows:

  * the veto branch: nothing crowned, nothing fired, one veto row in
    contest_gate_log.jsonl with pair, route, measured entry_quality, gate_v and
    the pooled/OOS figures;
  * the breakout branch: crowned, and the fire callback invoked;
  * a run of consecutive cycles: no exception, no fire for vetoed cycles, the
    contest account file byte-identical before and after, no watch row written.

Run: .venv/Scripts/python.exe -X utf8 scratch/loop_replay.py
"""
from __future__ import annotations

import bisect
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import background_scanner as bs   # noqa: E402
import breakout_gate as bg        # noqa: E402
import config as cfgmod           # noqa: E402
import scanner as sc              # noqa: E402
from entry_edge_study import LOOKBACK, hourly, load_tape  # noqa: E402

POINTS = ROOT / "scratch" / "_tourney_points.jsonl"
ACCOUNT = ROOT / "contest_account.json"
WATCH = ROOT / "contest_watch.json"
MIN_CONF = 48


class _TickerOnly:
    """Prices, no history: the deep read's history fetch fails soft exactly as a
    dead venue would, while entry_quality comes from the real recorded frame."""

    def fetch_ohlcv(self, *a, **k):
        return []

    def fetch_ticker(self, *a, **k):
        return {"high": 10.0, "low": 8.0, "last": 9.0, "quoteVolume": 5e6}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def build(ts, pairs):
    tape = load_tape()
    cfg = cfgmod.TradingConfig()
    cfg.mtf_enabled = False
    cfg.liquidity_filter_enabled = False
    cfg.deep_scan_enabled = False
    results, all_data = {}, {}
    for sym in pairs:
        bars = tape.get(sym)
        if not bars or len(bars) < 500:
            continue
        hb = hourly(bars)
        hst = [int(b[0]) for b in hb]
        i = bisect.bisect_right(hst, ts * 1000) - 1
        if i < 60:
            continue
        window = hb[max(0, i - LOOKBACK + 1):i + 1]
        try:
            df = sc._ohlcv_to_df(window)
            sig = sc.evaluate_strategy(df, cfg)
        except Exception:
            continue
        if sig.action != "buy" or sig.confidence < MIN_CONF:
            continue
        results[sym] = sig
        all_data[sym] = df
    return results, all_data


def main():
    pts = [json.loads(l) for l in POINTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    cfg = cfgmod.TradingConfig()
    acc_before, watch_before = _sha(ACCOUNT), _sha(WATCH)
    fails, fired = [], []

    def chk(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + "  " + str(detail))
        if not cond:
            fails.append(name)

    def rows_since(n):
        if not bg.GATE_LOG.exists():
            return []
        allr = [json.loads(l) for l in bg.GATE_LOG.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        return allr[n:]

    def count():
        return (len(bg.GATE_LOG.read_text(encoding="utf-8").splitlines())
                if bg.GATE_LOG.exists() else 0)

    # ── the veto branch: the first real cycle whose champion is not a breakout
    dip = next(p for p in pts
               if (max(p["pool"], key=lambda c: c["conf"])["pos_hour"] or 1.0) < 0.80)
    results, all_data = build(dip["t"], [c["pair"] for c in dip["pool"]])
    champ = max(results, key=lambda p: results[p].confidence)
    n0 = count()
    out = bs.crown_cycle(_TickerOnly(), results, all_data, cfg, MIN_CONF,
                         fire=lambda p, s, t: fired.append(p), source="replay")
    new = rows_since(n0)
    print(f"\n-- veto branch: real cycle {dip['t']}, loudest buy {champ} "
          f"{results[champ].confidence:.0f}%, decision={out['decision']}")
    chk("V1 the loop's cycle crowns NOTHING for a sub-0.85 champion",
        out["pair"] is None and out["signal"] is None, out.get("pair"))
    chk("V2 and fires NOTHING", fired == [], fired)
    chk("V3 it appends exactly one veto row",
        len(new) == 1 and new[0]["decision"] == "veto", new)
    if new:
        r = new[0]
        chk("V4 the row carries pair, route, measured entry_quality, gate v, pooled/OOS",
            r["pair"] == champ and r["route"] == "champion"
            and r["entry_quality"] is not None and r["entry_quality"] < 0.85
            and r["gate_v"] == 1 and r["pooled_win_pct"] == 80.6
            and r["oos_win_pct"] == 66.7 and len(r["oos_ci"]) == 2, r)

    # ── the breakout branch, same function
    rip = next(p for p in pts
               if (max(p["pool"], key=lambda c: c["conf"])["pos_hour"] or 0.0) >= 0.90)
    results2, all_data2 = build(rip["t"], [c["pair"] for c in rip["pool"]])
    fired.clear()
    n1 = count()
    out2 = bs.crown_cycle(_TickerOnly(), results2, all_data2, cfg, MIN_CONF,
                          fire=lambda p, s, t: fired.append(p), source="replay")
    print(f"\n-- breakout branch: real cycle {rip['t']}, decision={out2['decision']}, "
          f"champion={out2['pair']}, entry_quality={out2.get('entry_quality')}")
    chk("B1 the same cycle function crowns the breakout", out2["pair"] is not None
        and out2["decision"] == "fire")
    chk("B2 and hands it to the fire callback exactly once",
        fired == [out2["pair"]], fired)
    chk("B3 a crowned breakout writes no veto row",
        not [r for r in rows_since(n1) if r["decision"] == "veto"],
        rows_since(n1))

    # ── consecutive real cycles: no exception, no fire on vetoed cycles
    print("\n-- a run of real consecutive cycles through the same function --")
    fires, vetoes, errors = 0, 0, 0
    for pt in pts[:40]:
        try:
            rs, ad = build(pt["t"], [c["pair"] for c in pt["pool"]])
            if not rs:
                continue
            before = len(fired)
            res = bs.crown_cycle(_TickerOnly(), rs, ad, cfg, MIN_CONF,
                                 fire=lambda p, s, t: fired.append(p), source="replay")
            if res["decision"] == "veto":
                vetoes += 1
                if len(fired) != before:
                    fails.append("R: a vetoed cycle fired")
            elif res["decision"] == "fire":
                fires += 1
        except Exception as exc:            # noqa: BLE001 — the run must not throw
            errors += 1
            print(f"EXCEPTION at {pt['t']}: {exc!r}")
    print(f"   {len(pts[:40])} cycles: {fires} crowned+handed to fire, "
          f"{vetoes} vetoed, {errors} exceptions")
    chk("R1 no cycle raised", errors == 0, errors)
    chk("R2 every vetoed cycle fired nothing", "R: a vetoed cycle fired" not in fails)
    chk("R3 the contest account is byte-identical", _sha(ACCOUNT) == acc_before)
    chk("R4 no watch row was written", _sha(WATCH) == watch_before)

    print(f"\nRESULT: {'PASS' if not fails else 'FAIL'} ({len(fails)} failed)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
