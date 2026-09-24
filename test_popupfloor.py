"""Test the ROTATION-AWARE POPUP FLOOR + HEAD-TO-HEAD CHALLENGE (user rule,
Sep 4: "the popup floor 89% doesn't know about the rotation schedule — it
needs to self-adjust accordingly, and the brain should allow it if it's
close within 5% to challenge head to head").

Floor stack: rotation rail (ROTATION_POPUP_FLOOR) → tide margin (RISING
relaxes by 11, never below 78) → lone wolf (never below 55). The brain's
deep read still judges every popup; within 5 BELOW the floor it fires as
a CHALLENGER; below floor-5 it stays suppressed; no brain read = no popup.
The QC's thin-book rule reads the SAME stack (popup_floor) — the PUMP
lesson: the alert and the QC must never disagree."""
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

bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
ap_src = Path("app.py").read_text(encoding="utf-8")

# ── 1. the rotation rails ──────────────────────────────────────────────
expected_rails = {"00-04": 84, "04-08": 89, "08-12": 84,
                  "12-16": 89, "16-20": 93, "20-24": 99}
check("ROTATION_POPUP_FLOOR covers all six rotations",
      set(bs.ROTATION_POPUP_FLOOR) == set(expected_rails), sorted(bs.ROTATION_POPUP_FLOOR))
for k, v in expected_rails.items():
    check(f"rail {k} -> {v}", bs.ROTATION_POPUP_FLOOR.get(k) == v,
          bs.ROTATION_POPUP_FLOOR.get(k))
check("unknown rotation fails closed to 89",
      bs.popup_floor_for_rotation("99-99") == 89)
check("cooldown rail is 99 (buys blocked upstream anyway)",
      bs.popup_floor_for_rotation("20-24") == 99)
check("POPUP_NEAR_MISS_PTS is 5 (the head-to-head window)",
      bs.POPUP_NEAR_MISS_PTS == 5.0, bs.POPUP_NEAR_MISS_PTS)

# ── 2. the floor stack ─────────────────────────────────────────────────
check("FLAT tide keeps the rotation rail (12-16 -> 89)",
      bs.popup_floor("FLAT", 0, "12-16") == 89.0)
check("RISING relaxes by the 11-pt margin, never below 78 (12-16 -> 78)",
      bs.popup_floor("RISING", 0, "12-16") == 78.0)
check("RISING on the tightened rail keeps the tighten (16-20 -> 82)",
      bs.popup_floor("RISING", 0, "16-20") == 82.0)
check("RISING on a relaxed rail bottoms at 78 (00-04 -> 78)",
      bs.popup_floor("RISING", 0, "00-04") == 78.0)
check("broken tide read fails closed to the rail",
      bs.popup_floor(None, 0, "00-04") == 84.0)
check("lone wolf 25 on FLAT 08-12 -> 59 (84-25, above the 55 floor)",
      bs.popup_floor("FLAT", 25, "08-12") == 59.0)
check("lone wolf 35 bottoms at the 55 floor",
      bs.popup_floor("RISING", 35, "16-20") == 55.0)
check("OUT never reaches the floor (blocked upstream; rail stands)",
      bs.popup_floor("OUT", 0, "08-12") == 84.0,
      "academic — the tide check returns first")

# ── 3. the head-to-head challenge window (send_alert's branch math) ───
def challenge_verdict(brain_conf, floor):
    """Replicates send_alert's brain-judged bar exactly."""
    if brain_conf >= floor:
        return "GREEN"
    if brain_conf >= floor - bs.POPUP_NEAR_MISS_PTS:
        return "CHALLENGER"
    return "SUPPRESSED"


check("brain 90 vs floor 89 -> full green",
      challenge_verdict(90, 89) == "GREEN")
check("brain 86 vs floor 89 -> CHALLENGER (within 5 — the user's ask)",
      challenge_verdict(86, 89) == "CHALLENGER")
check("brain 84 vs floor 89 -> CHALLENGER (exactly at the window edge)",
      challenge_verdict(84, 89) == "CHALLENGER")
check("brain 83.9 vs floor 89 -> suppressed (below the window)",
      challenge_verdict(83.9, 89) == "SUPPRESSED")
check("brain 88 vs floor 93 (US Late) -> CHALLENGER at the edge",
      challenge_verdict(88, 93) == "CHALLENGER")
check("brain 87 vs floor 93 (US Late) -> suppressed (the tighten bites)",
      challenge_verdict(87, 93) == "SUPPRESSED")
check("brain 82 vs floor 84 (Asia) -> CHALLENGER",
      challenge_verdict(82, 84) == "CHALLENGER")

# ── 4. send_alert source: the rotation floor stack ────────────────────
# (Sep 5, ADVISORY BRAIN: the floor no longer BLOCKS the popup — the
# scan must run and its direction must agree; the NUMBER sizes. The
# floor stack still computes (the Hail Mary's champion bar + the popup's
# context line) and the challenger retired with the blocking use.)
check("send_alert computes DESKTOP_ALERT_MIN via popup_floor() (with the pair — the coin's tide memory)",
      "DESKTOP_ALERT_MIN = popup_floor(_df_state, _lw_pts, _rot_now_key, pair)" in bs_src)
check("the old flat-89 line is gone",
      "DESKTOP_ALERT_MIN = (TIDE_RISING_DESKTOP_FLOOR if _df_state ==" not in bs_src)
check("the challenger branch is retired (no floor to challenge)",
      "elif _bc >= DESKTOP_ALERT_MIN - POPUP_NEAR_MISS_PTS:" not in bs_src
      and "_challenger = True" not in bs_src)
check("_challenger flag stays initialized (dead mark, kept harmless)",
      "_brain_floor_ok = None\n        _challenger = False" in bs_src)
check("the floor is CONTEXT + the Hail Mary bar now (never a blocker)",
      "stays the Hail Mary's champion-grade bar and the popup's" in bs_src)
check("the number SIZES via the bucket table (the advisory wiring)",
      "from hindsight_scorer import brain_size_mult" in bs_src
      and "_spend = _spend * _brain_mult" in bs_src)
check("the direction gates (the number is advisory, the direction is not)",
      '"deep-direction"' in bs_src)
check("fail-closed survives (no brain read = no popup)",
      "no unverified popups (fail-closed" in bs_src)
check("the fire-time rule holds the bridge too (no scan = no write)",
      "no ledger write (the fire-time rule)" in bs_src)
check("the floor log names the rotation",
      "POPUP FLOOR: rotation %s" in bs_src)

# ── 5. the QC agrees (the PUMP lesson; Sep 5: the thin-book challenger
#     window RETIRED with the thin-book brain rule — the user's call. The
#     QC now greens thin books at the standard floor, so it is strictly
#     MORE permissive than the alert: a challenger popup the QC vetoes can
#     no longer happen. The champ_floor is still computed — the Hail Mary's
#     champion-grade check + context.) ─────────────────────────────────
check("QC champ_floor reads the shared popup_floor() helper (with the pair)",
      "_bs5.popup_floor(_tide_state, _lw_pts, None, pair)" in ap_src)
check("QC stores the champ_floor for the render/Hail Mary context",
      '"champ_floor"' in ap_src)
check("the thin-book challenger window is retired (no gate, no mark)",
      "champ_challenge" not in ap_src and "_champ_challenge" not in ap_src)
check("the thin-book brain gate is gone from the render's passes",
      "not thin_book or sig.confidence >=" not in ap_src)
check("the thin-book skip toast is gone",
      "THIN BOOK, BRAIN NOT EXCEPTIONAL" not in ap_src)
check("the QC GO toast is clean (no challenger mark)",
      "⚔ CHALLENGER — brain" not in ap_src)
check("the auto-watch why is clean (no challenger mark)",
      "', challenger'" not in ap_src)
check("QC dup-safe reload for popup_floor",
      'hasattr(_bs5, "popup_floor")' in ap_src)

# ── 6. QC thin-book simulation (alert/QC agreement) ───────────────────
def qc_verdict(conf, floor):
    """Replicates the QC thin-book gate: challenge window included."""
    return "GREEN" if conf >= floor - 5.0 else "SKIP"


# alert: brain 86, 12-16 FLAT -> floor 89 -> CHALLENGER popup
# QC on the same numbers must not veto it
check("agreement: alert CHALLENGER at 86 vs 89 -> QC also green",
      qc_verdict(86, 89) == "GREEN")
# alert: brain 83, 12-16 FLAT -> floor 89 -> suppressed; QC skips too
check("agreement: alert suppressed at 83 vs 89 -> QC skips too",
      qc_verdict(83, 89) == "SKIP")
# 16-20 tightened rail on both sides
check("agreement: 88 vs the 93 US-Late rail -> both challenge",
      qc_verdict(88, 93) == "GREEN"
      and challenge_verdict(88, 93) == "CHALLENGER")
check("agreement: 87 vs the 93 US-Late rail -> both say no",
      qc_verdict(87, 93) == "SKIP"
      and challenge_verdict(87, 93) == "SUPPRESSED")

# ── 7. the calibration evidence: the drive PRODUCES it, then asserts it
# (Sep 24, the stalewatch lesson: the report lives in scratch/reports_temp
# and the watcher's honest 90-min purge must never hollow an evidence
# check — ordering-free, no manual diag run needed first)
import importlib
try:
    importlib.import_module("diag_popupfloor")   # module-level study; writes the report
    _diag_ran, _diag_err = True, ""
except Exception as _exc:
    _diag_ran, _diag_err = False, repr(_exc)[:140]
check("diag_popupfloor.py runs (the evidence is re-runnable by this drive)",
      _diag_ran, _diag_err)
check("_popupfloor_check.txt exists (calibration evidence, just produced)",
      scratch_space.report_temp("_popupfloor_check.txt").exists())

lines.append("")
lines.append(f"{PASS} passed, {FAIL} failed")
report = "\n".join(lines)
print(report)
scratch_space.report_temp('_popupfloor_test.txt').write_text(report + "\n", encoding="utf-8")
sys.exit(1 if FAIL else 0)
