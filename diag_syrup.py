"""Clean dump: bridge signals since Sep 1 + current open positions -> syrup_check.txt"""
import sqlite3
from pathlib import Path

out = []
conn = sqlite3.connect(Path(__file__).parent / "signals.db")
conn.row_factory = sqlite3.Row

out.append("=== signals since 2026-09-01 (all) ===")
for r in conn.execute(
    "SELECT signal_id, pair, action, confidence, source, status, timestamp, "
    "executed_at, execution_price, closed_at, pnl "
    "FROM signals WHERE timestamp > '2026-09-01' ORDER BY rowid"
):
    out.append(
        f"{r['timestamp'][:19]} {r['pair']:<12} {r['action']:<4} conf={r['confidence']:<5} "
        f"src={r['source'] or ''} status={r['status']} "
        f"exec={r['executed_at'] or ''} px={r['execution_price'] or ''} "
        f"closed={r['closed_at'] or ''} pnl={r['pnl'] or ''}"
    )

out.append("")
out.append("=== open positions (status=filled, not closed) ===")
for r in conn.execute(
    "SELECT signal_id, pair, action, confidence, source, status, timestamp, "
    "executed_at, execution_price, highest_price "
    "FROM signals WHERE status='filled' ORDER BY rowid DESC LIMIT 10"
):
    out.append(
        f"{r['timestamp'][:19]} {r['pair']:<12} {r['action']} status={r['status']} "
        f"exec={r['executed_at'] or ''} px={r['execution_price'] or ''} "
        f"high={r['highest_price'] or ''} src={r['source'] or ''}"
    )

conn.close()

txt = "\n".join(out)
Path(__file__).parent.joinpath("syrup_check.txt").write_text(txt, encoding="utf-8")
print(txt)
