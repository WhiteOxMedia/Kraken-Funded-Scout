"""Test THE ADAPTIVE TTL SYSTEM (user spec, Sep 5: "Adaptive TTL System
(Full Specification)").

Every fetcher: base TTL + min + max + a volatility-regime multiplier
(calm 1.5x / normal 1.0x / volatile 0.5x / extreme 0.25x; archival
fetchers ignore it), event-driven invalidation (scan / trade / pair
switch), cross-cache rules (price -> bot_positions, depth ->
scanner_status, positions -> book), and the staleness-aware UI
(green <10s / yellow 10-30s / orange past ttl / red forced).
"""
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


import adaptive_cache as ac


def reset():
    ac.ac_reset()


# ── 1. THE SPEC TABLE (the user's adaptive TTL table, verbatim) ───────
spec_want = {
    "ticker_px":     (30, 8, 45, True),
    "fetch_df":      (45, 20, 90, True),
    "qc_liquidity":  (60, 20, 120, True),
    "past_winners":  (60, 60, 300, False),
    "scanner_status": (60, 20, 120, True),
    "fetch_payouts": (180, 180, 600, False),
    "bot_positions": (30, 10, 45, True),
}
for k, want in spec_want.items():
    got = ac.SPEC[k]
    check(f"spec {k}: base={want[0]} min={want[1]} max={want[2]} adaptive={want[3]}",
          got == want, got)
check("exactly the seven spec'd fetchers", set(ac.SPEC) == set(spec_want),
      sorted(ac.SPEC))

# ── 2. THE VOLATILITY MODEL ───────────────────────────────────────────
reset()
ac.sample_price("BTC/USD", 100.0)
ac.sample_price("BTC/USD", 100.1)
check("too-thin data (<3 samples) holds the regime (no bootstrap flip)",
      ac.regime()["name"] == "normal", ac.regime())
reset()
for px in [100.0, 100.1, 100.15, 100.2]:
    ac.sample_price("BTC/USD", px)
check("4 real samples of a 0.2% market = calm (evidence, not bootstrap)",
      ac.regime()["name"] == "calm", ac.regime())
check("a calm market (0.2% over 60s) reads calm",
      abs(ac.vol_pct() - 0.2) < 0.05, ac.vol_pct())
reset()
for px in [100.0, 101.2, 101.5, 101.9]:
    ac.sample_price("BTC/USD", px)
check("a fast market (1.9%) reads volatile", ac.regime()["name"] == "volatile",
      ac.regime())
reset()
for px in [100.0, 103.5, 104.0, 104.1]:
    ac.sample_price("BTC/USD", px)
check("a runaway market (>3%) reads extreme", ac.regime()["name"] == "extreme",
      ac.regime())
reset()
ac.sample_price("BTC/USD", 100.0)
ac.sample_price("BTC/USD", 101.0)
ac.sample_price("ETH/USD", 50.0)
ac.sample_price("ETH/USD", 50.25)
check("vol is the MEAN across pairs (fleet-wide read)",
      abs(ac.vol_pct() - 0.75) < 0.05, ac.vol_pct())
reset()
ac.sample_price("BTC/USD", 0.0)
ac.sample_price("BTC/USD", None)
check("failed reads (0/None px) never sample", ac.vol_pct() == 0.0)
check("regime boundaries: 0.3/1.0/3.0 by the table",
      [(0.29, "calm"), (0.31, "normal"), (0.99, "normal"),
       (1.01, "volatile"), (2.99, "volatile"), (3.01, "extreme")] ==
      [(v, [n for a, m, n in ac.REGIMES if a is None or v < a][0])
       for v, _ in [(0.29, 0), (0.31, 0), (0.99, 0), (1.01, 0),
                    (2.99, 0), (3.01, 0)]])

# ── 3. THE ADAPTIVE TTL ───────────────────────────────────────────────
reset()
check("normal regime: base TTLs", ac.ttl_for("ticker_px") == 30
      and ac.ttl_for("fetch_df") == 45 and ac.ttl_for("bot_positions") == 30)
reset()
for px in [100.0, 100.05, 100.1, 100.15]:   # 0.15% -> calm
    ac.sample_price("BTC/USD", px)
check("calm loosens to the max (ticker 30x1.5=45)",
      ac.ttl_for("ticker_px") == 45, ac.ttl_for("ticker_px"))
check("calm: df 45x1.5=67.5 -> 67, within (20,90)",
      20 <= ac.ttl_for("fetch_df") <= 90, ac.ttl_for("fetch_df"))
check("calm: archival payouts IGNORE the multiplier (180)",
      ac.ttl_for("fetch_payouts") == 180, ac.ttl_for("fetch_payouts"))
check("calm: past winners IGNORE the multiplier (60)",
      ac.ttl_for("past_winners") == 60, ac.ttl_for("past_winners"))
reset()
for px in [100.0, 100.5, 100.9, 101.2]:   # 1.2% -> volatile
    ac.sample_price("BTC/USD", px)
check("volatile halves (ticker 15, positions 15)",
      ac.ttl_for("ticker_px") == 15 and ac.ttl_for("bot_positions") == 15)
reset()
for px in [100.0, 103.6, 104.0, 104.2]:   # >3% -> extreme
    ac.sample_price("BTC/USD", px)
check("extreme tightens to the min (ticker 30x0.25=7.5 -> clamped 8)",
      ac.ttl_for("ticker_px") == 8, ac.ttl_for("ticker_px"))
check("extreme: positions 30x0.25=7.5 -> clamped 10",
      ac.ttl_for("bot_positions") == 10, ac.ttl_for("bot_positions"))
check("extreme: archival still 180", ac.ttl_for("fetch_payouts") == 180)

# ── 4. THE CACHE ITSELF ───────────────────────────────────────────────
reset()
calls = {"n": 0}


def fetch_px():
    calls["n"] += 1
    return 100.0


ac.ac_cached("ticker_px", "BTC/USD", fetch_px)
ac.ac_cached("ticker_px", "BTC/USD", fetch_px)
check("fresh within TTL: one fetch, two reads", calls["n"] == 1, calls)
# age the store past the ttl (normal ticker = 30s)
for k in ac._STORE:
    ac._STORE[k]["ts"] -= 31
ac.ac_cached("ticker_px", "BTC/USD", fetch_px)
check("past the TTL: refetch", calls["n"] == 2, calls)
ac.ac_invalidate("ticker_px")
ac.ac_cached("ticker_px", "BTC/USD", fetch_px)
check("event invalidation forces the next read fresh", calls["n"] == 3, calls)

reset()
val = ac.ac_cached("fetch_df", "X|1|2", lambda: {"frame": 1}, copy_result=True)
val["frame"] = 999
val2 = ac.ac_cached("fetch_df", "X|1|2", lambda: {"frame": 1}, copy_result=True)
check("copy_result: callers can never poison the store",
      val2["frame"] == 1, val2)

# ── 5. CROSS-CACHE INVALIDATION ───────────────────────────────────────
reset()
pos_calls = {"n": 0}
book_calls = {"n": 0}
scan_calls = {"n": 0}
ac.ac_cached("bot_positions", "open", lambda: (pos_calls.__setitem__(
    "n", pos_calls["n"] + 1), [])[1])
ac.ac_cached("bot_positions", "open", lambda: (pos_calls.__setitem__(
    "n", pos_calls["n"] + 1), [])[1])
check("positions serve from cache (no refetch)", pos_calls["n"] == 1, pos_calls)
# price moves 1.0% (> the 0.8% rule) -> bot_positions busts
ac.ac_cached("ticker_px", "BTC/USD", lambda: 100.0)
ac.ac_cached("ticker_px", "BTC/USD", lambda: 101.0)  # age past ttl first
for k in list(ac._STORE):
    if k[0] == "ticker_px":
        ac._STORE[k]["ts"] -= 31
ac.ac_cached("ticker_px", "BTC/USD", lambda: 101.0)
ac.ac_cached("bot_positions", "open", lambda: (pos_calls.__setitem__(
    "n", pos_calls["n"] + 1), [])[1])
check("a >0.8% price move refreshes bot_positions",
      pos_calls["n"] == 2, pos_calls)
# a small move does NOT bust positions
for k in list(ac._STORE):
    if k[0] == "ticker_px":
        ac._STORE[k]["ts"] -= 31
ac.ac_cached("ticker_px", "BTC/USD", lambda: 101.3)   # +0.3%
ac.ac_cached("bot_positions", "open", lambda: (pos_calls.__setitem__(
    "n", pos_calls["n"] + 1), [])[1])
check("a <0.8% move leaves bot_positions cached",
      pos_calls["n"] == 2, pos_calls)

reset()
ac.ac_cached("qc_liquidity", "X/USD", lambda: {"ask_tob": 1000.0})
for k in list(ac._STORE):
    ac._STORE[k]["ts"] -= 121
ac.ac_cached("qc_liquidity", "X/USD", lambda: {"ask_tob": 1200.0})  # +20%
check("a >10% depth change refreshes scanner_status (the flag is set)",
      ("scanner_status",) in ac._INVALID)
reset()
ac.ac_cached("qc_liquidity", "X/USD", lambda: {"ask_tob": 1000.0})
for k in list(ac._STORE):
    ac._STORE[k]["ts"] -= 121
ac.ac_cached("qc_liquidity", "X/USD", lambda: {"ask_tob": 1050.0})  # +5%
check("a <10% depth change leaves scanner_status alone",
      ("scanner_status",) not in ac._INVALID)

reset()
ac.ac_cached("bot_positions", "open", lambda: [{"id": 1, "pair": "BTC"}])
for k in list(ac._STORE):
    ac._STORE[k]["ts"] -= 46
ac.ac_cached("bot_positions", "open", lambda: [])  # position exited
check("a position enter/exit refreshes qc_liquidity",
      ("qc_liquidity",) in ac._INVALID)

# ── 6. REGIME CHANGE = one forced refresh ─────────────────────────────
reset()
for px in [100.0, 100.05, 100.1, 100.14]:   # calm
    ac.sample_price("BTC/USD", px)
for px in [100.2, 103.0]:                    # the move to extreme
    ac.sample_price("BTC/USD", px)
check("a regime change forces ticker_px + fetch_df fresh",
      ("ticker_px",) in ac._INVALID and ("fetch_df",) in ac._INVALID)

# ── 7. STALENESS-AWARE UI ─────────────────────────────────────────────
reset()
ac.ac_cached("ticker_px", "BTC/USD", lambda: 100.0)
fr = {f["name"]: f for f in ac.freshness()}
check("a 0s-old read is green", fr["ticker_px"]["color"] == "green")
for k in ac._STORE:
    ac._STORE[k]["ts"] -= 15
fr = {f["name"]: f for f in ac.freshness()}
check("15s old is yellow", fr["ticker_px"]["color"] == "yellow")
for k in ac._STORE:
    ac._STORE[k]["ts"] -= 120
fr = {f["name"]: f for f in ac.freshness()}
check("past-ttl is orange", fr["ticker_px"]["color"] == "orange")
ac.ac_invalidate("ticker_px")
fr = {f["name"]: f for f in ac.freshness()}
check("a forced refresh pending is red", fr["ticker_px"]["color"] == "red")
check("never-fetched fetchers render 'off'",
      fr["fetch_payouts"]["color"] == "off")
html = ac.freshness_html()
check("the plate carries the regime + labels + tooltips",
      "NORMAL" in html and "PX" in html and "Last updated" in html
      and "next refresh in" in html)

# ── 8. the console wiring (app.py routes through the engine) ─────────
app = open("app.py", encoding="utf-8").read()
check("app imports the engine", "from adaptive_cache import ac_cached" in app)
wrappers = {
    "_ticker_px": '_ticker_px_cached(pair)',
    "scanner_status": "scanner_status_core",
    "bot_positions": "bot_positions_core",
    "fetch_payouts": "fetch_payouts_core",
    "_fetch_df_cached": "_fetch_df_core",
    "_qc_liquidity": "_qc_liquidity_core",
    "_past_winners": "_past_winners_core",
}
for fn, core in wrappers.items():
    check(f"{fn} routes through the adaptive engine",
          f'ac_cached(' in app.split(f"def {fn}(")[1].split("\ndef ")[0]
          and core in app, fn)
check("the cores carry the st burst shields (ttl=min)",
      all(f'@st.cache_data(ttl={mn})' in app
          for mn in (8, 10, 20, 180, 60)), "ttl=8/10/20/180/60")
check("the double-decorator warts are gone (bot_positions, fetch_payouts)",
      "@st.cache_data(ttl=60)\n@st.cache_data(ttl=30)" not in app
      and "@st.cache_data(ttl=300)\n@st.cache_data(ttl=180)" not in app)
check("the candle frames serve COPIES (the poisoning guard)",
      "copy_result=True" in app.split("def _fetch_df_cached")[1].split("def fetch_df")[0])
check("EVENT: the QC scan busts candles + book",
      'ac_invalidate("fetch_df", "qc_liquidity")' in app)
check("EVENT: switching pairs busts candles + book",
      '_qc_last_asset' in app)
check("EVENT: a trade (SOLD / green-light add / MY ENTRY) busts bot + book",
      app.count('ac_invalidate("bot_positions", "qc_liquidity")') >= 1
      and app.count('ac_invalidate("qc_liquidity", "bot_positions")') >= 2)
check("the freshness plate renders in the sidebar",
      'plate("Data Freshness"' in app and "freshness_html()" in app)
check("a new champion busts the adaptive winners cache too",
      'ac_invalidate("past_winners")' in app)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_adaptivecache_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
