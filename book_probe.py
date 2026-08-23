"""Live book probe: real top-10 depth + health + tiers for $100/$400/$1400 buys
across majors → thin challenge assets. Read-only (public API)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt
import execution_tiers as et

PAIRS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD",          # majors
    "ADA/USD", "LINK/USD",                                 # large alts
    "EIGEN/USD", "PENGU/USD", "TURBO/USD", "MOODENG/USD", # challenge thin alts
]
SIZES = [100.0, 400.0, 1400.0]

ex = ccxt.kraken({"enableRateLimit": True})
lines = ["LIVE BOOK PROBE — top-10 depth, health, buy tiers", "=" * 100]
for pair in PAIRS:
    try:
        book = et.measure_book(ex, pair)
    except Exception as exc:
        lines.append(f"{pair:<12} BOOK ERROR: {exc}")
        continue
    if not book:
        lines.append(f"{pair:<12} EMPTY BOOK")
        continue
    score, bd = et.liquidity_health_score(book, pair)
    tiers = []
    for usd in SIZES:
        num, name, cons = et.classify_tier(usd, book["ask_tob"])
        slip, _vw = et.forecast_slippage(book["asks"], usd, book["mid"], "buy")
        slip_s = f"{slip:.3f}%" if slip is not None else "—"
        tiers.append(f"${usd:>5}: {name:<15} {cons:>5.1f}% slip {slip_s}")
    lines.append(
        f"{pair:<12} book ${book['ask_tob']:>10,.0f} ask / ${book['bid_tob']:>10,.0f} bid · "
        f"spread {book['spread_bps']:>6.1f}bps · health {score}/100"
    )
    lines.append(f"{'':<12} " + " │ ".join(tiers))

out = Path(__file__).resolve().parent / "book_probe_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("probe done →", out.name)
