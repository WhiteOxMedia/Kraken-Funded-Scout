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
       # THE FLOOR IS LOCAL (Sep 23): the block computes its own popup
       # floor now — the old DESKTOP_ALERT_MIN stub was the leak's
       # fingerprint (a name production never binds in that scope). The
       # stub keeps the slack math identical to the old 92.0.
       "popup_floor": lambda tide_state=None, lw_pts=0, rot_key=None,
                      pair=None: 92.0,
       "popup_floor_for_rotation": lambda rot_key=None, now=None: 92.0,
       "asset": "X", "pair": "X/USD",
       "_now_mst": datetime.datetime.now,   # rot key only
       "rotation_key_for_hour": lambda h: "0-4",
       "logs": [], "supp": [], "marks": []}

exec(compile("def _st_veto(_st_deep, r):\n"
             + "\n".join("    " + ln for ln in _s_ded.splitlines()),
             "surething_block", "exec"), SNS)
check("the sure-thing release's floor is LOCAL (the exec'd block carries no "
      "champion-path leak)",
      "DESKTOP_ALERT_MIN" not in _s_block)

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

# ── 6. the stale-move release, exec'd from source (Oct 8) ───────────────
# The STRANDED gate: the journal has carried stale-move relief since the
# release-fuel strip shipped, but no gate consumed it — app.py rendered
# the pts display-only while both wiggle-trap sites hard-returned on the
# APT lesson. Both sites now spend the fuel in the deep-veto shape:
# HOLD-only deep gate (a SELL always stands; no deep read in hand
# releases nothing — the release never buys a brain read), quick within
# SEAM_RELEASE_SLACK of the floor, relief from THIS gate's own burials,
# fail-closed on a broken journal. RED-FIRST: this section ran RED
# against the unwired guard before the wiring landed.
try:
    smsrc = Path("background_scanner.py").read_text(encoding="utf-8")

    # ── the re-entry site: the whole guard block, try: through except ──
    _sm_anchor = smsrc.index("_st = _stale_move(exchange, pair)")
    _sm_start = smsrc.rindex("try:", 0, _sm_anchor)
    _sm_start = smsrc.rindex("\n", 0, _sm_start) + 1   # the line's own head:
                            # rindex alone lands mid-line and the dedent
                            # base would miss the leading indent
    _sm_end = smsrc.index("pair, exc)", _sm_anchor) + len("pair, exc)")
    _sm_block = smsrc[_sm_start:_sm_end]
    _sm_ind = len(_sm_block) - len(_sm_block.lstrip(" "))
    _sm_ded = "\n".join(ln[_sm_ind:] if ln.strip() else ln
                        for ln in _sm_block.splitlines())

    SMNS = {"logger": types.SimpleNamespace(
                info=lambda *a, **k: SMNS["logs"].append(
                    " ".join(str(x) for x in a)),
                warning=lambda *a, **k: SMNS["logs"].append(
                    "WARN " + " ".join(str(x) for x in a))),
            "_log_suppression": lambda pair, gate, detail, **kw:
                SMNS["supp"].append((pair, gate)),
            "_mark_release": lambda pair, gate:
                SMNS["marks"].append((pair, gate)),
            "DESKTOP_ALERT_MIN": 92.0,
            "_df_state": "FLAT", "_df_pct": 61.0, "_rot_now_key": "16-20",
            "exchange": None, "pair": "X/USD", "asset": "X",
            "logs": [], "supp": [], "marks": []}

    exec(compile("def _sm(_stale_move, signal, _deep):\n"
                 + "\n".join("    " + ln for ln in _sm_ded.splitlines()),
                 "stalemove_block", "exec"), SMNS)

    _SM_STALE = (5.0, 12.0)   # 4h high 5h ago, 12% below it
    _mv = lambda ex, pr: _SM_STALE
    _fresh = lambda ex, pr: None

    dc.load_state = lambda: _state({"stale-move": {"n": 3, "usd": 45.0}})
    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, _Sig(), _Deep("hold", 70.0))
    check("re-entry: stale + HOLD deep + champion journal + within slack → released",
          any("JOURNAL RELEASE" in l for l in SMNS["logs"])
          and not SMNS["supp"], SMNS["supp"])
    check("re-entry: the release stamps stale-move provenance",
          SMNS["marks"] == [("X/USD", "stale-move")], SMNS["marks"])

    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, _Sig(), _Deep("sell", 80.0))
    check("re-entry: SELL deep read always stands — no release even with the journal",
          len(SMNS["supp"]) == 1 and SMNS["supp"][0][1] == "stale-move"
          and not SMNS["marks"]
          and not any("JOURNAL RELEASE" in l for l in SMNS["logs"]))

    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, _Sig(), None)
    check("re-entry: no deep read in hand → the trap stands (the release "
          "never buys a brain read)",
          len(SMNS["supp"]) == 1 and not SMNS["marks"])

    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, type("S", (), {"confidence": 86.0})(), _Deep("hold", 70.0))
    check("re-entry: HOLD + journal but quick beyond slack → the trap stands",
          len(SMNS["supp"]) == 1 and not SMNS["marks"])

    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_fresh, _Sig(), _Deep("hold", 70.0))
    check("re-entry: a fresh move (not stale) never touches the release",
          not SMNS["supp"] and not SMNS["marks"]
          and not any("JOURNAL RELEASE" in l for l in SMNS["logs"]))

    dc.load_state = lambda: {"weights": {}, "burial_ledger": []}
    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, _Sig(), _Deep("hold", 70.0))
    check("re-entry: silent journal → the trap stands (fail-closed)",
          len(SMNS["supp"]) == 1 and not SMNS["marks"])

    dc.load_state = _boom
    SMNS["logs"].clear(); SMNS["supp"].clear(); SMNS["marks"].clear()
    SMNS["_sm"](_mv, _Sig(), _Deep("hold", 70.0))
    check("re-entry: broken journal → the trap stands (fail-closed)",
          len(SMNS["supp"]) == 1 and not SMNS["marks"])
    dc.load_state = _real_load

    # ── the mover site: the release decision, exec'd from source ──
    _mv_start = smsrc.index("_m_stale = _stale_move(exchange, pair)")
    _mv_start = smsrc.rindex("\n", 0, _mv_start) + 1   # the line's own head
    _mv_end = smsrc.index("if _m_stale and not _m_sm_rel:", _mv_start)
    _mv_frag = smsrc[_mv_start:_mv_end]
    _mv_ind = len(_mv_frag) - len(_mv_frag.lstrip(" "))
    _mv_ded = "\n".join(ln[_mv_ind:] if ln.strip() else ln
                        for ln in _mv_frag.splitlines())

    MVNS = {"exchange": None, "pair": "X/USD", "asset": "X"}
    exec(compile("def _mv_rel(_stale_move, signal, _deep_sig):\n"
                 + "\n".join("    " + ln for ln in _mv_ded.splitlines())
                 + "\n    return _m_sm_rel",
                 "mover_stalemove_rel", "exec"), MVNS)

    dc.load_state = lambda: _state({"stale-move": {"n": 3, "usd": 45.0}})
    check("mover: stale + the desk gate's buy + journal → release",
          MVNS["_mv_rel"](_mv, _Sig(), _Deep("buy", 70.0)) is True)
    check("mover: SELL always stands — no release even with the journal",
          MVNS["_mv_rel"](_mv, _Sig(), _Deep("sell", 80.0)) is False)
    check("mover: a hold still releases on the desk-blessed gate (unreachable "
          "past the desk gate today — the gate must not silently bury fuel "
          "if the desk gate ever admits it)",
          MVNS["_mv_rel"](_mv, _Sig(), _Deep("hold", 70.0)) is True)
    check("mover: no deep read in hand → no release",
          MVNS["_mv_rel"](_mv, _Sig(), None) is False)
    dc.load_state = lambda: _state({"stale-move": {"n": 3, "usd": 45.0}})
    check("mover: quick beyond the 89 rail + slack → no release",
          MVNS["_mv_rel"](_mv, type("S", (), {"confidence": 83.0})(),
                          _Deep("buy", 70.0)) is False)
    check("mover: a fresh move never releases",
          MVNS["_mv_rel"](_fresh, _Sig(), _Deep("buy", 70.0)) is False)
    dc.load_state = lambda: {"weights": {}, "burial_ledger": []}
    check("mover: silent journal → no release (fail-closed)",
          MVNS["_mv_rel"](_mv, _Sig(), _Deep("buy", 70.0)) is False)
    dc.load_state = _boom
    check("mover: broken journal → no release (fail-closed)",
          MVNS["_mv_rel"](_mv, _Sig(), _Deep("buy", 70.0)) is False)
    dc.load_state = _real_load

    # ── the wiring pins: both sites, the shape, the control flow ──
    check("both sites read the stranded journal (stale-move fuel is spendable)",
          smsrc.count('journal_release_pts("stale-move")') == 2)
    check("both sites mark stale-move provenance on release",
          smsrc.count('_mark_release(pair, "stale-move")') == 2)
    check("re-entry: HOLD-only deep gate in the source",
          '_deep is not None and _deep.action == "hold"' in _sm_block)
    check("re-entry: fail-closed — the release flag resets on a broken journal",
          _sm_block.count("_sm_rel = False") >= 2)
    check("re-entry: the 5-pt slack vs the LIVE popup floor",
          "SEAM_RELEASE_SLACK" in _sm_block
          and "float(DESKTOP_ALERT_MIN)" in _sm_block)
    check("re-entry: the released candidate skips the return — the popup proceeds",
          "if _sm_rel:" in _sm_block
          and sum(1 for ln in _sm_block.splitlines() if ln.strip() == "return") == 1)
    check("mover: the suppression is conditional — a release falls into the popup else",
          "_m_sm_rel = False" in _mv_frag
          and smsrc.count("if _m_stale and not _m_sm_rel:") == 1)
    _mv_supp_end = smsrc.index("rot=rot_key)", _mv_start)
    _mv_else_head = smsrc[_mv_supp_end:smsrc.index("THE GOAL CLOCK", _mv_supp_end)]
    check("mover: the release mark + log sit at the head of the popup else",
          "else:" in _mv_else_head
          and '_mark_release(pair, "stale-move")' in _mv_else_head
          and "JOURNAL RELEASE (mover)" in _mv_else_head)
except ValueError as _ve:
    check("stale-move release: the wiring exists in background_scanner.py",
          False, f"source anchor missing: {_ve}")

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
