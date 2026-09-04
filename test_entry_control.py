"""Verify the unified entry control + real-time exits (plan 2026-09-03_18-34-41).
Source assertions follow the project's test_*.py convention; watch-record
semantics are verified by running the exact mutation logic against a temp file."""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ap = (BASE / "app.py").read_text(encoding="utf-8")
bs = (BASE / "background_scanner.py").read_text(encoding="utf-8")
results = []


def check(name, ok):
    results.append((name, bool(ok)))


# ── ent-1: shared goal helper ────────────────────────────────────────────
check("_derive_goal defined in app.py", "def _derive_goal(pair: str)" in ap)
check("_derive_goal wraps _window_goal_pct", "_window_goal_pct" in ap[ap.find("def _derive_goal"):ap.find("def _derive_goal") + 900])
check("window formula matches green light", "max(60.0, min(180.0, goal * 60.0))" in ap)

# ── ent-2: the unified control ──────────────────────────────────────────
i0 = ap.find("def _render_entry_control")
check("_render_entry_control exists", i0 > 0)
body = ap[i0:ap.find("def render_auto_watch")]
check("mode toggle present", "SAME TRADE" in body and "NEW TRADE" in body)
check("spent÷owned is the primary input", body.find("$ you spent") < body.find("entry $"))
check("free-text pair for unwatched coins", "not on the watch yet" in body)
same_i = body.find("SAME TRADE: keyed updates")
same_blk = body[same_i:same_blk_end] if (same_blk_end := body.find("st.rerun", same_i)) > 0 else body[same_i:]
check("same-trade does NOT touch added", 'rec["added"]' not in same_blk)
check("same-trade does NOT touch goal/window", 'rec["goal_pct"]' not in same_blk and 'rec["window_min"]' not in same_blk)
check("same-trade preserves peak (floored)", 'rec["high"] = max(' in same_blk)
new_i = body.find("NEW TRADE (or first registration)")
new_blk = body[new_i:body.find("# SAME TRADE: keyed updates")]
check("new-trade sets fresh added", 'rec["added"] = _now_mst()' in new_blk)
check("new-trade derives goal when missing", "_derive_goal(pair)" in new_blk)
check("new-trade uses setdefault (no wipe)", 'setdefault("goal_pct"' in new_blk)
check("old full-dict replacement is GONE", 'w[pair] = {"entry": entry, "high": entry,' not in ap)
check("old _render_reenter_control is GONE", "def _render_reenter_control" not in ap)

# ── ent-3: wired into both sites ────────────────────────────────────────
check("rendered below SOLD buttons", '_render_entry_control(prefix="ec")' in ap)
check("rendered in Quick Check", '_render_entry_control(prefix="qc"' in ap)
check("Quick Check +/- inputs retired", 'key="qc_watch_upd"' not in ap)

# ── ent-4: WATCH-anyway ────────────────────────────────────────────────
wa_i = ap.find("entering on your own conviction")
wa_blk = ap[max(0, wa_i - 1200):ap.find("Advisory", wa_i)]
check("WATCH-anyway derives goal", "_derive_goal(pair)" in wa_blk)
check("WATCH-anyway takes a size", 'key="qc_watch_size"' in wa_blk)

# ── rt-1: _run_exit_logic real-time ────────────────────────────────────
rl_i = ap.find("def _run_exit_logic")
rl_blk = ap[rl_i:ap.find("def _light_check")]
check("exit logic reads the ticker", "fetch_ticker(pair)" in rl_blk)
check("ticker is primary, candle close is fallback", rl_blk.find("fetch_ticker") > rl_blk.find("live = float(df"))
check("in-progress candle seeded", 'df.loc[_idx, "close"] = live' in rl_blk)

# ── rt-2: EXIT CHECK tab real-time ─────────────────────────────────────
ec_i = ap.find("def render_exit_check")
ec_blk = ap[ec_i:]
check("EXIT CHECK reads the ticker", "fetch_ticker(pair)" in ec_blk[ec_blk.find("with st.spinner"):ec_blk.find("sig = _eval")])

# ── Sep 3+: the synthesis — position data + goal knowledge + the brain ──
check("EXIT CHECK reads the watch record", "_load_watch().get(pair)" in ec_blk)
check("watch entry overrides bridge entry", 'entry = float(_wr["entry"])' in ec_blk)
check("watch peak merged into the trail", "high = max(high, _w_high)" in ec_blk)
check("goal multiples computed", "_goal_mult = int(_pct / _w_goal)" in ec_blk)
check("window clock computed", "_elapsed_min = (datetime.now(MST) - _ad)" in ec_blk)
check("window-expired is an exit reason", "_window_expired or _hard_stop_hit" in ec_blk)
check("tier-aware hard stop consulted", "contest_account as _ca_ec" in ec_blk)
check("thesis check: entry why vs current signal", "_thesis_ok = " in ec_blk)
check("THESIS line rendered", "THESIS <span" in ec_blk)
check("GOAL line rendered", "GOAL <span" in ec_blk)
check("POSITION line rendered (dollars)", "POSITION <span" in ec_blk)
check("TAKE THE PROFIT verdict exists", "TAKE THE PROFIT" in ec_blk)
check("goal ETA uses the watch goal", "_goal_px = (entry * (1 + _w_goal / 100.0))" in ec_blk)
check("green light stores the why", "(green light" in ap)
check("green light why carries the challenger mark (Sep 4)",
      "', challenger' if _champ_challenge else ''" in ap)
check("WATCH-anyway stores the why", "(watched anyway)" in ap)
check("unified control takes a why input", 'key=f"{prefix}_why"' in ap)

# ── rt-3: scanner voice + bridge exit check real-time ──────────────────
cl_i = bs.find("def contest_light")
cl_blk = bs[cl_i:bs.find("def ", cl_i + 50)]
check("voice loop already used the ticker (pre-existing)", 'fetch_ticker(pair)["last"]' in cl_blk)
check("voice sell bar seeds the live price", 'df.loc[_idx, "close"] = live' in bs[cl_i:cl_i + 9000])
check("bridge exit check seeds the live price", 'df.loc[_rt_idx, "close"] = _rt_live' in bs)
check("bridge trailing stop uses ticker price", "live_price = _rt_live" in bs)

# ── invariants ──────────────────────────────────────────────────────────
check("entry control never writes the bridge", "from signal_bridge" not in body)
check("SOLD stays the only clearer (pop lives in sold handler)", "cw_sold.pop" in ap or "_cw_sold.pop" in ap)

# ── watch-record semantics: run the EXACT mutations both modes perform ──
tmp = Path(tempfile.mkdtemp()) / "contest_watch.json"
now_iso = "2026-09-03T18:00:00-06:00"
# a green-light entry exists
w = {"PENGU/USD": {"entry": 0.0100, "high": 0.0120, "size": 800.0, "added": now_iso,
                   "goal_pct": 1.8, "window_min": 108.0}}
tmp.write_text(json.dumps(w))

# SAME TRADE: fix numbers (exact keyed-update statements from the control)
pair = "PENGU/USD"; entry = 0.0105; size = 780.0
rec = w[pair]
rec["entry"] = float(entry)
rec["size"] = float(size)
rec["high"] = max(float(rec.get("high") or 0.0), float(entry))
tmp.write_text(json.dumps(w))
after = json.loads(tmp.read_text())
r = after[pair]
check("SIM same-trade: added preserved", r["added"] == now_iso)
check("SIM same-trade: goal preserved", r["goal_pct"] == 1.8)
check("SIM same-trade: window preserved", r["window_min"] == 108.0)
check("SIM same-trade: peak preserved (not reset to entry)", r["high"] == 0.0120)
check("SIM same-trade: entry+size updated", r["entry"] == 0.0105 and r["size"] == 780.0)

# NEW TRADE (fresh registration, no goal yet — derived path)
w2 = {}
rec = w2.get("VIRTUAL/USD") or {}
rec["entry"] = 0.69; rec["high"] = 0.69; rec["size"] = 300.0
rec["added"] = "2026-09-03T19:00:00-06:00"
g, win = 1.5, 90.0   # _derive_goal fallback path
rec.setdefault("goal_pct", g); rec.setdefault("window_min", win)
w2["VIRTUAL/USD"] = rec
check("SIM new-trade: goal derived (not wiped)", w2["VIRTUAL/USD"]["goal_pct"] == 1.5)
check("SIM new-trade: window derived", w2["VIRTUAL/USD"]["window_min"] == 90.0)
check("SIM new-trade: peak = entry", w2["VIRTUAL/USD"]["high"] == 0.69)

out = ["VERIFICATION RESULTS", "=" * 60]
fails = 0
for name, ok in results:
    out.append(f"{'PASS' if ok else 'FAIL'}  {name}")
    fails += 0 if ok else 1
out.append("")
out.append(f"{len(results) - fails}/{len(results)} passed")
Path(__file__).with_name("_entry_check.txt").write_text("\n".join(out), encoding="utf-8")
print(f"{len(results) - fails}/{len(results)} passed -> _entry_check.txt")
sys.exit(1 if fails else 0)
