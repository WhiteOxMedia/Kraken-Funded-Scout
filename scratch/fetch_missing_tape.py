"""BACKFILL THE TAPE COVERAGE GAP (Nov 15).

The entry-quality replay could position only 1,129 of 2,706 stored closes because
26 pairs in contest_trades.jsonl had no 5m bars in the aligned archive. This
fetches the missing symbols from the same source the archive was built from —
Kraken Futures charts spot 5m via contest_replay._fetch_futures_spot_pair — for
exactly the window the closes need (first entry - 2h .. last close + 1h), and
writes a separate archive the replay merges:

    scratch/kraken_5m_cover_2026-11-15.json

Per symbol it reports bars fetched, the span the venue actually served, and any
symbol the venue refused, so an unrecoverable residual is named instead of
silently missing. It never writes to the aligned archive, the live watch file or
any state the scanner reads.

Usage:
    .venv\\Scripts\\python.exe -X utf8 scratch/fetch_missing_tape.py [--max-pages N]
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import contest_replay as cr  # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
BASE_TAPE = ROOT / "scratch" / "kraken_5m_long_183d_aligned.json"
OUT = ROOT / "scratch" / "kraken_5m_cover_2026-11-15.json"

MAX_PAGES = 60
BUFFER_BEFORE_S = 2 * 3600      # enough for the 12-bar prior-hour window
BUFFER_AFTER_S = 3600


def main() -> int:
    have = set((json.load(open(BASE_TAPE, encoding="utf-8")).get("pairs") or {}))
    if OUT.exists():
        have |= set((json.load(open(OUT, encoding="utf-8")).get("pairs") or {}))

    need: dict[str, list[float]] = defaultdict(list)
    with open(TRADES, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                sym = str(r.get("pair") or "").split("/")[0]
                sold = datetime.fromisoformat(r["sold_at"]).timestamp()
                held = float(r.get("held_min") or 0)
            except Exception:
                continue
            if not sym or sym in have:
                continue
            need[sym].append(sold - held * 60.0)

    if not need:
        print("nothing missing: coverage already complete")
        return 0

    print(f"missing symbols {len(need)}; closes affected "
          f"{sum(len(v) for v in need.values())}", flush=True)
    doc = {"interval": cr.INTERVAL,
           "fetched_at": time.time(),
           "source": "kraken futures charts spot 5m (contest_replay fetcher), "
                     "fetched to close the aligned-archive coverage gap",
           "pairs": {}, "coverage": {}, "refused": {}}
    t0 = time.time()
    for sym in sorted(need):
        start = int(min(need[sym]) - BUFFER_BEFORE_S)
        end = int(max(need[sym]) + BUFFER_AFTER_S)
        t = time.time()
        bars = cr._fetch_futures_spot_pair(sym, start, end, max_pages=MAX_PAGES)
        if not bars:
            doc["refused"][sym] = {"requested": [start, end],
                                   "why": "venue returned no candles for PF_%sUSD"
                                          % sym}
            print(f"  {sym:<9} REFUSED  (requested "
                  f"{datetime.fromtimestamp(start):%m-%d %H:%M}.."
                  f"{datetime.fromtimestamp(end):%m-%d %H:%M})", flush=True)
            continue
        doc["pairs"][sym] = bars
        doc["coverage"][sym] = [bars[0][0], bars[-1][0]]
        got = (bars[-1][0] - bars[0][0]) / 86400.0
        print(f"  {sym:<9} {len(bars):>6} bars  "
              f"{datetime.fromtimestamp(bars[0][0]):%m-%d %H:%M}.."
              f"{datetime.fromtimestamp(bars[-1][0]):%m-%d %H:%M}  "
              f"({got:.0f}d)  {time.time() - t:.1f}s", flush=True)

    OUT.write_text(json.dumps(doc), encoding="utf-8")
    print(f"\nwrote {OUT.name}: {len(doc['pairs'])} symbols, "
          f"{sum(len(v) for v in doc['pairs'].values())} bars, "
          f"{OUT.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s", flush=True)
    if doc["refused"]:
        print("refused by the venue:", ", ".join(sorted(doc["refused"])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
