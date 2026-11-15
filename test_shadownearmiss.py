"""test_shadownearmiss.py — the shadow near-miss recorder (Nov 15).

The recorder exists to stop every future study dying on a 12-trade holdout, so its
contract is strict: it writes what decided each near-miss, resolves it against real
closes, and touches nothing else — no order, no watch row, no balance.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

import near_miss as nm
import background_scanner as bs
import config
import scanner as sc

PASS = FAIL = 0
lines = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  {detail}")


class _Res:
    def __init__(self, pair, eq, conf, rows=30):
        self.pair = pair
        self.entry_quality = eq
        self.spend = {"cap_usd": 3000, "src": ["deep_history", "ticker"]}
        self.signal = sc.Signal(action="buy", confidence=conf, price=1.0, reasons=["r"])


def _df(n=30, base=10.0):
    import pandas as pd
    rows = []
    for i in range(n):
        px = base + i * 0.05
        rows.append({"open": px, "high": px + 0.03, "low": px - 0.03,
                     "close": px, "volume": 100.0})
    return pd.DataFrame(rows)


class _Bars:
    """Real-ish 5m bars: rises 20 bars, then gives back — a resolvable shape."""

    def __init__(self, n=40, start_ts=None):
        self.n = n
        self.start = start_ts or 1_700_000_000.0

    def fetch_ohlcv(self, pair, timeframe=None, since=None, limit=None):
        out = []
        for i in range(self.n):
            ts = (since / 1000.0 if since else self.start) + i * 300
            px = 10.0 + 0.02 * min(i, 20) - 0.02 * max(0, i - 20)
            out.append([ts * 1000, px, px + 0.01, px - 0.01, px, 50.0])
        return out


class _Dead:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("offline")


print("== 1. features say what decided the call")
_f = nm.features(_df(), 0.78, 55.0)
check("1.1 it carries the gate's number and the signal's confidence",
      _f["entry_quality"] == 0.78 and _f["confidence"] == 55.0)
check("1.2 it carries extension and the base-relative position",
      _f["ext4h"] is not None and _f["base_pct"] is not None and _f["base_pct"] > 0,
      (_f["ext4h"], _f["base_pct"]))
check("1.3 an unreadable frame degrades to Nones, never raises",
      nm.features(None, 0.5, 50.0)["ext4h"] is None)

print("== 2. recording a cycle's near-misses")
_tmp = pathlib.Path(tempfile.mkdtemp()) / "nm.jsonl"
_cfg = config.TradingConfig()
cands = {"LOW/USD": _Res("LOW/USD", 0.40, 60.0),
         "NEAR/USD": _Res("NEAR/USD", 0.79, 70.0),
         "HIGH/USD": _Res("HIGH/USD", 0.95, 80.0)}
all_data = {k: _df() for k in cands}
n = nm.record_pool(cands, all_data, "LOW/USD", _cfg, source="test", path=_tmp)
rows = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
check("2.1 the vetoed champion and the near-miss are journalled, the crowned one is not",
      n == 2 and {r["pair"] for r in rows} == {"LOW/USD", "NEAR/USD"}, [r["pair"] for r in rows])
check("2.2 each row names its reason and is unresolved",
      all(r["reason"] and r["resolved"] is False for r in rows))
check("2.3 a far-under candidate is not journalled",
      "HIGH/USD" not in {r["pair"] for r in rows})
nm.record_veto("MV/USD", sc.Signal(action="buy", confidence=61, price=1.0, reasons=["r"]),
               0.30, None, _cfg, route="mover-bridge", source="test", path=_tmp)
rows = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
check("2.4 a refused mover fire is journalled with its route",
      rows[-1]["pair"] == "MV/USD" and rows[-1]["route"] == "mover-bridge")

print("== 3. resolving against real closes")
_now = 1_700_000_000.0 + 4000
for r in rows:
    r["ts_epoch"] = 1_700_000_000.0 - 7200        # aged past the window
_tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
start_len = len(rows)
nm.resolve(_Bars(), _cfg, path=_tmp, now_s=_now)
rows = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
res = [r for r in rows if r.get("resolved")]
check("3.1 every aged row is resolved", len(res) == start_len, (len(res), start_len))
check("3.2 a resolved row carries forward return, MAE and the hard-stop flag",
      all(r.get("fwd_pct") is not None and r.get("mae_pct") is not None
          and isinstance(r.get("hit_hard_stop"), bool) and r.get("bars", 0) > 0
          for r in res), res[0] if res else None)
check("3.3 the resolution horizon is the shipped hold window",
      all(r["hold_min"] == nm.HORIZON_MIN for r in res))
check("3.5 a resolved row carries the peak path, which is what a take-profit needs",
      all(r.get("peak_pct") is not None and isinstance(r.get("reached_2pct"), bool)
          for r in res), res[0] if res else None)
check("3.6 the peak is at least the entry and never below the worst excursion",
      all(r["peak_pct"] >= 0 and r["peak_pct"] >= r["mae_pct"] for r in res))
nm.resolve(_Bars(), _cfg, path=_tmp, now_s=_now + 100)
again = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
check("3.4 resolving again changes nothing (idempotent, append-free)",
      len(again) == len(rows))

print("== 3b. peaks backfilled for rows resolved before the field existed")
_old = pathlib.Path(tempfile.mkdtemp()) / "nm3.jsonl"
row = {"ts": "2026-11-15T07:40:00-07:00", "ts_epoch": 1_700_000_000.0 - 7200,
       "pair": "X/USD", "route": "crown", "source": "test", "reason": "r",
       "entry_quality": 0.9, "gate_min": 0.85, "resolved": True,
       "fwd_pct": -0.5, "mae_pct": -0.6, "hit_hard_stop": False}
_old.write_text(json.dumps(row) + "\n", encoding="utf-8")
n = nm.backfill_peaks(_Bars(), path=_old)
back = json.loads(_old.read_text(encoding="utf-8").splitlines()[0])
check("3b.1 the backfill adds peak_pct and reached_2pct to an old row",
      n == 1 and back.get("peak_pct") is not None
      and isinstance(back.get("reached_2pct"), bool), back)
n2 = nm.backfill_peaks(_Bars(), path=_old)
check("3b.2 and is idempotent (a row with a peak is left alone)", n2 == 0)

print("== 4. it cannot move the book")
fresh = pathlib.Path(tempfile.mkdtemp()) / "nm2.jsonl"
nm.record_veto("X/USD", sc.Signal(action="buy", confidence=50, price=1.0, reasons=["r"]),
               0.2, _df(), _cfg, route="test", source="test", path=fresh)
r0 = json.loads(fresh.read_text(encoding="utf-8").splitlines()[0])
r0["ts_epoch"] = 1_700_000_000.0 - 7200
fresh.write_text(json.dumps(r0) + "\n", encoding="utf-8")
nm.resolve(_Dead(), _cfg, path=fresh, now_s=_now)
kept = json.loads(fresh.read_text(encoding="utf-8").splitlines()[0])
check("4.1 a dead venue leaves the row unresolved instead of inventing a number",
      kept["resolved"] is False)
_src_nm = open("near_miss.py", encoding="utf-8").read()
check("4.2 the module never touches the bridge, the watch or an order",
      not any(k in _src_nm for k in ("write_buy_signal", "place_market", "contest_watch",
                                     "CONTEST_WATCH")),
      "order/watch reference found")

print("== 5. it is wired into the live cycle")
_src_bs = open("background_scanner.py", encoding="utf-8").read()
check("5.1 the cycle journals its near-misses on a veto",
      "_near_miss.record_pool(cands, all_data, best_pair, cfg, source=source)"
      in _src_bs)
check("5.2 the refractory resolve runs inside the cycle",
      "_near_miss.resolve(exchange, cfg)" in _src_bs)
check("5.3 the mover-bridge refusal is journalled too",
      'route="mover-bridge"' in _src_bs and "_near_miss.record_veto(" in _src_bs)
check("5.4 the recorder is called after the decision, never instead of it",
      _src_bs.index("if not ok:\n        out[\"decision\"] = \"veto\"")
      < _src_bs.index("_near_miss.record_pool("))

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
