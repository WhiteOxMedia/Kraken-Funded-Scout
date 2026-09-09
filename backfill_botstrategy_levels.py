"""Backfill the bot_strategy rows' stop_loss / take_profit (user rule,
Sep 9: "make bot_strategy popup records stamp their tp/sl like scout_auto
does, so the replay-live diag can grade the mover funnel too").

The bot's own-strategy buy path stamped None forever (its Signal dataclass
never carried levels) while the exit engine enforced the CONFIG rails --
stop 5%, take 10% of the filled price (config.py, unchanged since the
2026-08-05 commit, which predates the earliest bot_strategy row of Aug 21).
So the honest historical stamp is exactly those rails off entry_price.

The forward fix lives in the bot repo (bot_engine.py stamps
config.stop_loss_pct / config.take_profit_pct at insert time). This script
retrofits the 77 rows already on disk so the diag can grade them today.

Run:  .venv\\Scripts\\python.exe backfill_botstrategy_levels.py
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# ONE definition of the rails: the diag's bot_levels (the graders' own
# numbers) -- this script must stamp exactly what the diag grades.
from diag_replaylive import bot_levels

DB = Path(__file__).parent / "signals.db"


def main() -> int:
    if not DB.exists():
        print("signals.db not found -- nothing to do")
        return 1
    bak = DB.with_suffix(".db.bak_botstrategy")
    if not bak.exists():
        shutil.copy2(DB, bak)
        print(f"backup: {bak.name}")

    sc = sqlite3.connect(str(DB))
    rows = sc.execute(
        "SELECT signal_id, entry_price FROM signals "
        "WHERE source='bot_strategy' AND action='buy' "
        "AND entry_price IS NOT NULL AND entry_price > 0 "
        "AND take_profit IS NULL AND stop_loss IS NULL"
    ).fetchall()
    print(f"rows to backfill: {len(rows)}")
    n = 0
    for sid, entry in rows:
        sl, tp = bot_levels(entry)
        sc.execute(
            "UPDATE signals SET stop_loss=?, take_profit=? "
            "WHERE signal_id=?",
            (sl, tp, sid),
        )
        n += 1
    sc.commit()
    left = sc.execute(
        "SELECT COUNT(*) FROM signals WHERE source='bot_strategy' "
        "AND take_profit IS NULL"
    ).fetchone()[0]
    total = sc.execute(
        "SELECT COUNT(*) FROM signals WHERE source='bot_strategy'"
    ).fetchone()[0]
    sc.close()
    print(f"backfilled: {n}  |  bot_strategy rows now: {total}, "
          f"still tp-null: {left}")
    return 0 if left == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
