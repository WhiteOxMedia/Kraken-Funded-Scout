"""test_climbouthindsight.py — hermetic pins for the climb-out bridge's
hindsight study (diag_climbouthindsight).

The climb-out fire ledger has never carried a fire yet (the bridge is
Sep 19-new), so these pins prove the machinery on synthetic worlds —
no production file is read or written:

  1. the join's five worlds: a pay, a cost, a pending, the
     fire-before-close rule (an earlier close never joins), and the
     repeat-fire rule (a second fire takes the pair's NEXT close)
  2. junk-line tolerance on both ledgers; missing files price zeros
  3. the arithmetic to the cent: net, wins/losses, per-fire average
  4. the driver loop: fast-clock ticks append, clean stop
  5. the family drift guard: the scanner's driver block carries the
     climb-out daemon with the family's conventions
"""
import json
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import diag_climbouthindsight as dch  # noqa: E402

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  [{detail}]")


import tempfile  # noqa: E402
import scratch_space   # scratch policy: nothing volatile lands in the repo root
TMP = Path(tempfile.mkdtemp())
_fires = TMP / "fires.jsonl"
_trades = TMP / "trades.jsonl"
_led = TMP / "ledger.jsonl"
_out = TMP / "out.txt"
_real = (dch.FIRES_FILE, dch.TRADES_FILE, dch.LEDGER, dch.OUT)

try:
    dch.FIRES_FILE, dch.TRADES_FILE = _fires, _trades
    dch.LEDGER, dch.OUT = _led, _out

    # 1. THE JOIN — five worlds in one fixture.
    _fires.write_text("\n".join([
        json.dumps({"pair": "AAA/USD",
                    "fired_at": "2026-09-20T05:00:00-06:00"}),
        json.dumps({"pair": "BBB/USD",
                    "fired_at": "2026-09-20T05:00:00-06:00"}),
        json.dumps({"pair": "CCC/USD",
                    "fired_at": "2026-09-20T05:00:00-06:00"}),
        json.dumps({"pair": "AAA/USD",
                    "fired_at": "2026-09-20T09:00:00-06:00"}),
        "junk line"]) + "\n", encoding="utf-8")
    _trades.write_text("\n".join([
        json.dumps({"pair": "AAA/USD",
                    "sold_at": "2026-09-20T04:00:00-06:00",
                    "pnl_net": 50.0, "held_min": "30", "goal_pct": 1.5}),
        json.dumps({"pair": "AAA/USD",
                    "sold_at": "2026-09-20T05:30:00-06:00",
                    "pnl_net": -3.0, "held_min": "20", "goal_pct": 1.5}),
        json.dumps({"pair": "AAA/USD",
                    "sold_at": "2026-09-20T09:30:00-06:00",
                    "pnl_net": 12.0, "held_min": "25", "goal_pct": 1.5}),
        json.dumps({"pair": "BBB/USD",
                    "sold_at": "2026-09-20T06:00:00-06:00",
                    "pnl_net": -7.5, "held_min": "40", "goal_pct": 1.5}),
        "{ broken json"]) + "\n", encoding="utf-8")
    s = dch.run_study()
    by_fire = {(r["pair"], r["fired_at"][11:16]): r for r in s["rows"]}
    check("join: a fire prices its pair's first close AT OR AFTER the "
          "fire (AAA 05:00 -> the 05:30 close, not the earlier 04:00 "
          "pay — the fire cannot claim a trade it did not open)",
          by_fire[("AAA/USD", "05:00")]["pnl_net"] == -3.0
          and by_fire[("AAA/USD", "05:00")]["verdict"] == "COST",
          json.dumps(by_fire[("AAA/USD", "05:00")]))
    check("join: a repeat fire takes the pair's NEXT unused close "
          "(AAA 09:00 -> the 09:30 close)",
          by_fire[("AAA/USD", "09:00")]["pnl_net"] == 12.0
          and by_fire[("AAA/USD", "09:00")]["verdict"] == "PAY",
          json.dumps(by_fire[("AAA/USD", "09:00")]))
    check("join: a fire with no close yet stays PENDING — never "
          "priced before it exists",
          by_fire[("CCC/USD", "05:00")]["verdict"] is None
          and s["pending"] == 1, json.dumps(by_fire[("CCC/USD", "05:00")]))
    check("arithmetic to the cent: 1 win, 2 losses, net +1.50, "
          "avg +0.50/graded fire",
          s["wins"] == 1 and s["losses"] == 2 and s["net_usd"] == 1.5
          and s["avg_usd"] == 0.5 and s["graded"] == 3,
          json.dumps({k: s[k] for k in ("wins", "losses",
                                        "net_usd", "avg_usd")}))
    check("the report and the ledger landed; junk rows priced around",
          _out.exists() and _led.exists()
          and len(_led.read_text(encoding="utf-8").strip()
                  .splitlines()) == 1,
          f"txt={_out.exists()} led={_led.exists()}")

    # 2. THE HONEST ZEROS — missing files never fabricate.
    _fires.unlink()
    _trades.unlink()
    _led.write_text("", encoding="utf-8")
    s2 = dch.run_study()
    check("empty world: no fires prices honest zeros (no crash, no "
          "invented rows)",
          s2["fires_total"] == 0 and s2["graded"] == 0
          and s2["net_usd"] == 0.0 and s2["rows"] == [],
          json.dumps(s2)[:160])

    # 3. THE DRIVER — fast-clock ticks, clean stop.
    _fires.write_text(json.dumps(
        {"pair": "AAA/USD",
         "fired_at": "2026-09-20T05:00:00-06:00"}) + "\n",
        encoding="utf-8")
    _trades.write_text(json.dumps(
        {"pair": "AAA/USD", "sold_at": "2026-09-20T05:30:00-06:00",
         "pnl_net": 4.0, "held_min": "30"}) + "\n", encoding="utf-8")
    stop = threading.Event()
    th = threading.Thread(target=dch.monthly_driver_loop,
                          args=(stop, 0.05, 0.2), daemon=True)
    th.start()
    time.sleep(1.2)
    stop.set()
    th.join(timeout=5)
    led = [json.loads(l) for l in _led.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("driver: appended per tick and stopped cleanly",
          not th.is_alive() and len(led) >= 2, f"{len(led)} records")

    # 4. THE FAMILY — the scanner boots the climb-out driver.
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    check("family: the scanner boots the climb-out hindsight driver "
          "with the family's conventions",
          'name="climbouthindsight-monthly"' in bs_src
          and "appends _climbouthindsight_monthly.jsonl" in bs_src
          and "_dch.FIRST_DELAY_S" in bs_src
          and dch.FIRST_DELAY_S == 925.0
          and dch.MONTHLY_PERIOD_S == 30 * 86400.0,
          "driver block missing or drifted")
finally:
    (dch.FIRES_FILE, dch.TRADES_FILE, dch.LEDGER, dch.OUT) = _real

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
(scratch_space.report_temp('_climbouthindsight_check.txt')).write_text(
    "\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
sys.exit(1 if FAIL else 0)
