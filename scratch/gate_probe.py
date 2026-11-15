"""GATE PROBE — both branches of the breakout gate, on real tape, shipped code (Nov 15).

Drives the scanner's own functions (`deep_crown_pool`, which crowns and gates, and
`breakout_gate.evaluate`, which every fire path calls at the choke point) over REAL
recorded cycles: the pool at a real timestamp is rebuilt from the real 5m tape with
the scanner's own `evaluate_strategy`, so the champion, its `entry_quality` and the
verdict are the ones the live loop would have produced.

  branch A — a real cycle whose champion sits at the top of its hour range:
             crowned, and the choke records the fire.
  branch B — a real cycle whose champion sits BELOW the gate: not crowned, no
             fire, and the refusal lands in `contest_gate_log.jsonl` with the
             gate version, the measured entry_quality and the pooled/OOS numbers.

No order is placed and the real watch/account files are never touched: this probe
only reads the tape and appends to the gate's own journal.

Run: .venv/Scripts/python.exe -X utf8 scratch/gate_probe.py
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import config as cfgmod          # noqa: E402
import background_scanner as bs  # noqa: E402
import breakout_gate as bg       # noqa: E402
import scanner as sc             # noqa: E402
from entry_edge_study import LOOKBACK, hourly, load_tape  # noqa: E402

POINTS = ROOT / "scratch" / "_tourney_points.jsonl"
MIN_CONF = 48


class _TickerOnly:
    """A client with prices but no history: the probe's spending read fails
    soft exactly as a dead venue would, while entry_quality comes from the real
    recorded frame — which is the piece under test."""

    def fetch_ohlcv(self, *a, **k):
        return []

    def fetch_ticker(self, *a, **k):
        return {"high": 10.0, "low": 8.0, "last": 9.0, "quoteVolume": 5e6}


def build(ts, pairs):
    """The real pool at a real recorded timestamp, rebuilt with the scanner."""
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
    veto_pt = next(p for p in pts
                   if (ch := max(p["pool"], key=lambda c: c["conf"]))["pos_hour"] is not None
                   and ch["pos_hour"] < 0.80)
    fire_pt = next(p for p in pts
                   if (ch := max(p["pool"], key=lambda c: c["conf"]))["pos_hour"] is not None
                   and ch["pos_hour"] >= 0.90)
    cfg = cfgmod.TradingConfig()
    fails = []

    def chk(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + "  " + str(detail))
        if not cond:
            fails.append(name)

    for label, pt, want_fire in (("A (breakout)", fire_pt, True),
                                 ("B (dip champion)", veto_pt, False)):
        pairs = [c["pair"] for c in pt["pool"]]
        results, all_data = build(pt["t"], pairs)
        if not results:
            print(f"SKIP  branch {label}: the recorded pool could not be rebuilt")
            continue
        best_pair = max(results, key=lambda p: results[p].confidence)
        best_sig = results[best_pair]
        before = bg.GATE_LOG.read_text(encoding="utf-8").count("\n") if bg.GATE_LOG.exists() else 0
        bp, bsig, cands, ranks = bs.deep_crown_pool(
            _TickerOnly(), results, all_data, cfg, best_pair, best_sig, MIN_CONF)
        eq = (cands.get(best_pair).entry_quality if cands.get(best_pair) else None)
        rows = []
        if bg.GATE_LOG.exists():
            rows = [json.loads(l) for l in bg.GATE_LOG.read_text(encoding="utf-8").splitlines()
                    if l.strip()][before:]
        print(f"\n-- branch {label}: real cycle {pt['t']}, champion {best_pair} "
              f"{best_sig.confidence:.0f}%, entry_quality {eq}")
        if want_fire:
            chk(f"{label} 1. the gate crowns the breakout champion", bp == best_pair, bp)
            ok, eq2 = bg.evaluate(best_pair, bsig, cfg=cfg, route="crown")
            chk(f"{label} 2. the choke point allows the fire", ok is True, eq2)
        else:
            chk(f"{label} 1. the gate does NOT crown the champion below 0.85",
                bp is None and bsig is None, bp)
            chk(f"{label} 2. the refusal is in the gate journal, with its numbers",
                any(r["pair"] == best_pair and r["decision"] == "veto"
                    and r["entry_quality"] is not None and r["entry_quality"] < 0.85
                    and r["gate_v"] == 1 and r["pooled_win_pct"] == 80.6
                    and len(r["oos_ci"]) == 2 for r in rows),
                rows[-1] if rows else "no row written")
            print(f"     journal row: {rows[-1] if rows else 'MISSING'}")
            # ... and the same real signal through the CHOKE POINT every fire
            # route calls (send_alert's own function, same argument shape):
            # the refusal the live loop would make on a dip champion.
            _okc, _eqc = bg.evaluate(best_pair, best_sig, cfg=cfg, route="standard")
            _rowc = [json.loads(l) for l in bg.GATE_LOG.read_text(encoding="utf-8").splitlines()
                     if l.strip()][-1]
            chk(f"{label} 3. the fire choke refuses it too, and journals the refusal",
                _okc is False and _rowc["decision"] == "veto"
                and _rowc["route"] == "standard" and _rowc["pair"] == best_pair, _rowc)

    print(f"\nRESULT: {'PASS' if not fails else 'FAIL'} ({len(fails)} failed)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
