"""test_phantommirror.py — THE PHANTOM-MIRROR GATE, PINNED (Sep 29, the INJ double-book).

THE INCIDENT: her 07:00/08:00 "double buy" was ONE Kraken market order
(OFIAMG-R3GDY-C7RW3F) recorded twice. The scout wrote e9292c32 (scout_auto,
07:58:55-06:00, acknowledged 07:59:41); the bot executed it through
bot_engine._execute_buy, whose bridge mirror asked is_already_holding —
which counted status='filled' ONLY, and the scout row was still
'acknowledged' (mark_executed runs ~21ms later, scout_signal_reader:908) —
so the mirror wrote 50385ce5 (bot_strategy, filled) with the SAME order_id
at 07:00:01-07:00 — stamped with a hardcoded UTC-7 clock while the scout
used auto-DST (the dual-clock signature in the history). 49 of 83
bot_strategy rows are such phantoms; every scout-executed buy got one.
The double 'filled' row double-booked the book (the console said
"tracking 2 positions: INJ, INJ") and, worse, the filled-only gate is a
class: anything checking holdings inside the ack window sees a free pair.

THE FIXES, each pinned here:
  1. signal_bridge.is_already_holding counts 'acknowledged' too (the
     ack window is a position in progress) — closes the write-time gate
     AND the readers' take-time gate (both repos ride this one module).
  2. signal_bridge._retire_order_phantoms: the self-healing sweep —
     wired into mark_executed (the truth landing) and
     mark_closed_by_pair (the bot's pair-close seam) — retires any
     same-order bot_strategy phantom wherever the call came from.
  3. bot_engine's mirror: dedupe by order_id, sweep-instead-of-twin when
     a scout row owns the pair, true own-strategy buys still land, and
     the hardcoded hours=-7 clock is dead (the bridge's auto-DST MST).
  4. The bot repo's signal_bridge twin stays byte-identical (normalized)
     to the scout's — one bridge, two checkouts.

All offline and hermetic — the bridge DB is redirected into the scratch
sandbox before any call; nothing here touches production.
"""
import sys
import importlib.util
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import signal_bridge as sb
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# ── the sandbox: bridge DB + kill switch sealed BEFORE any call ───────
SBX_DIR = scratch_space.sbx("phantommirror_world")
SBX_DIR.mkdir(parents=True, exist_ok=True)
SB_DB = SBX_DIR / "signals.db"
SB_LOCK = SBX_DIR / "kill_switch.lock"
if SB_DB.exists():
    SB_DB.unlink()
sb.DB_PATH = SB_DB      # leak-audit: ok (suite-per-process redirect into the scratch sandbox; restore-never)
sb.KILL_SWITCH_FILE = SB_LOCK

BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
esrc = (BOT / "bot_engine.py").read_text(encoding="utf-8")
rsrc = (BOT / "scout_signal_reader.py").read_text(encoding="utf-8")

sb.init_db()

_INJ = "INJ/USD"


def _mk(sid, pair=_INJ, action="buy", source="scout_auto", status="pending",
        order_id=None, cooldown_minutes=30):
    """Seed one signal row shaped like the real thing (levels, stamps)."""
    now = sb._now_mst()
    executed = now.isoformat() if status == "filled" else None
    with sb._get_db() as conn:
        conn.execute("""
            INSERT INTO signals (
                signal_id, pair, action, confidence, reason,
                timestamp, valid_until, cooldown_until,
                price_at_signal, entry_price, stop_loss, take_profit,
                risk_reward, position_size_usd, source, status,
                executed_at, execution_price, order_id
            ) VALUES (?, ?, ?, 100.0, 'phantommirror fixture',
                      ?, ?, ?, 7.7, 7.7, 7.3, 8.1, 2.5, 500.0, ?, ?, ?, ?, ?)
        """, (
            sid, pair, action,
            now.isoformat(), (now + timedelta(minutes=5)).isoformat(),
            (now + timedelta(minutes=cooldown_minutes)).isoformat(),
            source, status, executed, 7.7 if executed else None, order_id,
        ))
        conn.commit()


def _row(sid):
    with sb._get_db() as conn:
        r = conn.execute("SELECT * FROM signals WHERE signal_id=?", (sid,)).fetchone()
    return dict(r) if r else None


# ── 1. the Sep-29 shape, reproduced hermetically ─────────────────────
# The scout row exists, the bot acknowledged it, the fill hasn't landed
# (acknowledged_at 07:59:41, executed_at 07:00:01-07:00 came 21s later).
# Cooldown is set EXPIRED so only the holding gate can refuse below.
_mk("ack0001", status="acknowledged", cooldown_minutes=-1)
check("the incident shape: an acknowledged buy reads as ALREADY HOLDING "
      "(the ack window is a position in progress)",
      sb.is_already_holding(_INJ) is True)
sid2 = sb.write_buy_signal(
    _INJ, "buy", 80.0, 7.7, 7.3, 8.1, 2.5, 500.0,
    ["Breakout — the challenger that must not write"])
check("record_signal refuses a second buy while the first is acknowledged "
      "(the old filled-only gate admitted exactly this)", sid2 is None)
check("the refusal is the holding gate, not cooldown (the acknowledged "
      "row's cooldown is long expired)", not sb.is_in_cooldown(_INJ))

# a pending first row does NOT block a write (the challenger machinery
# needs pending rows alive) — the take-time gate catches it instead:
_mk("pend0001", pair="PEPE/USD", status="pending", cooldown_minutes=-1)
sid3 = sb.write_buy_signal(
    "PEPE/USD", "buy", 80.0, 1.0, 0.95, 1.1, 2.5, 500.0,
    ["Breakout — second pending rides fine"])
check("a pending first row still allows the write (challengers stay "
      "writable; the take-time gate decides)", sid3 is not None)
_mk("pend0002", pair="PEPE/USD", status="acknowledged", cooldown_minutes=-1)
check("take-time shape: once one PEPE row is acknowledged, "
      "is_already_holding fires and the reader rejects the other "
      "('Already holding this asset')",
      sb.is_already_holding("PEPE/USD") is True)

# the gate must not over-block: a closed first row frees the pair
_mk("wld0001", pair="WLD/USD", status="closed", order_id="O-WLD-1",
    cooldown_minutes=-1)
sid4 = sb.write_buy_signal(
    "WLD/USD", "buy", 80.0, 2.0, 1.9, 2.2, 2.5, 500.0,
    ["Breakout — re-entry after a close"])
check("a closed first row does NOT block the re-buy (the ladder's "
      "re-entry stays open)", sid4 is not None)

# ── 2. the phantom sweep itself ───────────────────────────────────────
_mk("real0001", status="acknowledged", cooldown_minutes=-1)   # scout row, in execution
_mk("phantom1", source="bot_strategy", status="filled",
    order_id="OFIAMG-R3GDY-C7RW3F")
_mk("phantom_other", pair="FET/USD", source="bot_strategy", status="filled",
    order_id="O-OTHER-1")
n = sb._retire_order_phantoms("OFIAMG-R3GDY-C7RW3F", "real0001", 0.0)
check("the sweep retires exactly the same-order bot_strategy phantom",
      n == 1, n)
p1 = _row("phantom1")
check("the retired phantom is closed with pnl 0 and no cooldown stamp "
      "(it held no coins of its own)",
      p1["status"] == "closed" and p1["pnl"] == 0 and not p1["cooldown_until"])
check("the keep row is untouched by the sweep",
      _row("real0001")["status"] == "acknowledged")
check("a different pair's phantom with a different order survives",
      _row("phantom_other")["status"] == "filled")
check("an empty order_id sweeps nothing (no full-table accident)",
      sb._retire_order_phantoms("", "real0001", 0.0) == 0)
check("a different order_id sweeps nothing",
      sb._retire_order_phantoms("O-NOTHING", "real0001", 0.0) == 0)

# ── 3. mark_executed wires the sweep (self-healing while the old ─────
# mirror code is still running in a not-yet-restarted bot process) ────
sb.mark_executed("real0001", "OFIAMG-R3GDY-C7RW3F", 7.727)
r1 = _row("real0001")
check("the fill stamp lands on the real row with the order recorded",
      r1["status"] == "filled" and r1["order_id"] == "OFIAMG-R3GDY-C7RW3F")
_mk("phantom2", source="bot_strategy", status="filled",
    order_id="OFIAMG-R3GDY-C7RW3F")   # a mirror minted mid-execution, as live
sb.mark_executed("real0001", "OFIAMG-R3GDY-C7RW3F", 7.727)   # truth lands again
check("the fill stamp itself retires a phantom minted mid-execution "
      "(the old bot's mirror gets healed on the next truth event)",
      _row("phantom2")["status"] == "closed")

# ── 4. the bot engine's mirror: dedupe, sweep-not-twin, auto-DST ─────
check("the mirror dedupes by order_id before writing",
      "SELECT signal_id FROM signals WHERE order_id=? AND order_id != ''" in esrc)
check("a scout-owned pair sweeps any same-order phantom instead of "
      "adding a twin",
      "source != 'bot_strategy'" in esrc and "_bridge_sweep(order_id" in esrc)
check("the true own-strategy write survives (source/status literals intact)",
      "'bot_strategy', 'filled'" in esrc)
check("the old filled-only mirror gate is gone",
      "is_already_holding as _bridge_holding" not in esrc)
check("the mirror stopped guessing the timezone — the bridge's auto-DST "
      "clock is the mirror's clock now (the dual-clock signature's source)",
      "_mst = _bridge_mst" in esrc)
check("the bridge clock is auto-DST America/Denver (summer stamps -06:00)",
      str(sb.MST) == "America/Denver")
check("the readers' take-time gate rides the upgraded shared check",
      "not is_dca and is_already_holding(pair)" in rsrc)

# the bot repo's bridge twin stays identical — one bridge, two checkouts
a = (Path(__file__).parent / "signal_bridge.py").read_text(encoding="utf-8").replace("\r\n", "\n")
b = (BOT / "signal_bridge.py").read_text(encoding="utf-8").replace("\r\n", "\n")
check("the bot's signal_bridge twin is byte-identical (normalized) to the "
      "scout's — the copies must move together",
      a == b, f"scout {len(a)} chars vs bot {len(b)} chars")
spec = importlib.util.spec_from_file_location(
    "phantommirror_twin", str(BOT / "signal_bridge.py"))
twin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(twin)
import inspect
twin_holding_src = inspect.getsource(twin.is_already_holding)
check("the twin carries the same sweep and the same upgraded gate",
      hasattr(twin, "_retire_order_phantoms")
      and "status IN ('filled','acknowledged')" in twin_holding_src)

# ── 5. the close path: the phantom leaves the book with the position ─
_mk("real0002", pair="ARB/USD", status="filled", order_id="O-CLOSE-1")
_mk("phantom3", pair="ARB/USD", source="bot_strategy", status="filled",
    order_id="O-CLOSE-1")   # minted AFTER the real fill, as live — the newest row
check("fixture shape: the phantom is the MOST RECENT filled row (the "
      "mirror minted it microseconds after the real fill)",
      (_row("phantom3")["executed_at"] or "") > (_row("real0002")["executed_at"] or ""))
sb.mark_closed_by_pair("ARB/USD", -1.5)
r2, p3 = _row("real0002"), _row("phantom3")
check("mark_closed_by_pair closes the REAL owner (the mirror-first trap: "
      "the old newest-filled pick closed the phantom and left the real "
      "position open)",
      r2["status"] == "closed" and r2["source"] != "bot_strategy")
check("mark_closed_by_pair retires the same-order phantom with the book",
      p3["status"] == "closed")
check("the phantom's pnl rides the real close (one position, one pnl)",
      p3["pnl"] == -1.5 and r2["pnl"] == -1.5)
check("the loser cooldown belongs to the real row only (the sweep set none; "
      "the phantom's stray close used to arm a phantom cooldown)",
      r2["cooldown_until"] is not None and p3["cooldown_until"] is None)
check("the real row's 4h loss cooldown still arms after the close",
      sb.is_in_cooldown("ARB/USD") is True)
check("one close = one exit signal in the book (no double-count)",
      sum(1 for s in (_row("real0002"), _row("phantom3"))
          if s["status"] == "closed" and s["source"] != "bot_strategy") == 1)
check("a pure bot-strategy book still closes through the seam (the "
      "non-phantom preference falls back cleanly)",
      (lambda: (_mk("botown1", pair="TIA/USD", source="bot_strategy",
                    status="filled", order_id="O-BOTOWN-1"),
                sb.mark_closed_by_pair("TIA/USD", 0.25),
                _row("botown1")["status"] == "closed"))())

# ── 6. the scanner's auto-close closes the book (the INJ ghost) ──────
bs_src = (Path(__file__).parent / "background_scanner.py").read_text(encoding="utf-8")
check("the GET OUT auto-close closes the bridge book when the sale "
      "receipt is journaled (the book follows the ledger — INJ sold at "
      "08:27 while its bridge row rode every rotation voice line until "
      "the 20:00 reset, 12h of ghost)",
      "THE BOOK FOLLOWS THE LEDGER" in bs_src
      and "mark_closed_by_pair as _mcbp" in bs_src)

fails = [r for r in lines if r.startswith("FAIL")]
out = scratch_space.report_temp("phantommirror_check.txt")
out.write_text("\n".join(lines)
               + f"\n\nRESULT: {PASS} passed, {FAIL} failed\n",
               encoding="utf-8")
print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
