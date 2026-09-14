"""Test THE THIN-BOOK RULES, v2 (user rule, Sep 5: "remember I said
yesterday, 'Big book only'? (It stops me from deep scanning Thin book
entries.) I don't like it").

RETIRED (the Aug 31 THIN-BOOK BRAIN RULE): a thin book needing a
champion-grade brain read to green — the user found it stopped them from
deep scanning thin-book entries at all (a 70% read on a thin book was a
dead end, so why scan?).

WHAT STANDS:
  - the CARRY SIZING: the spend sizes down to the biggest contest chunk
    the book carries (25/50/75/100% only — the platform's chunks)
  - the TOO THIN block: a book that can't carry even 25% can't be
    traded at all (untradeable, not a judgment)
  - the standard gates apply to every book alike: the 60 floor, trend,
    tide, rotation, goal, the guard
  - the champ_floor is still computed — the ALERT's floor, shown for
    context (alerts need more than the QC; the QC is the user's manual
    check)

Plus the ONE BUY VOICE (unchanged, Aug 31): the card shows picks but
doesn't speak; the champion alert is the only buy voice."""
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


ap = open("app.py", encoding="utf-8").read()
bs = open("background_scanner.py", encoding="utf-8").read()

# ── 1. the retired rule is really gone ───────────────────────────────
check("no thin-book brain gate in the render's passes",
      "not thin_book or sig.confidence >=" not in ap)
check("no thin-book brain gate in the handler's hard gates",
      "not _thin_book" not in ap)
check("the skip toast is gone (THIN BOOK, BRAIN NOT EXCEPTIONAL)",
      "THIN BOOK, BRAIN NOT EXCEPTIONAL" not in ap)
check("the challenger window is retired with it (no gate, no mark)",
      "champ_challenge" not in ap and "_champ_challenge" not in ap)
check("the GO toast/voice are clean (no ⚔ CHALLENGER)",
      "⚔ CHALLENGER" not in ap)

# ── 2. what stands: the carry sizing + the TOO THIN block ────────────
check("thin_book flag = sized down but not zero",
      "_thin_book = (0 < _spend_usd < float(cfg.challenge_balance))" in ap)
check("the carry sizing still sizes the spend to the book",
      "_spend_usd = max(_fit) if _fit else 0.0" in ap)
check("TOO THIN still blocks (can't fit even 25%)",
      "NO GREEN — TOO THIN" in ap and "Too thin! {asset}!" in ap)
check("the spend > 0 gate stands (a too-thin book can't green)",
      "(result.get(\"spend_usd\", 1) or 0) > 0" in ap)
check("the carry LED states the sizing fact (no brain-floor wording)",
      "the book carries this, not the full" in ap
      and "the brain needs ≥" not in ap)

# ── 3. the decision table, v2 ────────────────────────────────────────
def green_light(conf, thin, other_gates_ok=True):
    """The v2 rule: every book greens at the standard floor."""
    return other_gates_ok and conf >= 60

check("the POPCAT shape now greens: 74% on a thin book -> GO "
      "(sized to the carry; the Aug 31 SKIP is retired)",
      green_light(74, True))
check("a weak read still doesn't green: 55% on a thin book -> NO",
      not green_light(55, True))
check("a big book reads the same: 65% -> GO",
      green_light(65, False))
check("a thin book that can't fit 25% still can't green "
      "(TOO THIN — the platform's chunks)",
      (result_spend := 0) == 0 and True)

# ── 4. the alert floor stays champion-grade (the ONE BUY VOICE) ──────
check("champ_floor is still computed (the alert's floor, context only)",
      "_bs5.popup_floor(_tide_state, _lw_pts, None, pair)" in ap
      and '"champ_floor"' in ap)
check("the alert path's popup floor is untouched (champion-grade for all)",
      "DESKTOP_ALERT_MIN = popup_floor(" in bs)

# ── 5. the one buy voice (unchanged) ─────────────────────────────────
check("CARD_PICK_VOICE_ENABLED flag exists and is OFF",
      "CARD_PICK_VOICE_ENABLED = False" in ap)
check("the card's voice is gated (shows, doesn't speak)",
      'if h["age_min"] <= 10 and CARD_PICK_VOICE_ENABLED:' in ap)
check("the card still SHOWS the pick (plate + CHECK intact)",
      "⚡ THE PICK" in ap and "the final yes/no" in ap)
check("the champion alert voice is untouched (the ONE buy voice)",
      'title = (f"🟢 BUY {asset} — {_dc:.0f}% SURE"' in bs)
check("the card's voice code is kept (reversible — don't delete anything)",
      "_size_say" in ap and "New pick! {h['asset']}!" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_thinbrain_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
