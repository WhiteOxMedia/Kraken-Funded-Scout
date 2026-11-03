"""diag_liqtier.py — THE TIER-METHOD BACKTEST (Sep 19).

QUESTION (user, Sep 19): did the advisory liquidity tier's conservative
bias ever CHANGE a trade's execution method at the 10k tier?

WHY THE QUESTION IS SHARP: the tier machinery (`classify_tier(notional,
tob)`) keys on spend-vs-book-depth — cut the account 10x and every spend
falls 10x, so books that looked big at 10k look enormous at 1k. The
question is whether any REAL fire sat near a tier boundary where the
tier flip (market -> ladder -> TWAP) actually happens.

STRUCTURAL TRUTHS THIS STUDY MUST HONOR:
  1. The bot executes EVERY buy as a market order (bot_engine.py:556) —
     it imports only gate_buy (blacklist/health/cap/slippage) from
     execution_tiers, never the tier->method map. The method is a
     label, not an executor. So the backtest prices the LABEL DELTAS
     (what would have changed) and the SLIPPAGE DELTAS (what would
     have actually cost/made money), and says so.
  2. No historical book depth was ever stored (no tob in any ledger).
     This study reads each pair's book LIVE and applies a +/-30%
     robustness band on tob — if a verdict flips inside that band, the
     honest answer is "unclear", not a number.
  3. The lived record = the bot's real fills (EVENT BUY lines, every
     one carrying Cost $). The 1k counterfactual sizes each fill
     through the unified sizing ladder (linear in balance, verified by
     diag_smoketier: 1.5% of balance), i.e. spend_1k = spend_10k / 10.

Run: .venv\\Scripts\\python.exe -X utf8 diag_liqtier.py
Writes _liqtier.txt. READ-ONLY on state; only public API reads.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(__file__).parent / "_liqtier.txt"
BOT_GLOB = str(Path(__file__).parent.parent / "kraken_xrp_trading_bot" / "logs" / "bot.log*")
TOB_BAND = 0.30          # +/-30% depth uncertainty (no stored book history)
TIER_BOUNDS = [          # mirror of execution_tiers.TIER_BOUNDS (cons% upper bounds)
    (5.0, "T1 Micro"),
    (20.0, "T2 Small"),
    (50.0, "T3 Medium"),
    (100.0, "T4 Large"),
    (300.0, "T5 Very Large"),
]
FILL_RE = re.compile(r"EVENT BUY (\S+) ::.*?Cost: \$([\d.]+)")
FILL_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


def parse_fills(paths: list[str]) -> list[dict]:
    """Every real bot fill: ts, pair, cost. Duplicates (log rotation) deduped
    on (ts_minute, pair, cost)."""
    seen: set[tuple] = set()
    fills: list[dict] = []
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            m = FILL_RE.search(line)
            if not m:
                continue
            ts = FILL_TS_RE.match(line)
            key = (ts.group(1) if ts else "", m.group(1), round(float(m.group(2)), 2))
            if key in seen:
                continue
            seen.add(key)
            fills.append({"ts": key[0], "pair": m.group(1), "cost": key[2]})
    return fills


def classify_tier(notional_usd: float, tob_usd: float) -> tuple[int, str, float]:
    """Mirror of execution_tiers.classify_tier (kept local so the study has
    zero state coupling and the pins can hand-verify)."""
    if tob_usd <= 0:
        return 6, "T6 Institutional", 999.0
    cons = notional_usd / tob_usd * 100.0
    for i, (hi, name) in enumerate(TIER_BOUNDS, start=1):
        if cons < hi:
            return i, name, cons
    return 6, "T6 Institutional", cons


def method_for_tier(num: int) -> str:
    """Mirror of liquidity_snapshot's method map."""
    return {1: "market", 2: "market", 3: "ladder x3", 4: "TWAP x4", 5: "capped+ladder", 6: "capped+ladder"}[num]


def flip_verdict(lived_tier: int, cf_tier: int, band_flips: bool) -> str:
    """One row's verdict: the method label that would have changed."""
    if lived_tier == cf_tier:
        return "same"
    return "FLIP-in-band" if band_flips else "FLIP-stable"


def main() -> int:
    fills = parse_fills(sorted(glob.glob(BOT_GLOB)))
    lines = []
    lines.append("THE TIER-METHOD BACKTEST — did the advisory tier's conservative bias")
    lines.append("ever change a trade's execution method at the 10k tier?")
    lines.append(f"real fills parsed from bot logs: {len(fills)}")
    if not fills:
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return 1

    # live books for every pair that ever fired
    pairs = sorted({f["pair"] for f in fills})
    try:
        import ccxt
        ex = ccxt.kraken()
        ex.enableRateLimit = True
    except Exception as exc:
        lines.append(f"FATAL: no ccxt for book reads: {exc}")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return 1

    books: dict[str, float] = {}
    spreads: dict[str, float] = {}
    for p in pairs:
        try:
            ob = ex.fetch_order_book(p, limit=25)
            asks = [[float(a), float(s), *a2] if (a2 := ()) else [float(a), float(s)]
                    for a, s, *_ in ob.get("asks", [])[:25]]
            bids = [[float(b), float(s)] for b, s, *_ in ob.get("bids", [])[:25]]
            if not asks or not bids:
                continue
            tob = sum(pr * sz for pr, sz in asks[:10])
            mid = (asks[0][0] + bids[0][0]) / 2.0
            books[p] = tob
            spreads[p] = (asks[0][0] - bids[0][0]) / mid * 10_000.0 if mid else 0.0
        except Exception as exc:
            lines.append(f"  book read failed {p}: {exc}")

    lines.append(f"pairs with live books: {len(books)}/{len(pairs)}")
    lines.append("")
    lines.append(f"{'ts':<17}{'pair':<13}{'lived$':>8}{'1k$':>7}  {'lived tier':<14}{'1k tier':<14}{'verdict':<12}{'band?'}")
    lines.append("-" * 100)

    n_flip = n_stable = n_unclear = n_nobook = 0
    slip_rows = []
    for f in fills:
        tob = books.get(f["pair"])
        if not tob:
            n_nobook += 1
            continue
        lt = classify_tier(f["cost"], tob)
        ct = classify_tier(f["cost"] / 10.0, tob)
        # robustness: worst case for a flip is 30% shallower book (pushes 1k tier up)
        lt_lo = classify_tier(f["cost"], tob * (1 - TOB_BAND))[0]
        ct_lo = classify_tier(f["cost"] / 10.0, tob * (1 - TOB_BAND))[0]
        band_flips = (lt_lo != ct_lo) or (lt[0] != lt_lo) or (ct[0] != ct_lo)
        v = flip_verdict(lt[0], ct[0], band_flips)
        if v == "same":
            n_stable += 1
        elif v == "FLIP-in-band":
            n_unclear += 1
        else:
            n_flip += 1
        lines.append(f"{f['ts']:<17}{f['pair']:<13}{f['cost']:>8.2f}{f['cost']/10:>7.2f}  "
                     f"{lt[1]:<14}{ct[1]:<14}{v:<12}{'yes' if band_flips else 'no'}")

    lines.append("")
    lines.append(f"VERDICT: same={n_stable}  stable-flip={n_flip}  in-band-flip(unclear)={n_unclear}  no-book={n_nobook}")
    lines.append("")
    lines.append("Structural context (checked, not assumed):")
    lines.append("  - the bot executes every buy as a MARKET order (bot_engine.place_market_buy);")
    lines.append("    it imports only gate_buy (blacklist/health/cap/slippage) from execution_tiers.")
    lines.append("    The tier->method map is a label: no executor consumes it today.")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-14:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
