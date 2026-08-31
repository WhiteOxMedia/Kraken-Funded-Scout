"""Test THE MOVER FUNNEL (user rule, Aug 31: "widen the gainer funnel for
the trading bot and leave the contest alone — we don't need both, they
share the same goal"). One funnel: today's runners (>=+5% on 24h, ALL
Kraken pairs) + yesterday's losers turning. BOT-ONLY bridge writes
(source="scout_mover"); the contest path keeps its 89 desktop floor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs

# ── 1. constants: the funnel's own calibration ─────────────────────────
check("MOVER_MIN_CONF is 55 (bot-grade, not the tournament bar)", bs.MOVER_MIN_CONF == 55.0, bs.MOVER_MIN_CONF)
check("MOVER_MIN_PCT is 5 (a runner is +5%+ on 24h)", bs.MOVER_MIN_PCT == 5.0, bs.MOVER_MIN_PCT)
check("MOVER_MIN_CANDLES is 100 (~4 days — fresh listings excluded)", bs.MOVER_MIN_CANDLES == 100, bs.MOVER_MIN_CANDLES)
check("MOVER_TOP_N is 10", bs.MOVER_TOP_N == 10, bs.MOVER_TOP_N)

# ── 2. fetch_kraken_movers: the widened net ────────────────────────────
TICKERS = {
    "XMR/USD":  {"percentage": 11.2, "quoteVolume": 2_000_000, "last": 1.0},   # the case that died silent
    "ZORA/USD": {"percentage": 20.6, "quoteVolume": 500_000, "last": 0.05},
    "WEN/USD":  {"percentage": 81.7, "quoteVolume": 900_000, "last": 0.1},
    "NEAR4/USD": {"percentage": 4.9, "quoteVolume": 900_000, "last": 3.0},      # just under the bar
    "DOGE/USD": {"percentage": -7.0, "quoteVolume": 900_000, "last": 0.2},      # negative: not a runner
    "EUR/USD":  {"percentage": 0.6, "quoteVolume": 9_000_000, "last": 1.1},     # fiat: never
    "DUST/USD": {"percentage": 40.0, "quoteVolume": 50_000, "last": 0.001},     # too thin
    "NULL/USD": {"quoteVolume": 900_000, "last": 1.0},                          # no pct field
    "ZERO/USD": {"percentage": 12.0, "quoteVolume": 900_000, "last": 0},        # bad price
    "BTC/EUR":  {"percentage": 30.0, "quoteVolume": 900_000, "last": 60000},    # wrong quote
}
movers = bs.fetch_kraken_movers(None, tickers=TICKERS)
pairs = [m["pair"] for m in movers]
check("XMR (+11.2%) is caught", "XMR/USD" in pairs, pairs)
check("ZORA (+20.6%) is caught", "ZORA/USD" in pairs)
check("WEN (+81.7%) is caught", "WEN/USD" in pairs)
check("sorted by pct desc (WEN first)", movers[0]["pair"] == "WEN/USD", movers[0]["pair"])
check("+4.9% excluded (under the runner bar)", "NEAR4/USD" not in pairs)
check("negative move excluded", "DOGE/USD" not in pairs)
check("fiat excluded (EUR/USD never again)", "EUR/USD" not in pairs)
check("thin book excluded (<$100K volume)", "DUST/USD" not in pairs)
check("missing pct excluded", "NULL/USD" not in pairs)
check("bad price excluded", "ZERO/USD" not in pairs)
check("non-USD quote excluded", "BTC/EUR" not in pairs)

# top_n caps the scan load
capped = bs.fetch_kraken_movers(None, tickers=TICKERS, top_n=1)
check("top_n caps the list", len(capped) == 1 and capped[0]["pair"] == "WEN/USD", capped)


class BrokenExchange:
    def fetch_tickers(self):
        raise RuntimeError("api down")


check("broken ticker fetch -> empty list, no crash",
      bs.fetch_kraken_movers(BrokenExchange()) == [])

# ── 3. THE XMR MATH: the funnel floor takes what the bar killed ───────
check("XMR's 56% clears the funnel floor (the ~80 bar killed it)",
      56 >= bs.MOVER_MIN_CONF)
check("a 54% runner is still rejected (quality floor holds)",
      54 < bs.MOVER_MIN_CONF)

# ── 4. source checks: the funnel is wired right ────────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("funnel writes source='scout_mover'", 'source="scout_mover"' in src)
check("old dead gainer path removed", "def fetch_kraken_gainers" not in src)
check("old bounce fast path removed", "Bounce fast path" not in src)
check("bounce list folded into the funnel (losers join the runners)",
      'cached_loser_pairs' in src and "_mover_cands.append((_bp, -5.0))" in src)
check("funnel stamps the tide on writes",
      src.count('tide_state=(_tsw or "UNKNOWN")') >= 2, src.count('tide_state=(_tsw or "UNKNOWN")'))
check("funnel has the rotation lockout", "MOVER SKIP" in src and "rotation_gate(asset)" in src)
check("funnel has the deep-read >=60 veto", "MOVER VETO" in src and "_deep_sig.confidence < 60" in src)
check("funnel skips tide OUT", "tide OUT (fleet %.0f%%)" in src)
check("funnel has the fresh-listing guard", "len(ohlcv) < MOVER_MIN_CANDLES" in src)
check("funnel paces itself (10-min rescan, contest path untouched)",
      "_mover_scanned.get(pair, 0)) < 600" in src)
check("desktop keeps the 89 floor (contest untouched)",
      'signal.confidence >= 89 and not (_b_hours is not None' in src)
check("funnel evaluates at its own floor, not the tournament bar",
      "evaluate_and_check(pair, df, MOVER_MIN_CONF)" in src)

# ── 5. the bot reader accepts the new source ───────────────────────────
BOT_READER = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"
rsrc = open(BOT_READER, encoding="utf-8").read()
check("reader accepts scout_mover buys", '"scout_mover"' in rsrc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_moverfunnel_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
