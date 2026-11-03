"""Drive of THE POPUP CHARTER (Oct 8, user spec: 'popup logic restored').

THE CHARTER (the spec, section by section, pinned against the LIVE code):
  1. the phone (any push leg) carries ONLY the two actionable
     instructions — BUY ALERT ('Buy [X] now' + the book amount) and
     EXIT ALERT ('Get out now' / 'CLOSE [X]'). Everything else — gate
     releases, calibration, funnel states, tide/stale-move, QC,
     diagnostics, internal logic, warnings, commentary — NEVER reaches
     any phone.
  2. the classification seam is the ONE door: _push_to_phone admits
     BUY/EXIT and refuses everything else (test_pushfilter pins the
     refusal classes; this drive pins the STRUCTURE: every phone-path
     call site rides _push_to_phone or the template-locked charter
     events, and the ops surfaces are console-only).
  3. the pool's machine leg is template-locked: the charter events
     (signal_buy / signal_exit + the jackpot family) are the only
     names push_client.emit may carry — the extras are whitelisted —
     so even an internal caller cannot mint a custom phone message.
  4. the console is READ-ONLY: it renders (the strips, the plates)
     and never sends.

RED-FIRST receipts: the stale-release tripwire's emit rode _send_ntfy
(the phone) at birth — this charter drive ran RED naming it, and the
cure re-planed the emit console-only before the tripwire ever landed.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


def main():
    src = (ROOT / "background_scanner.py").read_text(encoding="utf-8")

    # ── 1. the phone door is ONE function, and its classifier is the law ─
    check("the phone door is _push_to_phone (one choke point)",
          src.count("def _push_to_phone(") == 1)
    _di = src.index("def _push_to_phone(")
    door = src[_di:src.index("\ndef ", _di + 10)]
    check("the door admits only BUY/EXIT (the classifier regexes live "
          "in the door)",
          "_PUSH_SELL_RE.search" in door and "_PUSH_BUY_RE.search" in door)
    check("the door refuses warnings by prefix (the 06:41 lesson)",
          'startswith("warning")' in door)
    check("the door's charter comment names the Aug 30/31 rule",
          "BUY NOW" in door and "Get out now" in door)

    # ── 2. the phone leg's senders: every _send_ntfy call must be a
    #       charter-class surface (the classified trade push, its retry
    #       drain, the canary heartbeat) — never an internal-logic emit
    _calls = [(m.start(), src[max(0, m.start() - 300):m.start()])
              for m in re.finditer(r"_send_ntfy\(", src)
              if src[max(0, m.start() - 4):m.start()] != "def "]
    _allowed_ctx = ("_push_retry_q", "_CANARY_TEXT", "the stale-release "
                    "tripwire is console-only")
    _ops_sites = [ctx for _, ctx in _calls if "ops 1/2" in ctx]
    check("the OPS leg's phone sends are ONLY the aa-box heartbeat "
          "(machinery, operator's own phone — enumerated, not open)",
          len(_ops_sites) <= 2, len(_ops_sites))
    check("the stale-release tripwire carries NO phone send (the "
          "charter's first catch, re-planned console-only)",
          all("_note_release_alert" not in ctx for _, ctx in _calls))

    # ── 3. the tripwire emit: console-only by construction ──────────────
    _nra = src[src.index("def _note_release_alert("):
               src.index("STALE_RELEASE_ALERT_STATE = ")
               if "STALE_RELEASE_ALERT_STATE = " in src[
                   src.index("def _note_release_alert("):]
               else len(src)]
    _nra = src[src.index("def _note_release_alert("):
               src.index("\ndef ", src.index("def _note_release_alert(") + 10)]
    check("the tripwire's default push is the logger (console-only)",
          "logger.info" in _nra and "_send_ntfy" not in _nra)
    check("the tripwire's charter comment names the rule",
          "PUSH CHARTER" in _nra and "BUY ALERT" in _nra)

    # ── 4. the pool machine leg: template-locked events only ────────────
    check("the scanner names charter events, never custom text, on the "
          "pool leg",
          src.count("_charter_push(") - src.count("def _charter_push(") == 1
          and '"signal_exit" if is_sell else "signal_buy"' in src)
    _pc = Path(ROOT.parent / "monero_project" / "scout" / "push_client.py")
    if _pc.exists():
        pcs = _pc.read_text(encoding="utf-8")
        check("push_client's extras are whitelisted (no free-form body "
              "from the machine leg)",
              "_ALLOWED_EXTRAS" in pcs
              and '"amount_xmr"' in pcs)
    _ps = Path(ROOT.parent / "monero_project" / "pool" / "push_service.py")
    if _ps.exists():
        pss = _ps.read_text(encoding="utf-8")
        check("the pool's templates carry the two actionable titles",
              '"📈 BUY NOW"' in pss and '"📉 GET OUT NOW"' in pss)
        check("the pool's machine route is operator-token gated",
              "_operator_or_401" in pss or "X-Operator-Token" in pss)

    # ── 5. the console never sends ──────────────────────────────────────
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    check("app.py (the public console) carries no push leg at all",
          "_send_ntfy" not in app_src and "_push_to_phone" not in app_src
          and "ntfy.sh" not in app_src)

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
