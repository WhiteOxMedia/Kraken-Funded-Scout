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
      and abs(pts - 3.0 * (1 - 0.5 / dc.SEAM_RELIEF_DECAY_H)) < 0.01, pts)
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

# ── 3. the desktop veto block, exec'd from source ───────────────────────
src = Path("background_scanner.py").read_text(encoding="utf-8")
anchor = src.index('logger.info("  DEEP VETO (desktop)')
start = src.rindex('if _deep.action != "buy":', 0, anchor)
end = src.index("\n                            return", start) \
    + len("\n                            return")
block = src[start:end]
base_indent = len(block) - len(block.lstrip(" "))   # 20 spaces
dedented = "\n".join(
    ln[base_indent:] if ln.strip() else ln
    for ln in block.splitlines())
NS = {"logger": types.SimpleNamespace(
        info=lambda *a, **k: NS["logs"].append(" ".join(str(x) for x in a)),
        debug=lambda *a, **k: None),
      "_log_suppression": lambda pair, gate, detail, **kw:
          NS["supp"].append((pair, gate, kw.get("quick"))),
      "_mark_release": lambda pair, gate: NS["marks"].append((pair, gate)),
      "DESKTOP_ALERT_MIN": 92.0,
      "_df_state": "FLAT", "_df_pct": 61.0, "_rot_now_key": "16-20",
      "pair": "X/USD", "asset": "X", "logs": [], "supp": [], "marks": []}

class _Sig:
    confidence = 88.0

class _Deep:
    def __init__(self, action, conf):
        self.action, self.confidence = action, conf

exec(compile("def _veto(_deep, signal):\n"
             + "\n".join("    " + ln for ln in dedented.splitlines()),
             "scanner_block", "exec"), NS)

NS["logs"].clear(); NS["supp"].clear()
NS["_veto"](_Deep("sell", 80.0), _Sig())
check("SELL always stands — no release, suppression written",
      not any("JOURNAL RELEASE" in l for l in NS["logs"])
      and len(NS["supp"]) == 1 and NS["supp"][0][1] == "deep-veto")

NS["logs"].clear(); NS["supp"].clear(); NS["marks"].clear()
NS["_veto"](_Deep("hold", 70.0), _Sig())
check("HOLD + champion journal + quick within slack → released",
      any("JOURNAL RELEASE" in l for l in NS["logs"])
      and not NS["supp"], NS["supp"])
check("the release stamps the pair's provenance (gate recorded)",
      NS["marks"] == [("X/USD", "deep-veto")], NS["marks"])

NS["logs"].clear(); NS["supp"].clear()
NS["SIG"] = 80.0
NS["_veto"](_Deep("hold", 70.0), type("S", (), {"confidence": 80.0})())
check("HOLD + journal but quick beyond slack → veto stands",
      len(NS["supp"]) == 1 and not any("JOURNAL RELEASE" in l
                                       for l in NS["logs"]))

dc.load_state = lambda: {"weights": {}, "burial_ledger": []}
NS["logs"].clear(); NS["supp"].clear()
NS["_veto"](_Deep("hold", 70.0), _Sig())
check("HOLD + silent journal → veto stands (fail-closed)",
      len(NS["supp"]) == 1)
dc.load_state = _real_load

# ── 4. the mover gate's condition, truth-tabled from source ─────────────
msrc = Path("background_scanner.py").read_text(encoding="utf-8")
mstart = msrc.index('if (_deep_sig.action == "sell"')
mend = msrc.index("< _el_mv)):", mstart) + len("< _el_mv)):")
cond = msrc[mstart + len("if "):mend - 1]   # strip the trailing ':'
assert cond.count("(") == cond.count(")"), "condition slice unbalanced"
mns = {"_es_mv": 1.0, "_el_mv": 2.0}
class _IL:
    def __init__(self, v): self.v = v
    def __getitem__(self, i): return self.v
class _Series:
    def __init__(self, v): self.iloc = _IL(v)
class _DF:
    def __init__(self, close):
        self._c = close
    def __getitem__(self, k): return _Series(self._c)
mns["df"] = _DF(3.0)   # close 3.0 ABOVE the long EMA 2.0: the trend clause
                        # is neutralized so the table tests the deep-read terms
exec(compile("def _cond(_deep_sig, _m_release, _dc_r):\n    return ("
             + cond + ")", "mover_cond", "exec"), mns)
_mvr = (   # deep_sig, release, veto_expected
    (_Deep("buy", 70.0), False, False),
    (_Deep("sell", 70.0), True, True),      # SELL buries even with release
    (_Deep("hold", 70.0), False, True),     # no journal → veto
    (_Deep("hold", 58.0), True, False),     # near-bar hold released
    (_Deep("hold", 52.0), True, True),      # beyond slack → veto
    (_Deep("hold", 70.0), True, False),     # hold above the bar + journal
                                            # → the release's whole point
)
for deep, rel, want in _mvr:
    got = mns["_cond"](deep, rel, dc)
    check(f"mover gate: {deep.action}/{deep.confidence:.0f}"
          f"{'+journal' if rel else ''} → "
          f"{'veto' if want else 'pass'}", got == want)
check("downward trend still vetoes a buy",
      mns["_cond"](_Deep("buy", 70.0), False, dc) is False)
mns2 = {"_es_mv": 1.0, "_el_mv": 2.0, "_dc_r": dc, "_m_release": False,
        "df": _DF(1.5)}   # short EMA 1.0 BELOW long 2.0, close 1.5 under
                          # the long: the exact down-trend knife
exec(compile("def _cond2(_deep_sig):\n    return (" + cond + ")",
             "mover_cond2", "exec"), mns2)
check("EMA-down + close below EMA vetoes a buy (the trend knife)",
      mns2["_cond2"](_Deep("buy", 70.0)) is True)

# ── 5. the sure-thing veto block, exec'd from source (Sep 22) ───────────
# the same release, on the sure-thing's own gate name: hold edge only,
# sell always stands, slack vs the popup floor, relief read from THIS
# gate's own burials (sure-thing-deep-veto keeps its own ledger — the
# desktop pool must not open this gate).
ssrc = Path("background_scanner.py").read_text(encoding="utf-8")
_s_anchor = ssrc.index('if _st_deep.action != "buy":')
# the block's continue lives inside the else-branch (24 spaces) since the
# release split the old flat veto into released/else
_s_end = ssrc.index("\n                        continue", _s_anchor) \
    + len("\n                        continue")
_s_block = ssrc[_s_anchor:_s_end]
_s_indent = len(_s_block) - len(_s_block.lstrip(" "))   # 16 spaces
# the block's trailing `continue` is loop-bound in situ; at this
# extraction boundary `return` is its exact equivalent (both abandon the
# candidate) and the only legal form inside the exec'd function
_s_block = _s_block.replace("continue", "return", 1)
_s_ded = "\n".join(ln[_s_indent:] if ln.strip() else ln
                   for ln in _s_block.splitlines())

class _R:  # the coil result: .get("score")
    def __init__(self, score): self._s = score
    def get(self, k, d=None): return self._s if k == "score" else d


SNS = {"logger": types.SimpleNamespace(
           info=lambda *a, **k: SNS["logs"].append(
               " ".join(str(x) for x in a)),
           debug=lambda *a, **k: None),
       "_log_suppression": lambda pair, gate, detail, **kw:
           SNS["supp"].append((pair, gate)),
       "_mark_release": lambda pair, gate:
           SNS["marks"].append((pair, gate)),
       "DESKTOP_ALERT_MIN": 92.0,
       "asset": "X", "pair": "X/USD",
       "_now_mst": datetime.datetime.now,   # rot key only
       "rotation_key_for_hour": lambda h: "0-4",
       "logs": [], "supp": [], "marks": []}

exec(compile("def _st_veto(_st_deep, r):\n"
             + "\n".join("    " + ln for ln in _s_ded.splitlines()),
             "surething_block", "exec"), SNS)

def _st_state(gates):
    return {"weights": {"system_burial": 1.0},
            "burial_ledger": [{"date": datetime.datetime.now(dc.MST)
                               .isoformat()[:10], "rot": "16-20",
                               "ts": datetime.datetime.now(dc.MST).isoformat(),
                               "gates": gates, "duds": {}}]}

# SELL always stands — even with a champion journal
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
dc.load_state = lambda: _st_state({"sure-thing-deep-veto": {"n": 3, "usd": 45.0}})
SNS["_st_veto"](_Deep("sell", 80.0), _R(88.0))
check("sure-thing: SELL always stands — no release even with the journal",
      len(SNS["supp"]) == 1 and SNS["supp"][0][1] == "sure-thing-deep-veto"
      and not any("JOURNAL RELEASE" in l for l in SNS["logs"])
      and not SNS["marks"], SNS["marks"])

# HOLD + own-gate journal + within slack → released, provenance marked
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
SNS["_st_veto"](_Deep("hold", 70.0), _R(88.0))
check("sure-thing: HOLD + own-gate journal + within slack → released",
      any("JOURNAL RELEASE" in l for l in SNS["logs"])
      and not SNS["supp"], SNS["supp"])
check("sure-thing: the release stamps its own gate's provenance",
      SNS["marks"] == [("X/USD", "sure-thing-deep-veto")], SNS["marks"])

# beyond slack → veto stands
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
SNS["_st_veto"](_Deep("hold", 70.0), _R(80.0))
check("sure-thing: HOLD + journal but beyond slack → veto stands",
      len(SNS["supp"]) == 1 and not SNS["marks"])

# the own-ledger proof: the DESKTOP pool's relief must not open this gate
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
dc.load_state = lambda: _st_state({"deep-veto": {"n": 3, "usd": 45.0}})
SNS["_st_veto"](_Deep("hold", 70.0), _R(88.0))
check("sure-thing: the desktop gate's journal does NOT release this gate",
      len(SNS["supp"]) == 1 and not SNS["marks"], SNS["marks"])

# silent journal → fail-closed
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
dc.load_state = lambda: {"weights": {}, "burial_ledger": []}
SNS["_st_veto"](_Deep("hold", 70.0), _R(88.0))
check("sure-thing: silent journal → veto stands (fail-closed)",
      len(SNS["supp"]) == 1)

# broken journal → fail-closed
dc.load_state = _boom
SNS["logs"].clear(); SNS["supp"].clear(); SNS["marks"].clear()
SNS["_st_veto"](_Deep("hold", 70.0), _R(88.0))
check("sure-thing: broken journal → veto stands (fail-closed)",
      len(SNS["supp"]) == 1 and not SNS["marks"])
dc.load_state = _real_load

# the wiring: the fifth mark site and the release log line
check("sure-thing: the release marks provenance at the call site",
      ssrc.count('_mark_release(pair, "sure-thing-deep-veto")') == 1)
check("sure-thing: the release log names the gate and the slack",
      "SURE THING %s: JOURNAL RELEASE" in ssrc
      and '"sure-thing-deep-veto")' in ssrc)

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
