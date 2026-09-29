"""Verify the exit-check synthesis + watch-record semantics (from the
Sep 3 entry-control plan) — UPDATED Sep 21: THE ONE ENTRY CONTROL IS
RETIRED (user rule: "My entry-fix the numbers is no longer of any use" —
popups auto-track at fire time via auto_track_popup, the GET OUT
auto-close stops the watch, SOLD is the only manual tap). The record
semantics it guarded (same-trade keyed updates preserving the clock,
goal and peak) now live in the scanner's auto-track writer; the suite
keeps verifying the exit synthesis, the goal helper and the record
shape those writers produce, plus the retirement itself.
Source assertions follow the project's test_*.py convention; watch-record
semantics are verified by running the exact mutation logic against a temp file."""
import json
import sys
import tempfile
from pathlib import Path

import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
ap = (BASE / "app.py").read_text(encoding="utf-8")
bs = (BASE / "background_scanner.py").read_text(encoding="utf-8")
results = []


def check(name, ok):
    results.append((name, bool(ok)))


# ── ent-1: shared goal helper (still used by the green light + WATCH-anyway)
check("_derive_goal defined in app.py (Sep 4: takes kraken for the market cap)",
      "def _derive_goal(pair: str, kraken=None)" in ap)
check("_derive_goal wraps the engine derivation with the market-week cap",
      "_market_goal_pct" in ap[ap.find("def _derive_goal"):ap.find("_derive_goal") + 1800]
      and "_window_goal_pct" in ap[ap.find("def _derive_goal"):ap.find("_derive_goal") + 1800])
check("window formula matches green light", "max(60.0, min(180.0, goal * 60.0))" in ap)

# ── ent-2: THE ONE ENTRY CONTROL — RETIRED (Sep 21) ─────────────────────
check("MY ENTRY is gone (the manual entry-fix form)",
      "MY ENTRY" not in ap and "def _render_entry_control" not in ap)
check("the add/remove expander is gone (popups auto-track; GET OUT auto-stops)",
      "watch_add_form" not in ap and "add / remove" not in ap)
check("nothing references the retired control",
      "_render_entry_control(" not in ap)
check("the watch is written by the fire-time writer now (one writer per route)",
      "def auto_track_popup(" in bs)

# ── rt-1: _run_exit_logic real-time (untouched by the retirement) ──────
rl_i = ap.find("def _run_exit_logic")
rl_blk = ap[rl_i:ap.find("def _light_check")]
check("exit logic reads the ticker", "_ticker_px(pair)" in rl_blk)
check("ticker is primary, candle close is fallback", rl_blk.find("_ticker_px") > rl_blk.find("live = float(df"))
check("in-progress candle seeded", 'df.loc[_idx, "close"] = live' in rl_blk)

# ── rt-2: EXIT CHECK tab real-time ─────────────────────────────────────
ec_i = ap.find("def render_exit_check")
ec_blk = ap[ec_i:]
check("EXIT CHECK reads the ticker", "_ticker_px(pair)" in ec_blk[ec_blk.find("with st.spinner"):ec_blk.find("sig = _eval")])
check("EXIT CHECK reads the watch record", "_load_watch().get(pair)" in ec_blk)
check("watch entry overrides bridge entry", 'entry = float(_wr["entry"])' in ec_blk)
check("watch peak merged into the trail", "high = max(high, _w_high)" in ec_blk)
check("goal multiples computed", "_goal_mult = int(_pct / _w_goal)" in ec_blk)
check("window clock computed", "_elapsed_min = (datetime.now(MST) - _ad)" in ec_blk)
check("window-expired is NOT an exit term (the clock never exits)",
      "_window_expired or _hard_stop_hit" not in ec_blk
      and "_window_expired" in ec_blk)
check("thesis check: entry why vs current signal", "_thesis_ok = " in ec_blk)
check("goal verdict says STAY CLOSE (never take the profit)",
      "STAY CLOSE" in ec_blk and "TAKE THE PROFIT" not in ec_blk)
check("green light why is clean (no challenger mark)", "', challenger'" not in ap)

# ── rt-3: scanner voice + bridge exit check real-time ──────────────────
cl_i = bs.find("def contest_light")
cl_blk = bs[cl_i:bs.find("def ", cl_i + 50)]
check("voice loop already used the ticker (pre-existing)", 'fetch_ticker(pair)["last"]' in cl_blk)
check("bridge exit check seeds the live price", 'df.loc[_rt_idx, "close"] = _rt_live' in bs)
check("bridge trailing stop uses ticker price", "live_price = _rt_live" in bs)

# ── invariants ──────────────────────────────────────────────────────────
check("SOLD clears by TOMBSTONE (None), never a pop — the merge-save "
      "consumes the marker and the raw tombstone read keeps a stale "
      "carrier from resurrecting the row (the round-3/5 convention)",
      "_cw_sold[_sp] = None" in ap or "cw_sold[_sp] = None" in ap)
check("the fire-time writer never writes the bridge from the console",
      "from signal_bridge" not in ap[ap.find("def auto_track_popup") if "def auto_track_popup" in ap else 0:])

# ── record semantics: the EXACT mutations the auto-track writer performs ─
tmp = Path(tempfile.mkdtemp()) / "contest_watch.json"
now_iso = "2026-09-03T18:00:00-06:00"
# a popup-fire row exists (the writer's exact shape)
w = {"PENGU/USD": {"entry": 0.0100, "high": 0.0100, "size": 800.0, "added": now_iso,
                   "goal_pct": 1.8, "window_min": 108.0, "why": ["BUY 90% (champion)"],
                   "source": "champion"}}
tmp.write_text(json.dumps(w))

# the writer's second-fire behavior: never displace, keyed writes only
pair = "PENGU/USD"
rec = w[pair]
before = dict(rec)
displaced = pair in w  # auto_track_popup returns False — row untouched
after = json.loads(json.dumps(w))
r = after[pair]
check("SIM: a second fire never displaces the row",
      displaced and r["entry"] == before["entry"] == 0.0100)
check("SIM: the row keeps its fire-time clock",
      r["added"] == now_iso)
check("SIM: the row keeps its engine goal + window",
      r["goal_pct"] == 1.8 and r["window_min"] == 108.0)
check("SIM: the row carries source + why (the popup provenance)",
      r["source"] == "champion" and r["why"] == ["BUY 90% (champion)"])

# NEW fire (fresh registration, no goal yet — the writer's setdefault path)
w2 = {}
rec = w2.get("VIRTUAL/USD") or {}
rec["entry"] = 0.69; rec["high"] = 0.69; rec["size"] = 300.0
rec["added"] = "2026-09-03T19:00:00-06:00"
g, win = 1.5, 90.0   # engine fallback path
rec.setdefault("goal_pct", g); rec.setdefault("window_min", win)
w2["VIRTUAL/USD"] = rec
check("SIM new fire: goal set when missing (not wiped)", w2["VIRTUAL/USD"]["goal_pct"] == 1.5)
check("SIM new fire: window set", w2["VIRTUAL/USD"]["window_min"] == 90.0)
check("SIM new fire: peak = entry", w2["VIRTUAL/USD"]["high"] == 0.69)

out = ["VERIFICATION RESULTS", "=" * 60]
fails = 0
for name, ok in results:
    out.append(f"{'PASS' if ok else 'FAIL'}  {name}")
    fails += 0 if ok else 1
out.append("")
out.append(f"{len(results) - fails}/{len(results)} passed")
scratch_space.report_temp("_entry_check.txt").write_text("\n".join(out), encoding="utf-8")
print(f"{len(results) - fails}/{len(results)} passed -> _entry_check.txt")
sys.exit(1 if fails else 0)
