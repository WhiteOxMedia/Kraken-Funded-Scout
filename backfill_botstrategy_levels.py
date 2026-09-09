"""Backfill the bot_strategy rows' stop_loss / take_profit (user rule,
Sep 9: "make bot_strategy popup records stamp their tp/sl like scout_auto
does, so the replay-live diag can grade the mover funnel too").

The bot's own-strategy buy path stamped None forever (its Signal dataclass
never carried levels) while the exit engine enforced the CONFIG rails --
stop 5%, take 10% of the filled price (config.py, unchanged since the
2026-08-05 commit, which predates the earliest bot_strategy row of Aug 21).
So the honest historical stamp is exactly those rails off the FILLED price
(the price the exit engine measured; the bot's inserts stamp filled_price
into both entry_price and execution_price, so on the historical rows they
are identical -- Copilot, Sep 9: prefer the fill anyway, so a re-run on a
db where fills differ from quotes still stamps what the engine enforced).

The forward fix lives in the bot repo (bot_engine.py stamps
config.stop_loss_pct / config.take_profit_pct at insert time). This script
retrofits the rows already on disk so the diag can grade them today, and
is idempotent: a second run finds nothing to do and exits 0.

Run:  .venv\\Scripts\\python.exe backfill_botstrategy_levels.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# ONE definition of the rails: the diag's bot_levels (the graders' own
# numbers) -- this script must stamp exactly what the diag grades.
from diag_replaylive import bot_levels

DB = Path(__file__).parent / "signals.db"


def levels_for_row(entry, execution):
    """The rails off the price the exit engine actually measured -- the
    FILLED price (execution_price) when the row carries one, else the
    quoted entry."""
    base = execution if (execution is not None
                         and float(execution) > 0) else entry
    return bot_levels(base)


def main() -> int:
    if not DB.exists():
        print("signals.db not found -- nothing to do")
        return 1
    bak = DB.with_suffix(".db.bak_botstrategy")
    if not bak.exists():
        # WAL-SAFE backup (Copilot, Sep 9): a byte copy of the main db
        # file misses uncheckpointed -wal content. The sqlite backup API
        # takes a consistent snapshot of the database AS IT IS, WAL
        # included.
        src = sqlite3.connect(str(DB), timeout=15)
        dst = sqlite3.connect(str(bak))
        src.backup(dst)
        dst.close()
        src.close()
        print(f"backup (sqlite snapshot API, WAL-safe): {bak.name}")

    sc = sqlite3.connect(str(DB), timeout=15)
    rows = sc.execute(
        "SELECT signal_id, entry_price, execution_price FROM signals "
        "WHERE source='bot_strategy' AND action='buy' "
        "AND entry_price IS NOT NULL AND entry_price > 0 "
        "AND take_profit IS NULL AND stop_loss IS NULL"
    ).fetchall()
    print(f"rows to backfill: {len(rows)}")
    n = 0
    for sid, entry, execution in rows:
        sl, tp = levels_for_row(entry, execution)
        sc.execute(
            "UPDATE signals SET stop_loss=?, take_profit=? "
            "WHERE signal_id=?",
            (sl, tp, sid),
        )
        n += 1
    sc.commit()
    # The completion check counts only the rows this script OWNS:
    # eligible buys still missing levels. (Copilot, Sep 9: counting every
    # tp-null bot_strategy row would exit non-zero on non-buy rows or
    # invalid-entry rows even though no eligible row was missed.)
    left = sc.execute(
        "SELECT COUNT(*) FROM signals WHERE source='bot_strategy' "
        "AND action='buy' AND entry_price IS NOT NULL "
        "AND entry_price > 0 AND take_profit IS NULL"
    ).fetchone()[0]
    total = sc.execute(
        "SELECT COUNT(*) FROM signals WHERE source='bot_strategy'"
    ).fetchone()[0]
    sc.close()
    print(f"backfilled: {n}  |  bot_strategy rows now: {total}, "
          f"eligible still tp-null: {left}")
    return 0 if left == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
