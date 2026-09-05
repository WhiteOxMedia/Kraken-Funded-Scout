"""POPCAT's own 24h move at the 08:57 crown (Sep 4): the carve-out
threshold must catch the case that motivated it. Real candles."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
out = []

try:
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    oh = ex.fetch_ohlcv("POPCAT/USD", timeframe="1h", limit=60)
    by_hour = {}
    for c in oh:
        t = datetime.fromtimestamp(c[0] / 1000, MST)
        by_hour[(t.month, t.day, t.hour)] = c[4]
    # the 08:00 candle close on Sep 4 vs 24h earlier (Sep 3 08:00)
    now_p = by_hour.get((9, 4, 8))
    then_p = by_hour.get((9, 3, 8))
    if now_p and then_p:
        move = (now_p / then_p - 1) * 100
        out.append(f"POPCAT close Sep 3 08:00: {then_p:.5f}")
        out.append(f"POPCAT close Sep 4 08:00: {now_p:.5f}")
        out.append(f"own 24h move at crown time: {move:+.2f}%")
        out.append(f"FLAT lone-wolf tiers: >= +5% (25 pts) / >= +8% (35 pts)")
        out.append(f"-> at +{max(move,0):.1f}%: "
                   f"{'BIG tier (>=8)' if move >= 8 else 'MIN tier (>=5)' if move >= 5 else 'NEITHER tier'}")
except Exception as exc:
    out.append(f"candle read failed: {exc}")

txt = "\n".join(out)
BASE.joinpath("popcat_ownmove.txt").write_text(txt, encoding="utf-8")
print("DONE -> popcat_ownmove.txt")
