"""Test THE SMOOTHNESS PASS + NEW HERE REWRITE (user request, Sep 4:
"Update the section: 'New Here' what does the console do? (Should be a
current end to end explanation.) check each tab for choppy UI and make
it smooth. Simple Speak.").

Choppiness root causes fixed — every full rerun used to BLOCK on:
  - 2 PowerShell spawns (scanner_status in sidebar + NOW strip)
  - a SIGNED Kraken ledger fetch (fetch_payouts, every rerun)
  - a Neon round-trip (bot_positions in now_strip)
  - a ticker fetch per bot position (BOT'S BOOK)
  - an order-book fetch (QC Liquidity plate, while a result is shown)
  - OHLCV re-fetch per position (the 60s light cycle)
All now cached (30-180s TTLs). Plus: the light panel's clock ran the
RETIRED $20-pace math instead of the entry's own rotation-bounded
window; the watch settings stacked four full-width rows on every page;
the New Here tour still described the $20 game and the I RE-ENTERED
panel."""
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


app = Path("app.py").read_text(encoding="utf-8")

# ── 1. the hot-path caches (the freeze fixes; ADAPTIVE since Sep 5) ──
# The seven fetchers now route through adaptive_cache.py: an st.cache_data
# CORE with the min TTL (the burst shield) + the adaptive layer on top
# (base TTL, volatility-regime multiplier, event invalidation, cross-cache
# rules). The public names keep their signatures — every call site stands.
def _routes_adaptive(fn_name: str) -> bool:
    """True if the public fn calls ac_cached (the adaptive engine)."""
    body = app.split(f"def {fn_name}(")[1].split("\ndef ")[0]
    return "ac_cached(" in body


check("scanner_status adaptive (was 2 PowerShell spawns per rerun)",
      _routes_adaptive("scanner_status") and "def scanner_status_core" in app)
check("fetch_payouts adaptive (was a signed ledger fetch per rerun)",
      _routes_adaptive("fetch_payouts") and "def fetch_payouts_core" in app)
check("bot_positions adaptive (was a Neon query per rerun)",
      _routes_adaptive("bot_positions") and "def bot_positions_core" in app)
check("_ticker_px helper exists + adaptive",
      _routes_adaptive("_ticker_px") and "def _ticker_px_cached" in app)
check("BOT'S BOOK reads prices through the cached ticker",
      "_px = _ticker_px(_pair)" in app)
check("light cycle exit logic reads the cached ticker",
      "_t = _ticker_px(pair)" in app.split("def _run_exit_logic")[1].split("def _light_check")[0])
check("exit check reads the cached ticker",
      "_t = _ticker_px(pair)" in app.split("def render_exit_check")[1].split("# 1. trailing stop")[0])
check("fetch_df delegates to the cached candle fetcher",
      "_fetch_df_cached(pair, cfg.candle_interval, cfg.lookback_candles)" in app)
check("candle cache is TTL'd (not forever-stale; adaptive 45s base, 20-90)",
      _routes_adaptive("_fetch_df_cached") and "def _fetch_df_core" in app
      and "@st.cache_data(ttl=20)" in app.split("def _fetch_df_core")[0][-40:])
check("QC liquidity plate cached (_qc_liquidity)",
      _routes_adaptive("_qc_liquidity") and "snap = _qc_liquidity(pair, _notional)" in app)
check("TOURNAMENT past picks cached (_past_winners)",
      _routes_adaptive("_past_winners") and "winners = _past_winners()" in app)
check("a fresh champion busts the past-picks cache (both layers)",
      'ac_invalidate("past_winners")' in app and "_past_winners_core.clear()" in app)
check("the engine module is the ONE source (imported, not reimplemented)",
      "from adaptive_cache import ac_cached" in app)

# ── 2. the light panel clock: the entry's OWN window, not $20 pace ───
light_panel = app.split("def _render_light_panel")[1].split("def _render_entry_control")[0]
check("light clock reads the entry's window_min",
      'rec.get("window_min")' in light_panel)
check("light clock STAY CLOSE fires when the goal is nearly paid",
      "pnl >= _goal * 0.75" in light_panel)
check("retired $20 milestone counter gone from the panel",
      "_next20" not in light_panel)
check("legacy entries (no window) still get the old pace fallback",
      "_hours_to_scalp" in light_panel)

# ── 3. the watch settings row (was 4 stacked full-width rows) ────────
watch_block = app.split("def render_auto_watch")[1].split("def _time_to_goal")[0]
check("interval selectbox + volume dial in ONE row of columns",
      "_s1, _s2 = st.columns" in watch_block)
check("the status caption is FULL WIDTH (never inside a column)",
      watch_block.find('always on (critical to the system') <
      watch_block.find('with _s1:'))
check("the volume dial keeps its key (state survives)",
      'key="voice_volume"' in watch_block)
check("the retired toggles render nowhere in the watch block",
      'key="goal_toggle"' not in watch_block
      and 'key="voice_toggle"' not in watch_block
      and 'key="watch_toggle"' not in watch_block)
check("interval selectbox keeps its key (state survives)",
      'key="watch_interval_s"' in watch_block)
check("the settings row declares the three fixed-on features",
      "AUTO-WATCH" in watch_block and "GOAL CALL" in watch_block
      and "HER VOICE" in watch_block and "always on" in watch_block)
check("the volume dial says loudness-only (her voice is not a setting)",
      "Loudness only" in watch_block and "always on" in watch_block)

# ── 4. New Here: the current end-to-end, simple speak ────────────────
import re
tour_raw = app.split("New here? What does this console do?")[1].split("global exit alert banner")[0]
# the markdown string wraps across source lines — join the Python string
# literals before probing (what the browser actually renders)
tour = re.sub(r'"\s*\n\s*"', "", tour_raw)
for probe in ["two jobs", "rotations", "POPUP",
              "fire-time", "no scan, no popup", "sizes the",
              "rotation boundary", "death line",
              "GET OUT NOW", "SOLD", "DISCIPLINE", "TODAY'S FACTS",
              "TOURNAMENT", "QUICK CHECK", "EXIT CHECK", "never short"]:
    check(f"tour mentions: {probe}", probe in tour)
# (Sep 21: MY ENTRY retired from the tour with the form itself — popups
# auto-track; the SOLD tap is the only manual act left.)
check("tour: the retired rotation-floor wording is gone (the brain sizes, "
      "Sep 5)", "night hours are pickiest" not in tour)
check("tour: the daily lock is gone from the guards "
      "(removed with the lock, Sep 5)", "daily lock" not in tour)
check("tour: retired $20 counting gone", "$20s out loud" not in tour)
check("tour: retired I RE-ENTERED panel gone", "I RE-ENTERED panel" not in tour)
check("tour: popups are contest-only is stated", "contest only" in tour)
check("tour: the bot is separate money is stated", "separate money" in tour)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_uismooth_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
