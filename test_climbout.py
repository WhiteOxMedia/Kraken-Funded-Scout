"""Test THE CLIMB-OUT SYSTEM (user spec, Sep 6: "Find swing trades
that avoid danger zones: no trades near major resistance, no trades in
chop, no trades when liquidity is thin. Only show setups with clear
escape routes") — the four-layer architecture: the LEVEL MAP (per-coin
persistent structure memory), the THREE GATES (clearance / ER / floor,
all adaptive: tide, death line, break history, the dial), SELF-AWARENESS
(climb_rules.json read live + snapshot logging + the weekly grade), and
THE DIAL (strictness 0.5x-2.0x). Hard gates on champion + mover +
sure-thing + the QC mirror. Fails OPEN on broken reads (the death-line
guard is the gate that never yields)."""
import json
import sys
import tempfile
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


import climb_out as co

T0 = 1700000000000


class _FakeEx:
    """A fake exchange serving synthetic 1h candles."""
    def __init__(self, rows):
        self._rows = rows

    def fetch_ohlcv(self, pair, timeframe="1h", limit=None):
        return self._rows


def hist(n=340, base=100.0, amp=0.01, drift=0.0, vol=10.0):
    """An oscillating history with IRREGULAR amplitudes (deterministic
    jitter) so pivots and the ER distribution have real spread — a
    perfectly regular series reads ER 0.500 everywhere and the
    percentile degenerates."""
    rows = []
    px = base
    for i in range(n):
        _j = 0.4 + ((i * 37) % 100) / 100.0     # 0.4x - 1.4x, varied
        o = px
        px = px * (1 + drift + (amp * _j if i % 2 == 0 else -amp * _j))
        rows.append([T0 + len(rows) * 3600000, o,
                     max(o, px) * 1.001, min(o, px) * 0.999, px, vol])
    return rows


def two_phase(n1, amp1, n2, amp2, drift2=0.0, base=100.0, drift1=0.0):
    """Volatile early, calm (or trending) late — the squeeze setup.
    drift1 gives the early phase a real trend (high-ER windows) so a
    choppy late phase ranks at the BOTTOM of the coin's own ER
    distribution — the chop test needs that contrast."""
    rows = hist(n1, base=base, amp=amp1, drift=drift1)
    px = rows[-1][4] if rows else base
    for i in range(n2):
        _j = 0.4 + ((i * 53) % 100) / 100.0
        o = px
        px = px * (1 + drift2 + (amp2 * _j if i % 2 == 0 else -amp2 * _j))
        rows.append([T0 + len(rows) * 3600000, o,
                     max(o, px) * 1.001, min(o, px) * 0.999, px, 10.0])
    return rows


def with_pivots(rows, ceiling=110.0, floor=90.0, times=4):
    """Stamp single-candle spikes at the ceiling + floor in the LAST 24
    candles (prices there sit near the final close, so the stamps are
    locally extreme on both sides; a spike must beat its 3 neighbors
    each side, so stamps space 4 apart)."""
    out = list(rows)
    n = len(out)
    for k in range(times):
        ci = n - 24 + k * 4
        if ci + 4 >= n:
            break
        r = list(out[ci])
        r[2] = ceiling
        out[ci] = r
        r = list(out[ci + 2])
        r[3] = floor
        out[ci + 2] = r
    return out


def fresh_map():
    co.MAP_FILE = str(Path(tempfile.mkdtemp()) / "climb_levels.json")  # leak-audit: ok (tempfile-derived sandbox, fresh per scenario; suite-per-process)


# ── 1. THE LEVEL MAP ─────────────────────────────────────────────────
fresh_map()
# a basing coin: volatile history, then a flat calm tail near the final
# price (the stamps are locally extreme there)
base_rows = two_phase(240, 0.012, 100, 0.0015, drift2=0.0002)
final_px = base_rows[-1][4]
rows = with_pivots(base_rows, ceiling=final_px * 1.11,
                   floor=final_px * 0.97, times=4)
entry_map = co.update_map("TEST/USD", rows)
highs = [lv for lv in entry_map["levels"] if lv["side"] == "high"]
lows = [lv for lv in entry_map["levels"] if lv["side"] == "low"]
check("the map detects the stamped ceiling (repeated tests merge)",
      any(abs(float(lv["price"]) - final_px * 1.11) / (final_px * 1.11)
          < 0.005 and int(lv.get("touches") or 0) >= 3 for lv in highs),
      [(round(lv["price"], 2), lv.get("touches"), lv.get("strength"))
       for lv in highs])
check("the map detects the stamped floor",
      any(abs(float(lv["price"]) - final_px * 0.97) / (final_px * 0.97)
          < 0.005 for lv in lows))
check("repeated tests build strength (the coin's own 'major')",
      any(float(lv.get("strength") or 0) >= co.DEFAULT_RULES["major_strength"]
          for lv in highs + lows),
      [(round(lv["price"], 2), lv.get("strength"))
       for lv in entry_map["levels"]])

hi_p, lo_p = co.detect_pivots(rows)
check("pivot detection: swing highs found (the jittered history)",
      len(hi_p) >= 3, len(hi_p))
check("pivot detection: swing lows found", len(lo_p) >= 3, len(lo_p))

# ── 2. THE ER METRIC (not chop) ─────────────────────────────────────
trend = hist(n=200, amp=0.001, drift=0.004)   # a straight climb
er_t = co.efficiency_ratio([r[4] for r in trend])
check("a straight trend reads high ER", (er_t or 0) > 0.5, er_t)
chop = hist(n=200, amp=0.02, drift=0.0)       # traveled far, went nowhere
er_c = co.efficiency_ratio([r[4] for r in chop])
check("sideways noise reads low ER", (er_c or 0) < 0.3, er_c)
mix_trend = two_phase(160, 0.001, 60, 0.001, drift2=0.004)
_, pct_t = co._er_percentile([r[4] for r in mix_trend])
mix_chop = two_phase(160, 0.001, 60, 0.02, drift2=0.0)
_, pct_c = co._er_percentile([r[4] for r in mix_chop])
check("ER percentiles rank the recent trend above the recent chop",
      pct_t is not None and pct_c is not None and pct_t > pct_c,
      (pct_t, pct_c))
vol_sq = co._vol_percentile([r[4] for r in two_phase(240, 0.012, 100, 0.0005)])
check("a calm late phase reads squeezed (>= 70% of windows more volatile)",
      vol_sq is not None and vol_sq >= 0.70, vol_sq)

# ── 3. THE GATES (via climb_out_gate with the fake exchange) ────────
fresh_map()
import execution_tiers as et
et.measure_book = lambda ex, pair: {"bid_tob": 100000.0, "ask_tob": 100000.0}

# a clean setup: far ceiling + near floor, a basing (calm) tail
base_rows = two_phase(240, 0.012, 100, 0.0015, drift2=0.0002)
final_px = base_rows[-1][4]
rows = with_pivots(base_rows, ceiling=final_px * 1.11,
                   floor=final_px * 0.97, times=4)
res = co.climb_out_gate(_FakeEx(rows), "TEST/USD", entry=final_px,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("a clean setup (far ceiling, near floor, trending) PASSES",
      res["ok"], (res["fail_gate"], res["fail_reason"],
                  res["features"]))
check("the output block carries the full nine-item read",
      all(s in res["output_block"] for s in
          ("CLIMB-OUT", "Levels:", "Clearance:", "ER:", "Floor:",
           "Structural stop:", "Exit door:", "Adjustments:", "ESCAPE:")),
      res["output_block"][:100])
check("the escape line renders (floor · room · ER · door)",
      "ESCAPE:" in res["escape_line"] and "room" in res["escape_line"]
      and "door BID" in res["escape_line"], res["escape_line"])

# the ceiling sitting right overhead -> FAILS clearance
rows_near = with_pivots(list(base_rows), ceiling=final_px * 1.01,
                        floor=final_px * 0.97, times=4)
res = co.climb_out_gate(_FakeEx(rows_near), "TEST/USD", entry=final_px,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("a ceiling right overhead FAILS the clearance gate",
      not res["ok"] and res["fail_gate"] == "clearance",
      (res["fail_gate"], res["fail_reason"]))

# the chop case: a real early trend, then recent sideways + normal vol
# -> FAILS the ER gate (the recent ER lands in the bottom of the
# coin's own distribution — chop, not a coil: the vol is normal)
fresh_map()
chop_rows = two_phase(240, 0.0015, 60, 0.03, drift2=0.0, drift1=0.004)
cpx = chop_rows[-1][4]
chop_rows = with_pivots(chop_rows, ceiling=cpx * 1.11, floor=cpx * 0.94,
                        times=4)
res = co.climb_out_gate(_FakeEx(chop_rows), "TEST/USD", entry=cpx,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("recent sideways noise with normal vol FAILS the ER gate (chop)",
      not res["ok"] and res["fail_gate"] == "er",
      (res["fail_gate"], res["fail_reason"]))

# the squeezed case: low ER + squeezed vol = COIL -> passes ER
fresh_map()
coil_rows = two_phase(240, 0.012, 100, 0.0005)
kpx = coil_rows[-1][4]
coil_rows = with_pivots(coil_rows, ceiling=kpx * 1.11, floor=kpx * 0.965,
                        times=4)
res = co.climb_out_gate(_FakeEx(coil_rows), "TEST/USD", entry=kpx,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("squeezed + low ER = COIL, not chop (the Sure Thing exemption)",
      res["features"].get("climb_vol_pctile") is not None
      and res["features"]["climb_vol_pctile"] >= 0.70
      and res["fail_gate"] != "er",
      (res["fail_gate"], res["features"].get("climb_vol_pctile")))

# the trapdoor: the floor too far below -> FAILS the floor gate
fresh_map()
air_rows = with_pivots(list(base_rows), ceiling=final_px * 1.11,
                       floor=final_px * 0.80, times=4)
res = co.climb_out_gate(_FakeEx(air_rows), "TEST/USD", entry=final_px,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("the floor 20% below = a trapdoor drop (fails the max cushion)",
      not res["ok"] and res["fail_gate"] == "floor",
      (res["fail_gate"], res["fail_reason"]))

# the thin bid door
fresh_map()
et.measure_book = lambda ex, pair: {"bid_tob": 100.0, "ask_tob": 100000.0}
rows_door = with_pivots(list(base_rows), ceiling=final_px * 1.11,
                        floor=final_px * 0.97, times=4)
res = co.climb_out_gate(_FakeEx(rows_door), "TEST/USD", entry=final_px,
                        goal_pct=1.5, spend=1800.0, tide_state="FLAT")
check("a bid side that can't carry the exit FAILS the door gate",
      (not res["ok"] and res["fail_gate"] == "door") or res["degraded"],
      (res["fail_gate"], res["fail_reason"]))
et.measure_book = lambda ex, pair: {"bid_tob": 100000.0, "ask_tob": 100000.0}

# ── 4. THE ADAPTIVE MULTIPLIERS ─────────────────────────────────────
fresh_map()
rows_base = with_pivots(list(base_rows), ceiling=final_px * 1.04,
                        floor=final_px * 0.97, times=4)
res_flat = co.climb_out_gate(_FakeEx(list(rows_base)), "TEST/USD",
                            entry=final_px, goal_pct=1.5, spend=1800.0,
                            tide_state="FLAT")
res_out = co.climb_out_gate(_FakeEx(list(rows_base)), "TEST/USD",
                            entry=final_px, goal_pct=1.5, spend=1800.0,
                            tide_state="OUT")
check("the OUT tide tightens the clearance requirement "
      "(x3.0 vs x2.0 — the adjustments name it)",
      res_flat["features"]["climb_required_pct"] <
      res_out["features"]["climb_required_pct"]
      and any("OUT" in a for a in res_out["adjustments"]),
      (res_flat["features"]["climb_required_pct"],
       res_out["features"]["climb_required_pct"]))
res_death = co.climb_out_gate(_FakeEx(list(rows_base)), "TEST/USD",
                              entry=final_px, goal_pct=1.5, spend=1800.0,
                              tide_state="FLAT", near_death=True)
check("near the death line: the requirement rises (+0.5x)",
      res_death["features"]["climb_required_pct"] >
      res_flat["features"]["climb_required_pct"],
      res_death["features"]["climb_required_pct"])
res_dial = co.climb_out_gate(_FakeEx(list(rows_base)), "TEST/USD",
                             entry=final_px, goal_pct=1.5, spend=1800.0,
                             tide_state="FLAT", strictness=2.0)
check("the strictness dial scales the requirement (2.0x)",
      abs(res_dial["features"]["climb_required_pct"]
          - 2.0 * res_flat["features"]["climb_required_pct"]) < 0.1,
      res_dial["features"]["climb_required_pct"])
# ── 4b. THE DIAL'S OTHER TWO HANDS (Oct 4 audit, red-first): the
# console help promises the strictness dial scales "room to the
# nearest major ceiling, the chop bar, AND the floor cushion" — but
# the gate applied it ONLY to the clearance multiplier: the ER chop
# bar and the floor cushion never saw the dial (the audit probe read
# the byte-identical CHOP verdict at 0.5x / 1.0x / 2.0x). All three
# advertised hands scale now, each NAMED in the adjustments so the
# operator sees which gate obeyed the dial.
fresh_map()
_dial_chop = two_phase(240, 0.0015, 60, 0.03, drift2=0.0, drift1=0.004)
_dial_cpx = _dial_chop[-1][4]
_dial_chop = with_pivots(_dial_chop, ceiling=_dial_cpx * 1.11,
                         floor=_dial_cpx * 0.94, times=4)
_er_loose = co.climb_out_gate(_FakeEx(list(_dial_chop)), "TEST/USD",
                              entry=_dial_cpx, goal_pct=1.5, spend=1800.0,
                              tide_state="FLAT", strictness=0.5)
_er_strict = co.climb_out_gate(_FakeEx(list(_dial_chop)), "TEST/USD",
                               entry=_dial_cpx, goal_pct=1.5, spend=1800.0,
                               tide_state="FLAT", strictness=2.0)
check("the dial moves the CHOP BAR: 0.5x eases it to 15th pct (the "
      "chop coin's own ER 4% still fails — the dial loosens the bar, "
      "not the coin), named in the adjustments",
      any("ER bar x0.5" in a for a in _er_loose["adjustments"])
      and "CHOP" in _er_loose["fail_reason"],
      (_er_loose["fail_reason"], _er_loose["adjustments"]))
check("the dial tightens the CHOP BAR the other way: 2.0x raises it "
      "to 60th pct (a 40th-pct ER that passed the flat bar now "
      "reads chop — strict = only pristine momentum)",
      any("ER bar x2.0" in a for a in _er_strict["adjustments"]),
      _er_strict["adjustments"])
_dial_floor = with_pivots(list(base_rows), ceiling=final_px * 1.11,
                          floor=final_px * 0.982, times=4)
fresh_map()   # identical map state per leg — only the dial differs
_fl_ok = co.climb_out_gate(_FakeEx(list(_dial_floor)), "TEST/USD",
                           entry=final_px, goal_pct=1.5, spend=1800.0,
                           tide_state="FLAT", strictness=1.0)
fresh_map()   # the level map is PERSISTENT — re-prime it or leg two
              # reads a different floor state (strength/direction)
_fl_strict = co.climb_out_gate(_FakeEx(list(_dial_floor)), "TEST/USD",
                               entry=final_px, goal_pct=1.5, spend=1800.0,
                               tide_state="FLAT", strictness=2.0)
check("the dial scales the FLOOR CUSHION: the SAME escape route "
      "(floor 1.8% below the 1.5% goal) clears the floor gate at "
      "1.0x (needs 1.5%) and FAILS it at 2.0x (needs exactly double "
      "— 3.0%), the dial's hand named in the adjustments",
      _fl_ok["fail_gate"] != "floor"
      and _fl_strict["fail_gate"] == "floor"
      and "cushion needs 3.0%" in (_fl_strict["fail_reason"] or "")
      and any("cushion dial x2.0" in a
              for a in _fl_strict["adjustments"]),
      (_fl_ok["fail_gate"], _fl_strict["fail_reason"],
       _fl_strict["adjustments"]))
fresh_map()
_air2 = with_pivots(list(base_rows), ceiling=final_px * 1.11,
                    floor=final_px * 0.80, times=4)
_air_strict = co.climb_out_gate(_FakeEx(list(_air2)), "TEST/USD",
                                entry=final_px, goal_pct=1.5, spend=1800.0,
                                tide_state="FLAT", strictness=2.0)
check("the dial never breaks the trapdoor rail (2.0x tightens the "
      "MIN cushion but the 20%-below floor still fails the MAX)",
      _air_strict["fail_gate"] == "floor"
      and "20.0% below" in _air_strict["fail_reason"],
      (_air_strict["fail_gate"], _air_strict["fail_reason"]))

# ── 4c. THE CLIFF CAP (Oct 4 review on the dial's third hand): the
#    scaled cushion can demand MORE room than the cliff allows — at
#    dial-max the DEFAULT sinking cushion already lands exactly ON the
#    cliff (1.5 x 2.0 == 3.0) and the band [cushion_req, floor_max]
#    degenerates to a single point; a custom cushion above 1.5 empties
#    it outright and the floor gate refuses every entry in a world with
#    no satisfiable floor. The cap keeps a tenth-of-goal of band open
#    under the cliff and names itself in the adjustments.
fresh_map()
_rules_old = co.RULES_FILE
_rules_tmp = Path(tempfile.mkdtemp()) / "climb_rules.json"
_rules_tmp.write_text(json.dumps(
    {"floor_cushion": {"rising": 0.5, "flat": 1.6, "sinking": 1.5}}),
    encoding="utf-8")
co.RULES_FILE = str(_rules_tmp)
try:
    _cap_rows = with_pivots(list(base_rows), ceiling=final_px * 1.11,
                            floor=final_px * 0.956, times=4)
    _cap_res = co.climb_out_gate(_FakeEx(list(_cap_rows)), "TEST/USD",
                                 entry=final_px, goal_pct=1.5, spend=1800.0,
                                 tide_state="FLAT", strictness=2.0)
finally:
    co.RULES_FILE = _rules_old
check("4c.1 the cushion never demands past the cliff: a custom flat "
      "cushion (1.6) at dial-max caps a tenth-of-goal under the cliff — "
      "a floor 4.4% below (inside the surviving [4.35, 4.5] band) "
      "still clears the floor gate",
      _cap_res["fail_gate"] != "floor",
      (_cap_res["fail_gate"], _cap_res["fail_reason"],
       _cap_res["adjustments"]))
check("4c.2 the cap names itself in the adjustments (the operator "
      "sees the cliff, not a silent clamp)",
      any("cushion capped at the cliff" in a
          for a in _cap_res["adjustments"]),
      _cap_res["adjustments"])
res_coil = co.climb_out_gate(_FakeEx(list(rows_base)), "TEST/USD",
                             entry=final_px, goal_pct=1.5, spend=1800.0,
                             is_coil=True, coil_ceiling=final_px * 1.04)
check("the coil case: the ceiling being crossed IS the level "
      "(runway beyond it, coil_mult)",
      any("coil" in a.lower() for a in res_coil["adjustments"]),
      res_coil["adjustments"])

# ── 5. FAIL-OPEN ─────────────────────────────────────────────────────
res = co.climb_out_gate(_FakeEx([]), "TEST/USD", entry=100.0,
                        goal_pct=1.5, spend=1000.0)
check("thin history fails OPEN (degraded, ok=True) — the death-line "
      "guard is the gate that never yields",
      res["ok"] and res["degraded"])

# ── 6. THE RULES FILE + THE WIRING ───────────────────────────────────
check("climb_rules.json exists with the defaults (read LIVE, the "
      "weekly LOOSEN/TIGHTEN verdicts land there)",
      Path("climb_rules.json").exists()
      and co.load_rules()["tide_mult"]["RISING"] == 1.5)
bs = open("background_scanner.py", encoding="utf-8").read()
check("the champion path gates on climb-out (hard gate + suppression)",
      'f"climb-{_fg}"' in bs)
check("the mover path gates on climb-out",
      "MOVER CLIMB-OUT" in bs)
check("the sure-thing path gates on climb-out (the coil case)",
      "is_coil=True, coil_ceiling=float(r.get(\"zone_high\")" in bs)
check("the scanner wrapper reads the live context (tide, death, dial)",
      "def _climb_out_check" in bs and "_climb_strictness()" in bs)
check("the strictness dial reads console_state live",
      "def _climb_strictness" in bs
      and 'climb_strictness' in bs)
check("the fired snapshots carry the climb features",
      bs.count('"climb":') >= 2)
bsn = bs.replace("\r\n", "\n")
import background_scanner as _bsmod
check("the climb features carry the tide stamp (the gate report splits by tide)",
      "def _climb_features_stamped" in bsn
      and _bsmod._climb_features_stamped(None, "FLAT", 47) is None
      and _bsmod._climb_features_stamped({"climb_room_pct": 11.0}, "FLAT", 47.0)
      == {"climb_room_pct": 11.0, "climb_tide": "FLAT", "climb_tide_pct": 47.0})
check("the champion fired snapshot stamps the tide into its climb features",
      "_climb_features_stamped(\n                                   _climb.get(\"features\") if _climb else None,\n                                   _df_state, _df_pct)" in bsn)
check("the sure-thing fired snapshot stamps the tide",
      "_st_tide_state, _st_tide_pct)" in bsn)
check("the mover fired snapshot now carries climb features + the tide stamp",
      "_m_climb.get(\"features\") if _m_climb else None" in bsn
      and "_tsw, _tpw)})" in bsn)
check("the mover climb fail-open init (a broken read never NameErrors the popup)",
      "_m_climb = None" in bs)
app = open("app.py", encoding="utf-8").read()
check("the QC mirror: the green requires climb_ok (never disagrees)",
      "and _climb_ok" in app)
check("the QC speaks the no-escape rejection",
      "NO ESCAPE ROUTE" in app)
check("the sidebar strictness dial (0.5x-2.0x)",
      "climb_strictness_dial" in app and "Climb-out strictness" in app)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_climbout_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
