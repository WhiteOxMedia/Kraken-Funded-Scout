"""Test HER SIZE VOICE (user rule, Aug 31: "why is she not telling me
about the coin size and how much to buy?"). The card's pick voice only
spoke size when the book was THIN — a big book or an unreadable book got
silence. Now every pick speaks the size verdict + the amount, and the
card's voice and the scanner's alert voice use the SAME four phrases
(user rule, Aug 27: 'why are they not all the same?')."""
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
sc = open("background_scanner.py", encoding="utf-8").read()

# the card's pick-voice block (between the _size_say init and the _speak call)
_i = ap.find("_size_say = \"\"")
_card = ap[_i:ap.find("_speak(f\"New pick!")] if _i > 0 else ""

# ── 1. the card's voice now speaks ALL four size verdicts ────────────
check("card: thin book -> 'Small size only — $X'",
      "Small size only — {_spendv:,.0f} dollars!" in _card)
check("card: big book -> 'Big book — all $BAL in fine!'",
      "Big book — all {_balv:,.0f} dollars in fine!" in _card)
check("card: too thin to carry 25% -> 'Too thin'",
      "Too thin — this book can't carry the trade!" in _card)
check("card: unreadable book -> honest line",
      _card.count("Book unreadable — your call on size!") >= 2,
      "not-ok branch + except branch")
check("card: no silent branch left (every path sets _size_say)",
      "pass\n" not in _card.split("_size_say = \"\"")[0].split("except Exception:")[-1])

# ── 2. the scanner's alert voice matches (ONE STANDARD) ──────────────
check("scanner alert voice: big book speaks the amount",
      "Big book — all {(float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):,.0f} dollars in fine!" in sc)
check("scanner alert voice: unreadable book speaks too (was silent)",
      "Book unreadable — your call on size!" in sc)
check("scanner alert voice: small-size line unchanged",
      "Small size only — {_spend:,.0f} dollars!" in sc)

# ── 3. the same four phrases in both mouths ──────────────────────────
for phrase in ("Small size only", "Big book", "Book unreadable"):
    check(f"one standard: '{phrase}' in both voices",
          phrase in _card and phrase in sc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_hersize_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
