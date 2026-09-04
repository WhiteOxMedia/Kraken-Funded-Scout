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

# ── 1. the hot-path caches (the freeze fixes) ────────────────────────
def _has_cache(fn_name: str) -> bool:
    """True if the function def carries an @st.cache_data decorator."""
    import re
    m = re.search(r"(@st\.cache_data\(ttl=\d+\)\s*\n)?def " + fn_name + r"\(", app)
    return bool(m and m.group(1))


check("scanner_status cached (was 2 PowerShell spawns per rerun)",
      _has_cache("scanner_status"))
check("fetch_payouts cached (was a signed ledger fetch per rerun)",
      _has_cache("fetch_payouts"))
check("bot_positions cached (was a Neon query per rerun)",
      _has_cache("bot_positions"))
check("_ticker_px helper exists + cached",
      _has_cache("_ticker_px"))
check("BOT'S BOOK reads prices through the cached ticker",
      "_px = _ticker_px(_pair)" in app)
check("light cycle exit logic reads the cached ticker",
      "_t = _ticker_px(pair)" in app.split("def _run_exit_logic")[1].split("def _light_check")[0])
check("exit check reads the cached ticker",
      "_t = _ticker_px(pair)" in app.split("def render_exit_check")[1].split("# 1. trailing stop")[0])
check("fetch_df delegates to the cached candle fetcher",
      "_fetch_df_cached(pair, cfg.candle_interval, cfg.lookback_candles)" in app)
check("candle cache is TTL'd (not forever-stale)",
      "@st.cache_data(ttl=45)" in app.split("def _fetch_df_cached")[0].split("\n")[-2]
      or "@st.cache_data(ttl=45)\ndef _fetch_df_cached" in app)
check("QC liquidity plate cached (_qc_liquidity)",
      _has_cache("_qc_liquidity") and "snap = _qc_liquidity(pair, _notional)" in app)
check("TOURNAMENT past picks cached (_past_winners)",
      _has_cache("_past_winners") and "winners = _past_winners()" in app)
check("a fresh champion busts the past-picks cache",
      "_past_winners.clear()" in app)

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
check("interval + goal + voice toggles in ONE row of columns",
      "_s1, _s2, _s3 = st.columns" in watch_block)
check("goal toggle keeps its key (state survives)",
      'key="goal_toggle"' in watch_block)
check("voice toggle keeps its key (state survives)",
      'key="voice_toggle"' in watch_block)
check("interval selectbox keeps its key (state survives)",
      'key="watch_interval"' in watch_block)
check("GOAL CALL help speaks the current goal game (tide, rotation clock)",
      "scaled by the tide" in watch_block and "rotation boundary" in watch_block)

# ── 4. New Here: the current end-to-end, simple speak ────────────────
import re
tour_raw = app.split("New here? What does this console do?")[1].split("global exit alert banner")[0]
# the markdown string wraps across source lines — join the Python string
# literals before probing (what the browser actually renders)
tour = re.sub(r'"\s*\n\s*"', "", tour_raw)
for probe in ["two jobs", "rotations", "POPUP", "night hours are pickiest",
              "rotation boundary", "death line", "daily lock",
              "GET OUT NOW", "MY ENTRY", "DISCIPLINE", "TODAY'S FACTS",
              "TOURNAMENT", "QUICK CHECK", "EXIT CHECK", "never short"]:
    check(f"tour mentions: {probe}", probe in tour)
check("tour: retired $20 counting gone", "$20s out loud" not in tour)
check("tour: retired I RE-ENTERED panel gone", "I RE-ENTERED panel" not in tour)
check("tour: popups are contest-only is stated", "contest only" in tour)
check("tour: the bot is separate money is stated", "separate money" in tour)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_uismooth_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
