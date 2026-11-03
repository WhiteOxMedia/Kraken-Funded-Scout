"""Deep look at Kraken ledger non-trade entries to find how payouts are typed."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

lines = []
try:
    import keyring
    api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
    api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
    import ccxt
    ex = ccxt.kraken({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
    entries = ex.fetch_ledger(limit=200) or []
    lines.append(f"total entries: {len(entries)}")
    types = {}
    for e in entries:
        t = str(e.get("type") or "?")
        types[t] = types.get(t, 0) + 1
    lines.append(f"types: {types}")
    lines.append("— non-trade entries —")
    for e in entries:
        if str(e.get("type")) == "trade":
            continue
        ts = datetime.fromtimestamp((e.get("timestamp") or 0) / 1000, MST)
        lines.append(
            f"{ts.strftime('%b %d %I:%M %p')}  type={e.get('type')}  "
            f"amount={e.get('amount')} {e.get('currency')}  "
            f"direction={e.get('direction')}  after={e.get('afterBalance')}  "
            f"id={e.get('id')}  desc={str(e.get('description') or '')[:60]}")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "payout_check.txt"
scratch_space.report_temp('payout_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
