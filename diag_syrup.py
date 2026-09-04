"""Check whether the bot takes the SYRUP scout_mover signal (Sep 2 10:27 popup)."""
import json
import sqlite3
from pathlib import Path

base = Path(__file__).parent
conn = sqlite3.connect(base / "signals.db")
conn.row_factory = sqlite3.Row
out = []

out.append("=== JUP signals since 14:00 Sep 2 ===")
for r in conn.execute(
    "SELECT pair, action, confidence, source, status, execution_reason, "
    "entry_price, stop_loss, take_profit, tide_state, tide_pct, timestamp "
    "FROM signals WHERE pair LIKE '%JUP%' AND timestamp > '2026-09-02 14:00' "
    "ORDER BY rowid"
):
    out.append(f"{r['timestamp'][:19]} {r['pair']} {r['action']} conf={r['confidence']} "
               f"src={r['source']} status={r['status']} reason={r['execution_reason'] or ''} "
               f"entry={r['entry_price']} sl={r['stop_loss']} tp={r['take_profit']} "
               f"tide={r['tide_state']}/{r['tide_pct']}")
conn.close()

out.append("")
out.append("=== console_state.json (tier) ===")
try:
    out.append((base / "console_state.json").read_text())
except Exception as exc:
    out.append(f"read error: {exc}")

out.append("")
out.append("=== contest_watch.json JUP entry ===")
try:
    w = json.loads((base / "contest_watch.json").read_text())
    for k, v in w.items():
        if "JUP" in k:
            out.append(f"{k}: {json.dumps(v, indent=1)}")
except Exception as exc:
    out.append(f"read error: {exc}")

out.append("")
out.append("=== JUP window drawdown (engine cell) ===")
try:
    import sys
    sys.path.insert(0, str(base))
    import contest_account as _ca
    out.append(f"window_drawdown_pct('JUP/USD') = {_ca.window_drawdown_pct('JUP/USD')}")
    acct = _ca.load_state()
    out.append(f"account: {acct}")
    out.append(f"max_entry_usd at that dd = {_ca.max_entry_usd(float(acct['current_balance']), _ca.window_drawdown_pct('JUP/USD'), float(acct['start_balance'])):.0f}")
except Exception as exc:
    out.append(f"engine error: {exc}")

txt = "\n".join(out)
base.joinpath("syrup_check.txt").write_text(txt, encoding="utf-8")
print(txt)
