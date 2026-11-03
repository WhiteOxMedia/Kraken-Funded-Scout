"""test_rate_pin_fleet.py — THE FLEET RATE-PIN SWEEP (Oct 29, extended
Oct 30: the gateway class joined).

rowan's order: "Pin the XMR rate the same hermetic way in the remaining
drives that exercise USD<->XMR conversions" — done drive by drive —
then: make the fixture a CONTRACT, not folklore. This drive reads every
fleet drive (sorted(HERE.glob("test_*.py")), the same discovery rule
run_fleet.py uses, so new drives join automatically) and pins:

  1. EVERY drive in a rate-reaching class must set
     os.environ["XMR_PRICE_LIVE"] = "0" at the TOP LEVEL and BEFORE the
     first reaching import. The classes:
       a) DIRECT importers of jackpot_funding or xmr_price — the two
          rate-bearing surfaces (the funding ledger converts the pot
          USD<->XMR; xmr_price is the Kraken-ticker reader itself).
       b) GATEWAY importers — since the Oct 30 order: any drive that
          imports a pool module which itself imports a rate module.
          The gateway set is DERIVED from pool/*.py (pure text, same
          discovery each run), so today it finds exactly
          {server, kraken_challenge, kraken_referral, miner_yield};
          a drive importing any of them transitively imports the
          reader (server -> jackpot_funding -> lazily xmr_price), and
          a new server-importing drive used to slip through with no
          pin and re-float the same background-refresh flake. Growth
          of the derived set fails the envelope check (3.5), so a new
          pool gateway is a CONSCIOUS sweep edit, never a silent gap.
     That keeps the rate reader on the fallback,
     off the network, and the subprocesses inherit the pin: a background
     Kraken refresh can never dial from inside a drive and move dollar
     assertions (the fixture test_rate_label, test_ui_disclosure and the
     rest of the fleet carry; see their fixture comment for the wording).
  2. THE CLASSIFIED EXCEPTIONS: a drive lands here only by editing this
     drive — the decision cannot happen by accident. Today exactly one:
     the kill switch's own drive (test_xmr_price.py), which stubs
     xmr_price._fetch on every fetch path, never dials, and pins the
     switch precisely BECAUSE it is its subject matter (its section 6).
  3. THE ENVELOPE: every importer (direct or gateway) is accounted for
     (pinned or classified), the gateway table matches the derivation,
     the sweep's discovery matches the fleet's, and the
     sweep itself is HERMETIC BY CONSTRUCTION — pure text analysis: it
     imports no pool module, opens no DB, dials nothing, writes nothing
     (stamps compared before/after).

Direct-import detection recognizes `import jackpot_funding`,
`import xmr_price`, `import x as _x` aliases and `from <m> import ...`;
gateway detection the same shape for the derived pool module names.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

RATE_MODULES = ("jackpot_funding", "xmr_price")
IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?:" + "|".join(RATE_MODULES) + r")\b"
    r"|from\s+(?:" + "|".join(RATE_MODULES) + r")\s+import\b)")

# The table the derivation must MATCH (check 3.5): the pool modules the
# sweep knows to be direct rate importers. A drift — a pool module
# gaining or losing a rate import — fails the sweep loudly and is
# settled by editing this line consciously, like the EXEMPT table.
GATEWAY_TABLE = ("server", "kraken_challenge", "kraken_referral", "miner_yield")


TOP_IMPORT_RE = re.compile(
    r"^(?:import\s+(?:" + "|".join(RATE_MODULES) + r")\b"
    r"|from\s+(?:" + "|".join(RATE_MODULES) + r")\s+import\b)")


def _derive_gateways():
    """Pure text, hermetic: read pool/*.py and return the modules that
    import a rate module at MODULE TOP LEVEL (the set MUST equal
    GATEWAY_TABLE — check 3.5). Top-level only, deliberately: an
    `import gateway` EXECUTES exactly those statements, so that is the
    import-time transitivity the pin must precede. In-function (call-
    time) rate imports are out of this class's reach — the pin guards
    the whole process once it is set, and call-time readers there are
    the fixture discipline's own watch, not the sweep's.
    """
    found = set()
    for path in sorted((HERE / "pool").glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(TOP_IMPORT_RE.match(line) for line in text.splitlines()):
            found.add(path.stem)
    return tuple(sorted(found - set(RATE_MODULES)))


GATEWAYS = _derive_gateways()
GATEWAY_RE = re.compile(
    r"^\s*(?:import\s+(?:" + "|".join(GATEWAYS) + r")\b"
    r"|from\s+(?:" + "|".join(GATEWAYS) + r")\s+import\b)") if GATEWAYS else None
# The sweep sees a drive when EITHER class matches (pin goes before the
# FIRST reaching import, whichever class it is) — the UNION of the two
# regexes, not the gateway list alone.
ALL_REACHED = tuple(RATE_MODULES) + tuple(GATEWAYS)
REACHING_RE = re.compile(
    r"^\s*(?:import\s+(?:" + "|".join(ALL_REACHED) + r")\b"
    r"|from\s+(?:" + "|".join(ALL_REACHED) + r")\s+import\b)")
PIN_RE = re.compile(
    r"^os\.environ\s*\[\s*[\"']XMR_PRICE_LIVE[\"']\s*\]\s*=\s*[\"']0[\"']"
    r"\s*(?:#.*)?$")

# ── THE CLASSIFIED EXCEPTIONS (deliberate — edit consciously) ──
EXEMPT = {
    "test_xmr_price.py":
        "the kill switch's OWN drive: it stubs xmr_price._fetch on every "
        "fetch path, never dials, and the pin IS its subject matter (its "
        "section 6 checks the switch itself, then pops the env)",
}

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def _stamp(path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


drives = sorted(p for p in HERE.glob("test_*.py"))
_self = Path(__file__).name
_drives_before = {p.name: _stamp(p) for p in drives}

print("== 1. EVERY RATE-REACHING DRIVE PINS BEFORE THE IMPORT ==")
pinned, classified = [], []
idx = 0
for path in drives:
    name = path.name
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    imp_line = next((i for i, l in enumerate(lines, 1) if REACHING_RE.match(l)), None)
    if imp_line is None:
        continue
    idx += 1
    pin_line = next((i for i, l in enumerate(lines, 1) if PIN_RE.match(l)), None)
    if name in EXEMPT:
        classified.append(name)
        check(f"1.{idx}", True,
              f"{name}: classified exception — {EXEMPT[name]}")
        continue
    if pin_line is None or pin_line > imp_line:
        order = ("pin ABSENT" if pin_line is None
                 else f"pin at line {pin_line} AFTER the import at {imp_line}")
        check(f"1.{idx}", False,
              f"{name}: {order} — insert os.environ[\"XMR_PRICE_LIVE\"] = "
              f"\"0\" before the first import that reaches the rate "
              f"reader (direct, or a gateway like server; the same "
              f"fixture test_rate_label and test_ui_disclosure carry)")
    else:
        pinned.append(name)
        check(f"1.{idx}", True,
              f"{name} (pin line {pin_line} < first reaching import line {imp_line})")

print("== 2. THE CLASSIFIED EXCEPTIONS ARE CONSCIOUS ==")
for name, reason in EXEMPT.items():
    path = HERE / name
    is_importer = False
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if REACHING_RE.match(line):
                is_importer = True
                break
    check(f"2.{name}", is_importer,
          f"the exception table names {name}, which is no longer a "
          f"rate-reaching importer — drop it from the table (or pin it "
          f"like the rest)")
check("2.table", set(classified) == set(EXEMPT),
      f"table drift: classified {classified} vs table {sorted(EXEMPT)}")

print("== 3. THE ENVELOPE ==")
check("3.1", len(pinned) + len(classified) > 0,
      "the sweep found no rate-bearing drive at all — discovery is broken")
check("3.2", _self not in (pinned + classified),
      "the sweep must not count itself: it is pure text and imports no "
      "pool module")
check("3.3", all((HERE / n).exists() for n in (pinned + classified)),
      "a counted drive vanished mid-run")
check("3.4", 'glob("test_*.py")' in (HERE / "run_fleet.py").read_text(
          encoding="utf-8", errors="replace"),
      "the fleet still discovers by the glob this sweep scans — if "
      "run_fleet.py's discovery rule changes, re-point this contract")
check("3.5", GATEWAYS == tuple(sorted(GATEWAY_TABLE)),
      f"the derived gateway set drifted: {GATEWAYS} vs table "
      f"{sorted(GATEWAY_TABLE)} — a pool module gained or lost a direct "
      "rate import; settle it by editing GATEWAY_TABLE consciously")
check("3.6", "server" in GATEWAYS,
      "the order's anchor is gone: server no longer imports a rate "
      "module — server-importing drives are no longer transitive "
      "importers; settle it in GATEWAY_TABLE consciously")

print("== 4. HERMETIC BY CONSTRUCTION ==")
_drives_after = {p.name: _stamp(p) for p in drives}
check("4.1", _drives_before == _drives_after,
      "the sweep only ever reads — every scanned drive byte-identical "
      "across the run")

print("== 5. THE RATCHET ==")
_total = len(pinned) + len(classified)
check("5.1", _total >= 45,
      f"the ratchet floor (raised Oct 30 when the gateway class joined — "
      "45 was the fleet's first gateway-class count): "
      f"the fleet carries {_total} rate-reaching drives "
      f"({len(pinned)} pinned + {len(classified)} classified); "
      "a drop means a fixture was removed or a gateway drive went unpinned")
total = len(pinned) + len(classified)
print(f"---")
print(f"fleet rate pins: {len(pinned)} pinned before the first reaching "
      f"import, {len(classified)} classified exception(s)")
print(f"RESULT: {sum(results)} passed, {results.count(False)} failed")
sys.exit(1 if any(not r for r in results) else 0)
