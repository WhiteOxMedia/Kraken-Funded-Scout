"""THE BALANCE POP (Sep 22): the sidebar announces every balance change
with a slow-motion count from the old number to the new one — green
rising, red falling — captioned with the trade that moved it, then a
small colored chip that stays. Pinned here: the pure state machine
(prev→pop decisions, caption attribution, freshness gate, fail-soft),
the animation assets, and the wiring order in the sidebar.

THE SIDEBAR-SWEEP CENSUS (Sep 22, made permanent): every FILE READ in
the sidebar window must ride a live fragment — a read outside one
freezes between full reruns (the KAITO lesson: +$0.77 hit the file at
13:45, the sidebar showed it only on the next touch). The census is
calibrated dynamically (def lines → body extents), so it survives
line drift but not a new stranded read site.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
import scratch_space   # scratch policy: nothing volatile lands in the repo root

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


ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. the pure state machine, exec'd from source (app.py imports
#       Streamlit at module level — the drive never imports it whole) ───
i = ap.find("def _balance_pop_state")
j = ap.find("\ndef main():", i)
check("the pop state machine exists before main()", i > 0 and j > i)
NS = {"__name__": "balancepop"}
exec(ap[i:j], NS)
pop = NS["_balance_pop_state"]

NOW = datetime.now().astimezone()
FRESH = NOW.isoformat()
STALE = (NOW - timedelta(minutes=11)).isoformat()


def fresh_key(pair, amt, ts=FRESH, tag="getout"):
    return (f"{pair}|{ts}|{tag}", amt)


# seed: first render never pops history
r = pop(None, 10021.81, {}, 1.0)
check("first render seeds silently", r["kind"] == "seed" and r["delta"] == 0.0, r)

# the real loss shape: 10028.73 → 10021.81 with the fresh applied trade
r = pop(10028.73, 10021.81, dict([fresh_key("PUMP/USD", -6.92)]), NOW.timestamp())
check("loss pops red with the right delta",
      r["kind"] == "pop" and abs(r["delta"] - (-6.92)) < 1e-9, r)
check("loss caption names the trade + GET OUT", r["caption"] == "PUMP · GET OUT", r["caption"])

# the win shape: same math, green side
r = pop(10012.93, 10028.73, dict([fresh_key("PUMP/USD", 15.80)]), NOW.timestamp())
check("win pops with +delta", r["kind"] == "pop" and r["delta"] > 0, r)
check("win caption names the trade", r["caption"] == "PUMP · GET OUT", r["caption"])

# tag variants: a key without |getout says SOLD
r = pop(10000.0, 10004.74, dict([fresh_key("PUMP/USD", 4.74, tag="")]), NOW.timestamp())
check("untagged applied trade captions SOLD", r["caption"] == "PUMP · SOLD", r["caption"])

# freshness gate: matching amount but 11 minutes old → manual
r = pop(10028.73, 10021.81, dict([fresh_key("PUMP/USD", -6.92, ts=STALE)]), NOW.timestamp())
check("stale matching trade degrades to manual", r["caption"] == "manual", r["caption"])

# amount mismatch: the delta didn't come from the newest trade → manual
r = pop(10028.73, 10021.81, dict([fresh_key("PUMP/USD", -6.00)]), NOW.timestamp())
check("mismatched amount says manual", r["caption"] == "manual", r["caption"])

# fail-soft: broken/absent applied map still pops, never raises
r = pop(10028.73, 10021.81, None, NOW.timestamp())
check("no applied map still pops (manual)", r["kind"] == "pop" and r["caption"] == "manual", r)
r = pop(10028.73, 10021.81, {"garbage-key-no-ts": -6.92}, NOW.timestamp())
check("garbage applied keys fail-soft to manual", r["caption"] == "manual", r)

# float noise: sub-half-cent is not a change
r = pop(10021.81, 10021.81, dict([fresh_key("PUMP/USD", -6.92)]), NOW.timestamp())
check("identical balance → none", r["kind"] == "none", r)
r = pop(10021.81, 10021.812, {}, NOW.timestamp())
check("sub-cent drift → none", r["kind"] == "none", r)

# ── 2. the animation assets: slow motion, both colors, tabular digits ──
check("the count runs in slow motion (2800ms ease-out)",
      "_d=2800" in ap and "1-Math.pow(1-t,3)" in ap)
check("win and loss both styled",
      ".bp-win" in ap and ".bp-loss" in ap and "#3ddc84" in ap and "#ff5c5c" in ap)
check("tabular digits so the count doesn't jiggle", "tabular-nums" in ap)
check("entrance overshoot + persistent glow",
      "cubic-bezier(.2,1.6,.35,1)" in ap and "bpGlowW" in ap and "bpGlowL" in ap)

# ── 3. the sidebar wiring: pop sits between the balance input and the
#       CONTEST ACCOUNT plate, seeds session state, renders once ────────
i_in = ap.find('cfg.challenge_balance = float(_acct["current_balance"])')
i_pop = ap.find("_balance_pop_state(st.session_state.get(\"_bal_pop_prev\")")
i_plate = ap.find('plate("☠ CONTEST ACCOUNT"')
check("pop wired inside main() after the balance input",
      0 < i_in < i_pop, (i_in, i_pop))
check("pop renders above the CONTEST ACCOUNT plate", 0 < i_pop < i_plate, (i_pop, i_plate))
check("session seeds prev every rerun (no repeat pops)",
      'st.session_state["_bal_pop_prev"] = _bp_cur' in ap)
check("pop persists as data and renders animated once",
      '"born": time.time(), "rendered": False' in ap and '_pop["rendered"] = True' in ap)
check("static colored chip after the animation", "border-left:3px solid" in ap)
check("caption rides the pop html", '_pop["caption"]' in ap)
check("components iframe sized for the sidebar",
      "height=122, scrolling=False" in ap)
check("fail-soft wrapper so a pop bug can never break the sidebar",
      "balance tick skipped" in ap)

# ── 4. THE SIDEBAR-SWEEP CENSUS (the Sep 22 sweep, permanent): the
#       sidebar window (main() → the header nameplate) must contain
#       exactly the calibrated read surface — every DISPLAY-class file
#       read inside a live fragment, widget-default loads allowed.
#       A new read site outside a fragment strands and freezes between
#       full reruns — the drive fails with the site named. ────────────
_main_i = ap.find("def main():")
_hdr_i = ap.find("# ── header nameplate")
check("the sidebar window anchors exist", 0 < _main_i < _hdr_i, (_main_i, _hdr_i))
_L = ap.splitlines()
import re as _re
_PATS = [(r"\.read_text\(", "read_text"), (r"\.read_bytes\(", "read_bytes"),
         (r"json\.loads?\(", "json.loads"), (r"(?<![A-Za-z_])open\(", "open"),
         (r"_ca\.load_state\(", "load_state"), (r"_load_console_state\(", "console_state")]
_SITES = []
for _n in range(ap[:_main_i].count("\n"), ap[:_hdr_i].count("\n")):
    _txt = _L[_n] if _n < len(_L) else ""
    for _p, _nm in _PATS:
        if _re.search(_p, _txt):
            _SITES.append((_n + 1, _nm, _txt.strip())); break
check("exactly four file-read sites in the sidebar window", len(_SITES) == 4, _SITES)

# the two ALLOWED reads: widget defaults — the tier select and the
# balance input are user INPUTS; their value must track the file (the
# Sep 9 no-explicit-key lesson: pinning it silently UNDOES booked PnL).
# Verified empirically against HEAD (4 sites: lines 8037/8040 widget
# feeds, 8095 in _balance_tick, 8212 in _brain_count_tick).


def _body_end(def_ln):
    """First non-blank line at indent <= 4 after a def closes its body."""
    for _n in range(def_ln, min(def_ln + 400, len(_L))):
        _t = _L[_n]
        if _t.strip() and (len(_t) - len(_t.lstrip())) <= 4:
            return _n + 1
    return len(_L)


_BT0 = ap.find("def _balance_tick():")
_BC0 = ap.find("def _brain_count_tick():")
check("balance tick fragment exists", _BT0 > 0)
check("brain count fragment exists", _BC0 > 0)
_BT0 = ap[:_BT0].count("\n") + 1 if _BT0 > 0 else 0
_BC0 = ap[:_BC0].count("\n") + 1 if _BC0 > 0 else 0
_BODIES = [(_BT0, _body_end(_BT0), "_balance_tick"), (_BC0, _body_end(_BC0), "_brain_count_tick")]
_DISP = sorted(s for s in _SITES if any(b[0] < s[0] < b[1] for b in _BODIES))
_STRAND = [s for s in _SITES if s not in _DISP
           and not (s[1] in ("load_state", "console_state") and s[0] < _BT0)]
check("every display-class read rides a live fragment (nothing stranded)",
      len(_STRAND) == 0, _STRAND)
check("the account read rides _balance_tick (15s re-read)",
      len(_DISP) == 2 and _DISP and _DISP[0][1] == "load_state"
      and _DISP[0][0] > _BT0, _DISP[:1])
check("the snapshot count rides _brain_count_tick (15s re-read)",
      len(_DISP) == 2 and _DISP[1][1] == "open" and _DISP[1][0] > _BC0, _DISP[1:])
check("the widget-default loads sit before the fragments (tier + settings feeds)",
      len([s for s in _SITES if s[1] in ("load_state", "console_state")
           and s[0] < _BT0]) == 2,
      [s for s in _SITES if s[1] in ("load_state", "console_state") and s[0] < _BT0])
check("both live fragments re-read every 15s",
      "st.fragment(run_every=15)(_balance_tick)" in ap
      and "@st.fragment(run_every=15)\n    def _brain_count_tick():" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_balancepop_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))   # the verdict rides STDOUT — the gate's result
                           # mode reads it there (DONE-only fails closed)
raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): the verdict printed, the exit never rode it
