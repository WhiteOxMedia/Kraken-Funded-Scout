"""test_no_xmrj_claims.py — NO SHIPPING SURFACE MAY CLAIM AN XMRJ RAIL (Oct 25).

CHARTER: B28 found that the XMRJ treasury exists only as undeployed Anchor
source — no token, no transfer, no unlock instruction, no caller — while the
README and the paywall still implied one. Both were corrected. This drive
keeps them corrected.

It scans every surface a visitor or an operator actually reads: the README,
the rendered templates, the six-language catalogs (VALUES, not their
`download.treasury*` key names), the browser modules, the server's own
docstrings, the installer, the desktop app and the launchers. Anything under
dist/ is a build artifact of these files, not a source of truth, and is not
scanned.

Three rules, all negation-aware — "there is no XMRJ unlock" is the truth and
must stay allowed, while "Unlock with XMRJ" fails:
  1. no line may mention XMRJ and unlocking together,
  2. no banned identifier (`xmrj_treasury`, `xmrj_jackpot`, `xmrj_unlock`,
     `startXmrjUnlock`, `xmrj-unlock`) may appear,
  3. no line may put "treasury" next to money words (jackpot, unlock, fund,
     split, reserve, payout, pot, settle, XMR).
Section 3 feeds fabricated claims through the SAME predicate that scans the
files, so a green run means the detector still bites rather than that it was
quietly disabled.

READ-ONLY ON THE WORKSPACE: nothing here writes, builds or serves.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


# ── the rules ────────────────────────────────────────────────────────────────
BANNED = (
    "xmrj_treasury",
    "xmrj_jackpot",
    "xmrj_unlock",
    "xmrj-unlock",
    "startxmrjunlock",
    "xmrjunlock",
)
MONEY_RE = re.compile(
    r"\b(jackpot|unlock|unlocks|unlocked|unlocking|settle|settles|settled|"
    r"fund|funds|funded|funding|split|splits|reserve|reserves|payout|payouts|"
    r"pot|xmr)\b")
NEGATIONS = (" no ", " not ", " never ", " without ", " isn't ", " doesn't ",
             " none ", "there is no", "no xmrj", "no treasury")


def _clause(line, idx):
    """The sentence fragment holding `idx` — negation is per clause, not per
    line, so "No fee. Treasury funds the jackpot." cannot borrow the "No" from
    its neighbour while "The treasury does not fund the jackpot." stays a
    truthful denial (the negation follows the word it denies)."""
    start, end = 0, len(line)
    for m in re.finditer(r"[.;!?]|—", line):
        if m.end() <= idx:
            start = m.end()
        elif m.start() >= idx:
            end = m.start()
            break
    return " " + line[start:end].lower() + " "


def _negated(line, idx):
    """True when the claim at `idx` is being DENIED within its own clause."""
    return any(n in _clause(line, idx) for n in NEGATIONS)


def _claim_hits(line):
    """Every positive XMRJ/treasury claim on one line of text."""
    low = line.lower()
    hits = []

    # 1. XMRJ + unlocking in the same line, either order ("XMRJ unlock",
    #    "Unlock with XMRJ", "unlock via XMRJ"). The xmrjackpot brand and
    #    its window.XMRJackpot* namespace are not the token, so "xmrj" only
    #    counts when it is not the start of "xmrjackpot".
    _x = re.search(r"xmrj(?!ackpot)", low)
    u = low.find("unlock")
    if _x and u >= 0 and not _negated(line, min(_x.start(), u)):
        hits.append(("xmrj+unlock", line.strip()))

    # 2. the identifiers themselves: a name only code with a rail has.
    for tok in BANNED:
        start = 0
        while True:
            i = low.find(tok, start)
            if i < 0:
                break
            if not _negated(line, i):
                hits.append((tok, line.strip()))
            start = i + len(tok)

    # 3. a treasury money path: the word in a clause that also names the
    #    money it would move. Whole words only — "spot" is not "pot".
    for m in re.finditer(r"\btreasury\b", low):
        if _negated(line, m.start()):
            continue
        if MONEY_RE.search(_clause(line, m.start())):
            hits.append(("treasury", line.strip()))
            break
    return hits


# ── the surfaces ─────────────────────────────────────────────────────────────
def shipping_files():
    files = [HERE / "README.md"] + sorted(HERE.glob("*.bat"))
    files += sorted((HERE / "pool").glob("*.py"))
    files += sorted((HERE / "scout").glob("*.py"))
    files += sorted((HERE / "pool" / "templates").rglob("*.html"))
    files += sorted((HERE / "pool" / "templates").rglob("*.json"))
    for p in sorted((HERE / "pool" / "static").glob("*.js")):
        if p.name.endswith(".test.js") or p.name.startswith("test_"):
            continue
        files.append(p)
    files += sorted((HERE / "installer").glob("*.ps1"))
    files += sorted((HERE / "installer").glob("*.txt"))
    return [f for f in files
            if f.is_file() and f.name != "test_no_xmrj_claims.py"
            and not f.name.startswith("test_")]


def _json_values(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _json_values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _json_values(value)


def text_lines(path):
    """The lines a reader would see, with catalog KEY NAMES removed.

    The six-language catalogs key their money section
    `download.treasuryTitle` / `download.treasuryBody`; those are identifiers,
    not copy, so JSON is scanned by VALUE. Everything else — including HTML
    markup notes and Python/JS comments — is scanned as written, because a
    comment that claims a rail is exactly the defect this drive exists to
    catch (one did: see B28).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            return list(_json_values(json.loads(text)))
        except json.JSONDecodeError:
            return text.splitlines()
    return text.splitlines()


FILES = shipping_files()
violations = []
for path in FILES:
    rel = path.relative_to(HERE).as_posix()
    for line in text_lines(path):
        for rule, snippet in _claim_hits(line):
            violations.append(f"{rel}: [{rule}] {snippet[:140]}")

print("== 1. NO SURFACE CLAIMS AN XMRJ/TREASURY RAIL ==")
xmrj_unlock = [v for v in violations if "[xmrj+unlock]" in v]
check(1.1, not xmrj_unlock,
      "a surface still pairs XMRJ with unlocking: " + " | ".join(xmrj_unlock[:4]))
identifiers = [v for v in violations
               if any(f"[{tok}]" in v for tok in BANNED)]
check(1.2, not identifiers,
      "a banned XMRJ identifier survives: " + " | ".join(identifiers[:4]))
treasury = [v for v in violations if "[treasury]" in v]
check(1.3, not treasury,
      "a surface still routes money through a treasury: " + " | ".join(treasury[:4]))

print("== 2. THE SCAN IS NOT VACUOUS ==")
# A path typo would leave every rule green over an empty set. Name the
# surfaces that must be in it.
names = {p.name for p in FILES}
check(2.1, len(FILES) >= 20
      and {"README.md", "index.html", "en.json", "scout_paywall.js",
           "scout_paywall_wire_patch.js", "app.py"} <= names,
      f"scanned {len(FILES)} files; missing: "
      f"{sorted({'README.md', 'index.html', 'en.json', 'scout_paywall.js', 'scout_paywall_wire_patch.js', 'app.py'} - names)}")
check(2.2, len([p for p in FILES if p.suffix == ".json"]) >= 6,
      "the six-language catalogs are not all in the scan")
check(2.3, len([p for p in FILES if p.suffix == ".js"]) >= 5,
      f"only {len([p for p in FILES if p.suffix == '.js'])} browser modules scanned")

print("== 3. THE DETECTOR BITES (negative controls) ==")
# Same predicate, fabricated claims: each MUST be caught.
_must_catch = [
    ("Unlock with XMRJ and skip the checkout.",
     "xmrj+unlock", "a paywall unlocking with XMRJ"),
    ("XMRJ unlocks settle through the treasury before the pot grows.",
     "xmrj+unlock", "XMRJ unlocks settling into the pot"),
    ("`xmrj_treasury` forwards the convertible share to the jackpot.",
     "xmrj_treasury", "the program named as a live money path"),
    ("window.startXmrjUnlockFlow posts action: \"xmrj_unlock\".",
     "startxmrjunlock", "the removed paywall entry point"),
    ("Funds reach the pot through the treasury split.",
     "treasury", "a treasury split funding the pot"),
]
_missed = []
for text, rule, why in _must_catch:
    got = [r for r, _ in _claim_hits(text)]
    if rule not in got:
        _missed.append(f"{why} -> {got}")
check(3.1, not _missed, "the detector misses a claim: " + " | ".join(_missed))

# And the truthful denials must stay allowed, or the drive would punish the
# only correct sentence.
_allowed = [
    "There is no XMRJ unlock; the programs are undeployed and uncalled.",
    "The checkout is Monero-only and no treasury rail moves XMR into the pot.",
    "Treasury titles are translated in six languages.",
    "The treasury does not fund the jackpot.",
]
_still_flagged = [t for t in _allowed if _claim_hits(t)]
check(3.2, not _still_flagged,
      "a truthful denial is flagged as a claim: " + " | ".join(_still_flagged))

# The catalog plumbing: a KEY named download.treasuryBody is not copy, so it
# must pass; a VALUE that routes money through a treasury must not. Written to
# a temp file, so the workspace is never touched.
with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "en.json"
    _p.write_text(json.dumps({
        "download.treasuryTitle": "Where the money goes",
        "download.treasuryBody": "Scout Pro is the only paid item; its XMR "
                                "is credited to the pot once per order.",
    }), encoding="utf-8")
    _key_hits = [h for line in text_lines(_p) for h in _claim_hits(line)]
    _p.write_text(json.dumps({
        "download.treasuryBody": "Funds reach the pot through the treasury split.",
    }), encoding="utf-8")
    _value_hits = [h for line in text_lines(_p) for h in _claim_hits(line)]
check(3.3, not _key_hits and _value_hits,
      f"catalog scan: key hits {_key_hits}, value hits {_value_hits}")

# A negation in a DIFFERENT sentence must not launder the claim.
_masked = _claim_hits("No fee. Treasury funds the jackpot every order.")
check(3.4, _masked, "a neighbouring 'No' laundered a treasury claim")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
