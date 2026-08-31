"""Test ONE PICK VOICE (user rule, Aug 31: "the bot should be silent — I
shouldn't be hearing two different signals?"). Two contest-side mouths
were calling picks — the console card ("New pick! X!", no bar, every new
headline) and the scanner's champion alert ("Buy X! N% sure!", the full
gate stack). Today's record: ARB (the one correct call) came from the
champion alert; JUP/HYPE-dup/POPCAT were card calls = noise. Fix: the
card shows but doesn't speak; the champion alert stays quiet for pairs
the bot already holds (one coin, one call)."""
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

# ── 1. the card's pick voice is retired (shows, doesn't speak) ────────
check("CARD_PICK_VOICE_ENABLED flag exists", "CARD_PICK_VOICE_ENABLED" in ap)
check("the card's voice is OFF",
      "CARD_PICK_VOICE_ENABLED = False" in ap)
check("the card's _speak is gated behind the flag",
      0 < ap.find("if CARD_PICK_VOICE_ENABLED:")
      < ap.find('_speak(f"New pick! {h[\'asset\']}!'))
check("the card still SHOWS the pick (plate + check button intact)",
      "⚡ THE PICK" in ap and "the final yes/no" in ap)
check("the card's voice code is kept (reversible — don't delete anything)",
      "_size_say" in ap and "Big book — all {_balv:,.0f} dollars in fine!" in ap)

# ── 2. the champion alert stays quiet for bot-held pairs ─────────────
check("desktop alert checks the bridge for held pairs",
      "is_already_holding as _iah" in sc)
check("held pair -> quiet with a log line (DESKTOP QUIET)",
      "DESKTOP QUIET" in sc and "the bot already holds it" in sc)
check("the held check sits in the desktop policy (after the gates, before the floor)",
      0 < sc.find("Desktop alert policy") < sc.find("DESKTOP QUIET")
      < sc.find('TIDE_RISING_DESKTOP_FLOOR if _df_state == "RISING" else 89'))
check("broken bridge read fails open (the alert proceeds)",
      "broken read → the alert proceeds" in sc)

# ── 3. the one voice that remains is the champion alert ──────────────
check("the champion alert voice is intact (the ONE pick voice)",
      'title = f"🟢 BUY {asset} — {_dc:.0f}% SURE"' in sc)
check("the QC still speaks when the user asks (their action, not a signal)",
      "Go! " in ap or "I count the twenties" in ap)
check("the bot itself stays log-silent (no push/voice from the bot repo)",
      "logger.info" in open(
          r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\notifications.py",
          encoding="utf-8").read())

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_onevoice_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
