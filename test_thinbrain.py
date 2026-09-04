"""Test THE THIN-BOOK BRAIN RULE + THE ONE BUY VOICE (user rule, Aug 31,
sanctioned: "thin book buys in the contest are noisy... less entries but
more money, bigger margins from big coins, high confidence... it still
needs the brain" + "go" on both). Rule 1: a book that can't carry the full
position only gets a green light when the BRAIN clears the champion floor
(89 flat / 78 rising / 55 lone wolf) — else SKIP. Rule 2: the card shows
picks but no longer speaks; the champion alert is the only buy voice."""
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

# ── 1. the champion floor math (mirrors the alert path) ──────────────
def champ_floor(tide, lw_pts=0):
    f = 78.0 if tide == "RISING" else 89.0
    if lw_pts:
        f = max(55.0, f - lw_pts)
    return f


check("FLAT tide -> 89 (the strict floor)", champ_floor("FLAT") == 89.0)
check("RISING tide -> 78", champ_floor("RISING") == 78.0)
check("FLAT + lone wolf 35 -> 55 (ARB's lane)", champ_floor("FLAT", 35) == 55.0)
check("RISING keeps 78 (lone-wolf pts are FLAT-gated upstream — no stack)",
      champ_floor("RISING", 0) == 78.0)

# ── 2. the thin-book decision table ──────────────────────────────────
def green_light(conf, thin, floor):
    if thin:
        return conf >= floor
    return True  # big book: normal rules


check("POPCAT shape: 74% on a thin book, FLAT (floor 89) -> SKIP",
      not green_light(74, True, 89.0))
check("exceptional thin book: 91% on a thin book, FLAT -> GO",
      green_light(91, True, 89.0))
check("ARB shape: 84% lone wolf (floor 55) on a FULL book -> GO (book rules)",
      green_light(84, False, 89.0))
check("a full-carry book never needs the champion floor (60 QC floor rules)",
      green_light(65, False, 89.0))
check("thin book + rising tide floor 78: 80% -> GO",
      green_light(80, True, 78.0))

# ── 3. the wiring in the QC ───────────────────────────────────────────
check("champ floor computed from the scanner's constants (one source of truth)",
      'getattr(_bs5, "TIDE_RISING_DESKTOP_FLOOR", 78.0)' in ap
      and 'getattr(_bs5, "LONE_WOLF_DESKTOP_FLOOR", 55.0)' in ap)
check("thin_book flag = sized down but not zero",
      "_thin_book = (0 < _spend_usd < float(cfg.challenge_balance))" in ap)
check("green-light condition includes the thin-book brain test "
      "(Sep 4: with the challenger window — the QC agrees with the alert)",
      "(not _thin_book\n                             or sig.confidence >= _champ_challenge_min)" in ap)
check("render's passes mirrors the same test (champ_min, not the bare floor)",
      "(not thin_book or sig.confidence >= champ_min)" in ap)
check("skip toast + voice for the non-exceptional thin book",
      "THIN BOOK, BRAIN NOT EXCEPTIONAL" in ap
      and "not exceptional!" in ap)
check("GO voice speaks the brain's number when sized down",
      "The brain says {sig.confidence:.0f} percent!" in ap)
check("NO GREEN line names the thin-book floor",
      "NO GREEN — THIN BOOK, BRAIN NOT EXCEPTIONAL" in ap)
check("carry LED states the brain requirement on thin books",
      "the brain needs ≥{champ_floor:.0f}% here" in ap)
check("result stores thin_book + champ_floor for the render",
      '"thin_book"' in ap and '"champ_floor"' in ap)

# ── 4. the one buy voice ──────────────────────────────────────────────
check("CARD_PICK_VOICE_ENABLED flag exists and is OFF",
      "CARD_PICK_VOICE_ENABLED = False" in ap)
check("the card's voice is gated (shows, doesn't speak)",
      'if h["age_min"] <= 10 and CARD_PICK_VOICE_ENABLED:' in ap)
check("the card still SHOWS the pick (plate + CHECK intact)",
      "⚡ THE PICK" in ap and "the final yes/no" in ap)
check("the champion alert voice is untouched (the ONE buy voice; Sep 4: a "
      "challenger popup appends the mark, the base line stands)",
      'title = (f"🟢 BUY {asset} — {_dc:.0f}% SURE"\n'
      '                     + (" — CHALLENGER" if _challenger else ""))' in open(
          "background_scanner.py", encoding="utf-8").read())
check("the card's voice code is kept (reversible — don't delete anything)",
      "_size_say" in ap and "New pick! {h['asset']}!" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_thinbrain_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
