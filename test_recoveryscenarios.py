"""test_recoveryscenarios.py — THE OPS WHAT-IF DESK, PINNED (Sep 29).

ops/recovery_scenarios.py prices hold/add/exit what-ifs from live
prices, any time. The day it was built, the INJ ghost had just proved
the cost of an unpriced guess: the position everyone could SEE had
actually been sold at 08:27, and the 20:00 reset closed the ghost
12 hours later. This desk exists so the next decision is priced, not
gutted — and so it can never disagree with the book the console keeps.

Pins, all hermetic (watch loader, account state, bridge gate and the
drawdown profile are rebound into the sandbox; the Kraken price seam is
never touched — scenarios take the price as an argument):

  HOLD/EXIT reproduce the real 08:27 INJ journal row to the cent
  (entry 7.727, exit 7.742, size 34.351421645 -> gross 0.07, cost
  0.03, net 0.04 — the platform's own pnl_net_usd, not a re-derivation);
  balance_if_booked is the ledger arithmetic without the booking;
  ADD rides the platform's own guard (window_drawdown_pct ->
  max_entry_usd -> entry_size_usd), prices the operator's number, and
  repeats the gate's truth (0 add when the 3% buffer is gone);
  the bridge re-entry verdict is the REAL gate, fail-open with a named
  reason; UNPRICED rows refuse to invent numbers; the tool is
  structurally read-only (no save_state, no apply_pnl, no order
  endpoint, no file writes anywhere in it).
"""
import sys
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "ops"))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs      # for the loader seam (module cached: the tool's from-import resolves to THIS object)
import contest_account as ca
import signal_bridge as sb           # imported + sealed BEFORE the tool's gate runs (the from-import resolves to THIS object)
import recovery_scenarios as rs      # the ops tool (ops/ on the path above)

# ── the sandbox: watch file, state file, bridge DB, drawdown profile ──
SBX = Path(tempfile.mkdtemp())       # leak-audit: ok (windows restored in the finally below; the mkdtemp world itself is suite-per-process)
SB_WATCH = SBX / "contest_watch.json"
SB_STATE = SBX / "contest_account.json"
SB_DB = SBX / "signals.db"

_real_watch = bs.CONTEST_WATCH_JSON
_real_state = ca.STATE_FILE
_real_db = sb.DB_PATH
_real_dd = ca.window_drawdown_pct

try:
    sb.DB_PATH = SB_DB               # the bridge gate must never touch the live DB from here
    ca.window_drawdown_pct = lambda pair: 3.0   # the profile fallback: deterministic guard arithmetic
    bs.CONTEST_WATCH_JSON = str(SB_WATCH)
    ca.STATE_FILE = SB_STATE

    _added = (datetime.now(ca.MST) - timedelta(minutes=30)).isoformat()
    SB_WATCH.write_text(json.dumps({
        "INJ/USD": {"entry": 7.727, "size": 34.351421645, "goal_pct": 10.0,
                    "added": _added, "high": 7.783},
        "GHOST/USD": None,           # the merge-save's deletion marker
        "BAD/USD": "not a row",
    }), encoding="utf-8")
    SB_STATE.write_text(json.dumps({
        "tier": 10000, "start_balance": 10000.0,
        "current_balance": 10015.54,
    }), encoding="utf-8")

    ROW = {"pair": "INJ/USD", "entry": 7.727, "size": 34.351421645,
           "goal_pct": 10.0, "added": _added}
    LIVE = 7.742          # the price the real 08:27 exit took
    BAL = 10015.54        # today's live balance, as a fixture
    NOW = datetime.now(ca.MST)

    # ── 1. the loader seam: the exact tombstone-free view ─────────────
    book = rs.watch_book()
    check("watch_book rides the scanner's tombstone-free loader (the "
          "None marker never reaches a scenario)",
          "INJ/USD" in book and "GHOST/USD" not in book, sorted(book))
    check("the account balance is read from the state file, read-only",
          rs.account_balance() == 10015.54, rs.account_balance())

    # ── 2. HOLD: the live P&L, the goal, the profiled worst window ────
    hold = rs.scenario_hold(ROW, LIVE, NOW)
    check("HOLD net reproduces the real 08:27 INJ journal row to the "
          "cent (gross 0.07 / cost 0.03 / net 0.04 — the platform's own "
          "pnl_net_usd, never re-derived here)",
          (hold["pnl_gross"], hold["cost_rt"], hold["pnl_net"])
          == (0.07, 0.03, 0.04), hold)
    check("HOLD % from entry is priced",
          hold["pct_from_entry"] == 0.19, hold["pct_from_entry"])
    check("HOLD names the goal and what live still owes it "
          "(target 8.4997 needs +9.79% from live)",
          hold["target_price"] == 8.4997
          and abs(hold["goal_needs_pct_from_live"] - 9.79) < 0.01, hold)
    check("HOLD carries minutes held (~30) and the profiled worst window "
          "priced as a NET (below entry, a loss after costs)",
          hold["held_min"] in (29, 30, 31)
          and hold["window_dd_pct"] == 3.0
          and hold["window_worst_exit"] < hold["entry"]
          and hold["window_worst_net"] < 0, hold)

    # ── 3. EXIT: the ledger arithmetic WITHOUT the booking ────────────
    ex = rs.scenario_exit(ROW, LIVE, BAL, NOW)
    check("EXIT at the real exit price nets 0.04 and moves the ledger "
          "arithmetic to 10015.58 — while booking nothing",
          ex["pnl_net"] == 0.04
          and ex["balance_if_booked"] == 10015.58, ex)
    ex_goal = rs.scenario_exit(ROW, 8.4997, BAL, NOW)
    check("EXIT at the original goal (+10%) prices the whole ride "
          "(net 3.41 after the round trip)",
          ex_goal["pnl_net"] == 3.41, ex_goal)
    check("EXIT minutes held ride the row's added stamp",
          ex["held_min"] in (29, 30, 31), ex["held_min"])

    # ── 4. ADD: the platform's own guard, the operator's number ───────
    add = rs.scenario_add(ROW, LIVE, BAL, None)
    # guard: buf = 10015.54 - 10000*0.97 = 315.54; cap = (315.54*0.34)/0.03
    check("ADD's guard cap is the 3% guard's own arithmetic "
          "(buffer 315.54 -> max entry 3576.12)",
          abs(add["guard_max_entry_usd"] - 3576.12) < 0.5,
          add["guard_max_entry_usd"])
    check("ADD's default size is the guard size (min of guard and account)",
          add["add_size_usd"] == add["guard_size_usd"]
          and abs(add["add_size_usd"] - 3576.12) < 0.5, add)
    check("ADD blends the entry between the original and the add price "
          "(adding above entry pulls the average up, never below the "
          "original)",
          ROW["entry"] < add["blended_entry"] <= LIVE
          and add["blended_vs_original_entry_pct"] > 0, add)
    check("ADD prices the whole blended book out at the ORIGINAL goal",
          add["blended_exit_at_original_goal_net"] > 0
          and add["blended_exit_at_original_goal_net"] > ex_goal["pnl_net"],
          add["blended_exit_at_original_goal_net"])

    add_op = rs.scenario_add(ROW, LIVE, BAL, 400.0)
    check("the operator's --add-usd is priced and judged by the guard "
          "(400 sits inside the 3576 cap)",
          add_op["requested_add_usd"] == 400.0
          and add_op["add_size_usd"] == 400.0
          and add_op["within_guard"] is True, add_op)
    add_big = rs.scenario_add(ROW, LIVE, BAL, 5000.0)
    check("an add OVER the guard's cap is stated, not suggested "
          "(5000 > 3576.12 -> within_guard False)",
          add_big["within_guard"] is False
          and add_big["add_size_usd"] == 5000.0, add_big)
    add_broke = rs.scenario_add(ROW, LIVE, 9650.0, None)
    check("the 3% guard's truth repeats: below the death line the buffer "
          "is gone and the add is ZERO — no new risk",
          add_broke["guard_max_entry_usd"] == 0.0
          and add_broke["add_size_usd"] == 0.0
          and "blended_entry" not in add_broke, add_broke)

    # ── 5. the re-entry gate: the REAL bridge verdict, fail-open ──────
    sb.is_in_cooldown = lambda pair: True    # sandboxed above (SB_DB): the live DB is unreachable from here
    open_gate, note = rs.bridge_reentry_gate("INJ/USD")
    check("a holding bridge cooldown says HOLDS with the reason "
          "(30-min window / 4h loser lock)",
          open_gate is False and "cooldown holds" in note, note)
    sb.is_in_cooldown = lambda pair: False
    open_gate, note = rs.bridge_reentry_gate("INJ/USD")
    check("an open gate says OPEN", open_gate is True and "open" in note, note)
    def _boom(pair):
        raise RuntimeError("bridge unreadable")
    sb.is_in_cooldown = _boom
    open_gate, note = rs.bridge_reentry_gate("INJ/USD")
    check("a broken gate fails OPEN but NAMES the failure (never a "
          "silent fake pass)",
          open_gate is True and "unreadable" in note, note)

    # ── 6. presentation: UNPRICED refuses to invent numbers ───────────
    out_none = rs.render_row("XXX/USD", ROW, None, False, BAL, [], None, NOW)
    check("an UNPRICED pair prints the guard rail, no scenario lines",
          any("UNPRICED" in l for l in out_none)
          and any("lie with decimals" in l for l in out_none)
          and not any(l.strip().startswith("HOLD") for l in out_none), out_none)
    out_sim = rs.render_row("INJ/USD", ROW, LIVE, True, BAL, [], None, NOW)
    check("a pinned --prices run is labelled SIMULATED PRICE",
          any("SIMULATED PRICE" in l for l in out_sim), out_sim[:3])
    out_norm = rs.render_row("INJ/USD", ROW, LIVE, False, BAL, [8.4997],
                             400.0, NOW)
    check("a live row renders HOLD/EXIT/target/ADD/gate, and repeats "
          "that nothing is booked and an add is a trend bet",
          any(l.strip().startswith("HOLD") for l in out_norm)
          and any(l.strip().startswith("EXIT") for l in out_norm)
          and any("at 8.4997" in l for l in out_norm)
          and any(l.strip().startswith("ADD") for l in out_norm)
          and any("RE-ENTRY gate" in l for l in out_norm)
          and any("nothing is booked here" in l for l in out_norm)
          and any("only if the trend comes back" in l for l in out_norm),
          out_norm)
    out_bad = rs.render_row("BAD/USD", "not a row", LIVE, False, BAL, [],
                            None, NOW)
    check("a non-dict row degrades to the empty-row guard",
          any("empty/tombstoned row" in l for l in out_bad), out_bad)

    # ── 7. the tool is structurally READ-ONLY ─────────────────────────
    src = (Path(__file__).parent / "ops" / "recovery_scenarios.py"
           ).read_text(encoding="utf-8")
    check("the tool never books, never saves state, never opens a file "
          "for write, and never touches an order endpoint",
          "save_state" not in src and "apply_pnl" not in src
          and "place_market" not in src and "open(" not in src)
    check("the arithmetic is the platform's own helpers, not a "
          "re-derivation",
          "ca.pnl_net_usd(" in src and "ca.max_entry_usd(" in src
          and "ca.entry_size_usd(" in src and "ca.window_drawdown_pct" in src)
    check("live prices come from the PUBLIC ticker (no keys, no trading "
          "endpoint) and pinning overrides are honoured",
          "ccxt.kraken" in src and "--prices" in src.replace('`', ''))

finally:
    bs.CONTEST_WATCH_JSON = _real_watch
    ca.STATE_FILE = _real_state
    sb.DB_PATH = _real_db
    ca.window_drawdown_pct = _real_dd
    try:
        del sb.is_in_cooldown            # un-monkeypatch: the real function is the module's own
    except AttributeError:
        pass

fails = [r for r in lines if r.startswith("FAIL")]
import scratch_space   # scratch policy: the check report routes through the router
out = scratch_space.report_temp("recoveryscenarios_check.txt")
out.write_text("\n".join(lines) + f"\n\nRESULT: {PASS} passed, {FAIL} failed\n",
               encoding="utf-8")
print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
