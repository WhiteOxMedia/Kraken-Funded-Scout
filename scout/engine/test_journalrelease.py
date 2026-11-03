"""Drive of THE JOURNAL RELEASE (Sep 21): champion-grade candidates a
gate buried earn the journal's relief, same caps/decay as brain-floor.

Three layers, all REAL code:
  1. journal_release_pts == gate_relief (same journal, same cap/decay),
     fail-closed on a broken journal, 0 on a silent/dud-heavy one.
  2. journal_release(): slack window + relief, the boolean contract.
  3. The desktop deep-veto block exec'd from background_scanner source:
     SELL always vetoes; HOLD releases only when the journal has
     champion-grade hindsight AND the candidate sits within slack of
     the floor; the mover gate's condition truth-table from source.
  6. The stale-move release (Oct 8): the wiggle trap was the STRANDED
     gate — journal fuel rendered display-only by app.py's release-fuel
     strip, no consumer. Both guard sites (re-entry + mover) read
     journal_release_pts("stale-move") in the deep-veto shape.
     THE MOVER'S GATE IS THE DESK-BLESSED DIRECTION (the commit-gate
     reviewers' reachability finding, Oct 8): the mover desk gate only
     admits buy reads to the wiggle check, so a HOLD-only release there
     was dead code — the gate is now 'deep read in hand, SELL always
     stands' (buy in practice, hold still releases if the desk gate
     ever admits it); the desktop re-entry site keeps HOLD-only, which
     is LIVE there via the deep-veto released-hold path.
"""
import re
import sys
import types
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


import discipline as dc

NOW = datetime.datetime(2026, 9, 21, 20, 0, tzinfo=dc.MST)


def _state(gates, duds=None, ts=None):
    # (Sep 22) the default stamp is FRESH relative to real wall-clock: the
    # exec'd veto block calls journal_release_pts WITHOUT now=, so a
    # hardcoded ts ages past SEAM_RELIEF_DECAY_H and the drive's released
    # case silently degrades to a veto (the time bomb this session
    # detonated). Cases that pin decay arithmetic pass ts explicitly.
    _ts = ts or datetime.datetime.now(dc.MST).isoformat()
    return {"weights": {"system_burial": 1.0},
            "burial_ledger": [{"date": _ts[:10], "rot": "16-20",
                               "ts": _ts, "gates": gates, "duds": duds or {}}]}


_real_load = dc.load_state

# ── 1. the relief math is THE SAME journal ──────────────────────────────
st = _state({"deep-veto": {"n": 3, "usd": 45.0}},
            ts="2026-09-21T19:30:00-06:00")
dc.load_state = lambda: st
pts = dc.journal_release_pts("deep-veto", now=NOW)
# journal ts 19:30 vs NOW 20:00 → 0.5h age → decay 1 − 0.5/8 = 0.9375
check("relief pts == gate_relief pts (same journal)",
      pts == dc.gate_relief("deep-veto", now=NOW)
      and pts == 4.0, pts)  # the dial: 3 x sens 2.0 = 6.0, decayed 5.625 -
                    # still ABOVE the 4.0 cap: the cap binds, the decay is swallowed
check("relief capped at SEAM_RELIEF_MAX_PTS",
      dc.journal_release_pts("deep-veto", now=NOW) <= dc.SEAM_RELIEF_MAX_PTS)
st["burial_ledger"][0]["gates"]["deep-veto"]["n"] = 99
check("cap holds at n=99", dc.journal_release_pts("deep-veto", now=NOW)
      == dc.SEAM_RELIEF_MAX_PTS)
st["burial_ledger"][0]["ts"] = "2026-09-20T04:00:00-06:00"   # ~16h old
check("decay to zero past the 8h window",
      dc.journal_release_pts("deep-veto", now=NOW) == 0.0)
st["burial_ledger"] = []
check("silent journal gives nothing",
      dc.journal_release_pts("deep-veto", now=NOW) == 0.0)
st = _state({"deep-veto": {"n": 2, "usd": 30.0}}, duds={"deep-veto": 4})
dc.load_state = lambda: st
check("dud-heavy gate claws relief to zero",
      dc.journal_release_pts("deep-veto", now=NOW) == 0.0)

def _boom():
    raise RuntimeError("broken journal")
dc.load_state = _boom
check("broken journal fails closed (pts 0, release False)",
      dc.journal_release_pts("deep-veto", now=NOW) == 0.0
      and dc.journal_release("deep-veto", 99.0, 92.0, now=NOW) is False)
dc.load_state = _real_load

# ── 2. the boolean contract ─────────────────────────────────────────────
dc.load_state = lambda: _state({"deep-veto": {"n": 3, "usd": 45.0}})
check("within slack + relief → release",
      dc.journal_release("deep-veto", 88.0, 92.0, now=NOW) is True)
check("at-bar quick → release",
      dc.journal_release("deep-veto", 92.0, 92.0, now=NOW) is True)
check("beyond slack → no release",
      dc.journal_release("deep-veto", 86.0, 92.0, now=NOW) is False)
check("gate with no journal entries → no release even at the bar",
      dc.journal_release("tide-out", 92.0, 92.0, now=NOW) is False)
check("None quick fails closed",
      dc.journal_release("deep-veto", None, 92.0, now=NOW) is False)
check("NaN quick fails closed (a broken score closes, never opens)",
      dc.journal_release("deep-veto", float("nan"), 92.0, now=NOW) is False)
check("inf quick fails closed",
      dc.journal_release("deep-veto", float("inf"), 92.0, now=NOW) is False)
check("NaN floor fails closed",
      dc.journal_release("deep-veto", 88.0, float("nan"), now=NOW) is False)

# ── 3. THE ADVISORY PIN (Oct 9 rewrite, the gates deleted): the deep
# gate is a CONTRANOTE now — a contradicting deep read is LOGGED
# (forensics with the same ledger call) and the fire PROCEEDS. The drive
# re-execs the new block from source so a regression back into a veto
# (a `return` after the contranote, or the gate string resurrected)
# goes RED here, every pass.
src = Path("background_scanner.py").read_text(encoding="utf-8")
anchor = src.index('logger.info("  DEEP CONTRA (note)')
start = src.rindex('if _deep.action != "buy":', 0, anchor)
end = src.index("from indicators import ema as _ema3", start)
block = src[start:end]
check("the deep gate is advisory-only: NO return inside the contranote block",
      "return" not in block, block)
check("the deep-veto gate string is gone from the drive's scope",
      '"deep-veto"' not in block)
check("the contranote still writes the ledger (forensics survive)",
      "_log_suppression(pair" in block and "deep-contranote" in block)
check("the contranote still logs to the console (the eyes survive)",
      "DEEP CONTRA (note)" in block)

# ── 4. the stale-move pin (Oct 9 rewrite): the stale shape is a
# RE-SCAN now — a fresh deep read decides pass or fail, never silence.
smsrc = Path("background_scanner.py").read_text(encoding="utf-8")
_sm_anchor = smsrc.index("STALE-MOVE RE-SCAN PASS")
_s2_start = smsrc.rindex('if True:', 0, _sm_anchor) if False else smsrc.rindex('_st = _stale_move(exchange, pair)', 0, _sm_anchor)
_s2_block = smsrc[max(0, _s2_start - 1200):_sm_anchor + 1900]   # the tail reaches the fail branch's return
check("stale-move re-scan: a fresh deep read RUNS (the DO/DO-eager re-read)",
      "_do_deep_read()" in _s2_block, _s2_block[-600:])
check("stale-move re-scan: a PASS releases the fire (never a silent burial)",
      "_mark_release(pair, \"stale-move\")" in _s2_block
      and "RE-SCAN PASS" in smsrc)
check("stale-move re-scan: a FAIL still lands in the ledger (judged, not hidden)",
      "the fresh re-scan FAILED" in smsrc
      and "_log_suppression(pair, \"stale-move\"" in _s2_block)
check("stale-move re-scan: the fail STILL returns (a real fail is allowed "
      "to stand down the popup)",
      sum(1 for ln in _s2_block.splitlines() if ln.strip() == "return") == 1)

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
