"""What did the bot's overnight trades actually earn? Full P&L accounting."""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        # every position opened since Sun 20:00 MST (Sun 8 PM = 02:00 UTC Mon)
        rows = conn.execute(text(
            "SELECT id, pair, entry_price, entry_volume, entry_timestamp, exit_price, "
            "exit_volume, exit_timestamp, is_open "
            "FROM positions WHERE entry_timestamp >= '2026-08-24 02:00' "
            "ORDER BY id").fetchall() if False else text(
            "SELECT id, pair, entry_price, entry_volume, entry_timestamp "
            "FROM positions WHERE entry_timestamp >= '2026-08-24 02:00' ORDER BY id")).fetchall()
        lines.append(f"positions since Sun 8 PM MST: {len(rows)}")
        pos_ids = [r[0] for r in rows]

        # pair them with trades to compute P&L per round trip
        trades = conn.execute(text(
            "SELECT side, pair, volume, price, cost, fee, timestamp, reason "
            "FROM trades WHERE timestamp >= '2026-08-24 02:00' ORDER BY timestamp")).fetchall()
        lines.append(f"trades since Sun 8 PM MST: {len(trades)}")
        lines.append("")
        total_cost = 0.0
        total_rev = 0.0
        total_fees = 0.0
        lines.append(f"{'time':<7} {'side':<5} {'pair':<10} {'vol':>10} {'price':>10} {'usd':>8}")
        for t in trades:
            usd = (t[4] or 0)
            fee = (t[5] or 0)
            total_fees += fee
            if t[0] == "buy":
                total_cost += usd
            else:
                total_rev += usd
            lines.append(f"{str(t[6])[11:16]:<7} {t[0]:<5} {t[1]:<10} {t[2]:>10.4g} {t[3]:>10.5g} ${usd:>7.2f}")
        lines.append("")
        lines.append(f"total bought:  ${total_cost:,.2f}")
        lines.append(f"total sold:    ${total_rev:,.2f}")
        lines.append(f"total fees:    ${total_fees:,.2f}")
        lines.append(f"net P&L:       ${total_rev - total_cost - total_fees:+,.2f}")
        lines.append(f"round trips:   {len(trades) // 2}")

        # reason strings (why each exit)
        lines.append("")
        lines.append("exit reasons:")
        for t in trades:
            if t[0] == "sell" and t[7]:
                lines.append(f"  {str(t[6])[11:16]} {t[1]}: {str(t[7])[:110]}")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "night_pnl.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
