"""POPCAT reality check (Sep 4 morning): did the coin ACTUALLY reach a
~2.28% goal after the 08:57 crown (the discipline score's $227.72 claim),
within the rotation window? Real 1h candles, no approximation."""
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
    oh = ex.fetch_ohlcv("POPCAT/USD", timeframe="1h", limit=30)
    out.append("POPCAT/USD 1h candles (MST):")
    peak_after_0857 = 0.0
    base_0857 = None
    for c in oh:
        t = datetime.fromtimestamp(c[0] / 1000, MST)
        if t.date().day == 4 and 8 <= t.hour <= 13:
            out.append(f"  {t.strftime('%H:%M')}  O {c[1]:.5f}  H {c[2]:.5f}  "
                       f"L {c[3]:.5f}  C {c[4]:.5f}")
            if t.hour == 8:
                base_0857 = c[4]  # the 08:00 candle close ~ the 08:57 crown price
            if base_0857 and t.hour >= 9:
                peak_after_0857 = max(peak_after_0857, c[2])
    if base_0857:
        move = (peak_after_0857 / base_0857 - 1) * 100
        out.append(f"")
        out.append(f"08:00 close (crown-era base): {base_0857:.5f}")
        out.append(f"highest high 09:00-13:59:      {peak_after_0857:.5f}")
        out.append(f"max move after the crown:      {move:+.2f}%")
        out.append(f"the discipline goal was ~2.28% -> "
                   f"{'PAID' if move >= 2.28 else 'NOT PAID'}")
except Exception as exc:
    out.append(f"candle read failed: {exc}")

txt = "\n".join(out)
BASE.joinpath("popcat_candles.txt").write_text(txt, encoding="utf-8")
print("DONE -> popcat_candles.txt")
