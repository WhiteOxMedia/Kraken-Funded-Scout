"""Poll the shadow recorder's resolver while aged rows cross their 180m window."""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import ccxt, near_miss as nm
ex = ccxt.kraken({"enableRateLimit": True})
for i in range(60):
    n = nm.resolve(ex, None, limit=10)
    if n:
        print(f"{time.strftime('%H:%M:%S')} resolved {n}", flush=True)
    time.sleep(60)
