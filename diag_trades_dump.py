"""Trade ledger dump (Sep 4): the bot's trades table records every buy/sell
with a reason — this is the ground truth for the XMR -> XRP roll and whatever
closed Postgres position #198 (the live XRP row) without a Kraken sell."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
out = []

url = (os.environ.get("DB195374BC_DATABASE_URL")
       or os.environ.get("DB1005AA9E_DATABASE_URL"))
if url:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.connect() as conn:
            # find the trades table shape first
            cols = [r[0] for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name='trades' ORDER BY ordinal_position"))]
            out.append(f"trades columns: {cols}")
            rows = conn.execute(text(
                "SELECT * FROM trades ORDER BY id DESC LIMIT 20")).mappings().all()
            out.append(f"last {len(rows)} trades (newest first):")
            for r in rows:
                keep = {k: r[k] for k in (
                    "id", "ts", "timestamp", "side", "pair", "volume", "price",
                    "cost", "fee", "status", "kraken_txid", "reason",
                    "confidence", "paper_mode") if k in r}
                out.append(str(keep))
    except Exception as exc:
        out.append(f"query error: {exc}")

txt = "\n".join(out)
Path(__file__).parent.joinpath("trades_dump.txt").write_text(txt, encoding="utf-8")
print("DONE -> trades_dump.txt")
