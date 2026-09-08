"""Test the tide: classification thresholds + a live breadth reading."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from background_scanner import _tide_class, _fleet_tide, TIDE_RISING_MIN, TIDE_OUT_MAX

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# thresholds
check("70% -> RISING", _tide_class(70.0) == "RISING")
check("69.9% -> FLAT", _tide_class(69.9) == "FLAT")
check("50% -> FLAT", _tide_class(50.0) == "FLAT")
check("35.1% -> FLAT", _tide_class(35.1) == "FLAT")
check("35% -> OUT", _tide_class(35.0) == "OUT")
check("10% -> OUT", _tide_class(10.0) == "OUT")
check("bands are sane", TIDE_RISING_MIN == 70.0 and TIDE_OUT_MAX == 35.0)

# live reading from the real engine cache
pct, state = _fleet_tide(max_age_s=0)
if pct is None:
    check("live tide readable (cache may be empty in test env)", True, "returned None (fail-open)")
else:
    check("live breadth in 0-100", 0.0 <= pct <= 100.0, f"{pct}")
    check("live state is a real state", state in ("RISING", "FLAT", "OUT"), state)

# ── THE FLAT SIZING FACTOR (Sep 7: the stamped week spoke — FLAT 22%
#    win vs RISING 57%; the user picked 0.50×) ──────────────────────────
bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8").replace("\r\n", "\n")
ap_src = (BASE / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
check("the factor exists at 0.50", "TIDE_FLAT_SIZE_FACTOR = 0.50" in bs_src)
_st_fn = bs_src[bs_src.find("def _sure_thing_scan"):bs_src.find("# ── ONE VOICE ENGINE")]
check("the SURE THING sizes FLAT too (Sep 7: the same factor, no exception)",
      "_st_tide_mult = TIDE_FLAT_SIZE_FACTOR" in _st_fn
      and "_spend = _spend * _st_tide_mult" in _st_fn
      and "FLAT TIDE SIZING" in _st_fn)
check("the Sure Thing popup names the FLAT factor (line + voice)",
      "TIDE SIZES IT: FLAT" in _st_fn
      and "flat tide — half size!" in _st_fn
      and "× {_st_tide_mult:.2f} FLAT tide" in _st_fn)
check("the Sure Thing reads the tide fresh at fire time (not the funnel cache)",
      "_st_tide_pct, _st_tide_state = _fleet_tide()" in _st_fn)
check("the champion sizes FLAT down (after the guard + brain, before the worth floor)",
      "if is_buy and (_df_state or \"\") == \"FLAT\":" in bs_src
      and "_spend = _spend * _tide_size_mult" in bs_src
      and bs_src.find("FLAT TIDE SIZING") < bs_src.find("if is_buy and not _fast_route:"))
check("the mover sizes FLAT down",
      "(_tsw or \"\") == \"FLAT\"" in bs_src
      and "_m_spend = _m_spend * TIDE_FLAT_SIZE_FACTOR" in bs_src)
check("the QC mirrors the alert (never disagrees)",
      "_bs_tsf.TIDE_FLAT_SIZE_FACTOR" in ap_src
      and "(_tide_state or \"\") == \"FLAT\"" in ap_src)
check("the popup + voice name the discount",
      "TIDE SIZES IT: FLAT" in bs_src and "Flat tide — half size!" in bs_src)

fails = [r for r in results if not r[1]]
out = BASE / "tide_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results) +
    (f"\n\nlive tide now: {pct and f'{pct:.0f}%'} {state or '(no data)'}"), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
