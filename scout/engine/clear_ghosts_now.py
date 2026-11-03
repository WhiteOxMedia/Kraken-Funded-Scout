"""Clear ghost bridge records — ONLY those whose assets are confirmed gone
from the actual Kraken wallet (same rule as the Console Integrity button)."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import sqlite3
import keyring
from kraken_api import KrakenAPI
from signal_bridge import mark_closed, get_open_positions

lines = []

# ground truth: the wallet
api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
kr = KrakenAPI(api_key, api_secret)
balances = kr.fetch_balance()
# collect all free+used base assets with nonzero balance
wallet_assets = set()
for _, info in balances.items():
    if isinstance(info, dict):
        total = float(info.get("total") or 0)
        if total > 0:
            # key format varies ('BTC' or ('BTC','free')) — handle by re-walking
            pass
# simpler: walk raw keys
for k, v in balances.items():
    try:
        if isinstance(v, dict):
            amt = float(v.get("total") or 0)
        else:
            amt = float(v or 0)
        if amt > 1e-9 and "/" not in str(k):
            wallet_assets.add(str(k).split(".")[0])  # strip .S/.EQ suffixes
    except Exception:
        pass
lines.append(f"wallet holds: {sorted(wallet_assets)}")

open_positions = get_open_positions()
lines.append(f"bridge 'filled' records: {len(open_positions)}")
for pos in open_positions:
    pair = pos["pair"]
    base = pair.split("/")[0]
    signal_id = pos.get("signal_id")
    if base in wallet_assets:
        lines.append(f"  KEEP {pair} — {base} really is in the wallet (real position)")
        continue
    try:
        mark_closed(signal_id, 0.0)
        lines.append(f"  CLEARED {pair} ({signal_id}) — {base} not in wallet → ghost closed")
    except Exception as exc:
        lines.append(f"  ERROR {pair}: {exc}")

# final count
remaining = get_open_positions()
lines.append(f"remaining bridge 'filled': {len(remaining)}")

out = BASE / "ghost_clear.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
