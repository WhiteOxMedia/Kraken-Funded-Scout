"""Test THE MOVER FUNNEL (user rule, Aug 31: "widen the gainer funnel for
the trading bot and leave the contest alone — we don't need both, they
share the same goal"). One funnel: today's runners (>=+5% on 24h, ALL
Kraken pairs) + yesterday's losers turning. BOT-ONLY bridge writes
(source="scout_mover"); the desktop popup carries the new-game gauntlet
(Sep 2: brain's number, goal clock, stale guard, 3% guard, book carry)."""
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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

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
check("funnel stamps the tide on writes (the funnel's own write + the send_alert stamp it shares)",
      'tide_state=(_tsw or "UNKNOWN")' in src
      and 'tide_state=_tide_stamp' in src)
check("the lockout is retired — the gate call stays for the re-entry display",
      "rotation_gate(asset)" in src and "RETIRED" in src
      and "MOVER SKIP: %s — %s" not in src)
check("funnel has the deep-read >=60 veto", "MOVER VETO" in src and "_deep_sig.confidence < 60" in src)
check("funnel skips tide OUT", "tide OUT (fleet %.0f%%)" in src)
check("funnel has the fresh-listing guard", "len(ohlcv) < MOVER_MIN_CANDLES" in src)
check("funnel paces itself (10-min rescan, contest path untouched)",
      "_mover_scanned.get(pair, 0)) < 600" in src)
check("desktop uses the FIRE-TIME rule (Sep 5 ADVISORY BRAIN: the scan must "
      "run and agree on direction; the 89 number-bar retired — the number sizes)",
      "_deep_sig is not None" in src
      and "_deep_sig.action == \"buy\"" in src
      and "float(_deep_sig.confidence) >= 89" not in src)
check("mover popup fails CLOSED on a broken brain read (the JUP lesson)",
      "_m_desk_ok = False  # fail-closed" in src)
check("mover popup has the worth floor (half the daily pace, same as champion)",
      "MOVER WORTH FLOOR" in src and "WORTH_FLOOR_PACE_FRAC" in src)
check("funnel evaluates at its own floor, not the tournament bar",
      "evaluate_and_check(pair, df, MOVER_MIN_CONF)" in src)

# ── 4b. THE NEW-GAME MOVER POPUP (user option 1, Sep 2: "convert the
#        MOVER popup to the new-game format — same gauntlet as champion
#        popups"; the SYRUP 10:27 popup fired the old format quoting the
#        quick 72% while the brain's number cleared the bar) ──────────
check("mover popup title speaks the BRAIN'S number, leads with BUY",
      "MOVER BUY {asset}" in src and "{_m_dc:.0f}% SURE" in src,
      "old quick-number title gone" if "MOVER {asset} ({signal.confidence:.0f}%)" not in src else "OLD TITLE STILL PRESENT")
check("the quick number no longer headlines a mover popup (log only)",
      "MOVER {asset} ({signal.confidence:.0f}%)" not in src)
check("mover popup voice speaks the brain's number",
      "Buy {asset}! {_m_dc:.0f} percent sure!" in src)
check("mover popup carries the goal clock (the asset's own week + rotation-bounded window)",
      "_m_goal = _market_goal_pct(exchange, pair)" in src
      and "_m_win = _rotation_window_min()" in src
      and "Aim for plus {_m_goal:.0f} percent! " in src)
check("mover popup carries the stay-close discipline (the clock never exits)",
      "Stay close after {_m_out_by} — I'll call the exit!" in src
      and "If it hasn't paid by" not in src)
check("mover popup has the stale-move guard (the APT wiggle trap)",
      "MOVER STALE MOVE" in src and src.count("_stale_move(exchange, pair)") >= 2,
      src.count("_stale_move(exchange, pair)"))
check("mover popup has the 3% guard sizing (platform-only, Sep 5 — the daily lock removed)",
      "MOVER DEATH LINE" in src and src.count("max_entry_usd(") >= 2
      and "state=_m_acct" not in src,
      src.count("max_entry_usd("))
check("mover popup has the book-carry verdict (her four verdicts)",
      "measure_book(exchange, pair)" in src
      and "Small size only — {_m_spend:,.0f} dollars! " in src
      and "Book unreadable — your call on size! " in src)
check("mover popup suppresses on TOO THIN (no popup at all)",
      "MOVER TOO THIN" in src)
check("mover popup sizes off the LIVE contest account",
      "_ca.load_state()" in src and "import contest_account as _ca" in src)

# ── 5. the bot reader accepts the new source ───────────────────────────
BOT_READER = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"
rsrc = open(BOT_READER, encoding="utf-8").read()
check("reader accepts scout_mover buys", '"scout_mover"' in rsrc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_moverfunnel_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
