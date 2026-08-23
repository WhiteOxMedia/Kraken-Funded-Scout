"""Fire ONE contest light cycle live — proves the server-side voice works."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import ccxt
from config import TradingConfig
from background_scanner import contest_light

ex = ccxt.kraken({"enableRateLimit": True})
cfg = TradingConfig()
contest_light(ex, cfg)
print("light cycle fired — listen for Zira")
