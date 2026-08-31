"""One-off check: which coins are actually moving (24h), and are they in
the 54-asset universe the 5-rotation tournament scans? (User question,
Aug 31: 'you said all the coins that moved last night were not in the 56?')"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ccxt
from config import TradingConfig

FIAT = {"EUR", "GBP", "CHF", "AUD", "CAD", "JPY", "NZD", "USDT", "USDC"}

cfg = TradingConfig()
uni = set(cfg.trading_pairs)
print(f"universe size: {len(uni)}")

ex = ccxt.kraken({"enableRateLimit": True})
tickers = ex.fetch_tickers()
rows = []
for sym, t in tickers.items():
    if not sym.endswith("/USD"):
        continue
    if sym.split("/")[0] in FIAT:
        continue
    pct = t.get("percentage")
    if pct is None:
        continue
    rows.append((float(pct), sym))

rows.sort(reverse=True)

print(f"\nTOP 20 MOVERS (24h) — * = in the {len(uni)}-asset universe:")
in_top10 = 0
for i, (pct, sym) in enumerate(rows[:20], 1):
    mark = "*" if sym in uni else " "
    if i <= 10 and sym in uni:
        in_top10 += 1
    print(f"{i:>2}. {mark} {pct:+6.1f}%  {sym}")

print(f"\nof the top 10 movers, {in_top10} are in the universe")
print("\nbest IN-UNIVERSE movers:")
for pct, sym in rows:
    if sym in uni:
        print(f"    {pct:+6.1f}%  {sym}")
    # stop after the 5th universe hit
    if sum(1 for p, s in rows[:rows.index((pct, sym)) + 1] if s in uni) >= 5:
        break
