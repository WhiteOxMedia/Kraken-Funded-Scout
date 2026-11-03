"""test_kraken_accessor_exclusivity.py — THE ONE-READER/ONE-WRITER
CONTRACT for the KRAKEN_REFERRAL endpoint rows, pinned (Oct 30).

CHARTER: the two non-secret provider rows (KRAKEN_REFERRAL_ENDPOINT,
KRAKEN_REFERRAL_MODE) are owned by the accessor in
pool/kraken_referral.py — kr.endpoint_mode() READS them and
kr.set_endpoint_mode() WRITES them — and NO other production module
touches them, not even by piggybacking on the accessor's own key
constants (PROJECTION_ENDPOINT_KEY / PROJECTION_MODE_KEY). The key
literals and the URL/mode validation rules live in ONE place, so every
surface that renders provider state (the admin panel GET, the admin
POST, setup_kraken.py, provider_posture) can never disagree about what
is provisioned or simulate.

This drive is what CI enforces on every push: the static-invariants
job runs it directly (the one-reader/one-writer rule is a CONTRACT,
not folklore — same shape as test_static_listener_audit.py):

  7.1  no production module outside pool/kraken_referral.py performs a
       literal-key get_setting/set_setting on either row, and none
       uses kr's PROJECTION_(ENDPOINT|MODE)_KEY constants;
  7.2  INSIDE pool/kraken_referral.py itself, the rows are touched ONLY
       inside endpoint_mode (the read) and set_endpoint_mode (the
       write) — AST-verified over every function that names a row
       argument;
  7.3  RED-FIRST: a probe copy of pool/server.py holding one raw row
       read and one piggybacked key constant must catch BOTH — the
       detector catches the exact bypass shapes it claims to.

Test drives legitimately seed raw fixture state, so files whose name
starts with test_/conftest/_/setup are excluded from the scan — the
contract is about production code. A violation fails the CI job;
recording a deliberate exception means editing THIS drive (or
pool/kraken_referral.py, if the accessor itself changes shape) — the
decision cannot happen by accident. test_kraken_referral_unit.py's
§7 delegates here, so the local drive and CI exercise the same code.
Prints "RESULT: N passed, M failed".
"""
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


ROW_KEYS = re.compile(r"KRAKEN_REFERRAL_(?:ENDPOINT|MODE)")
ROW_ACCESS = re.compile(
    r"(?:get|set)_setting\s*\(\s*f?['\"]"
    r"KRAKEN_REFERRAL_(?:ENDPOINT|MODE)['\"]")
KEY_CONST = re.compile(r"\bPROJECTION_(?:ENDPOINT|MODE)_KEY\b")
ROW_ARG_NAMES = {"PROJECTION_ENDPOINT_KEY", "PROJECTION_MODE_KEY",
                 "KRAKEN_REFERRAL_ENDPOINT", "KRAKEN_REFERRAL_MODE"}
SCAN_DIRS = ("pool", "scout", "src", "tools", "installer", "relay",
             "xmrjackpot-seo")


def _production_texts(base, is_root=False):
    out = {}
    it = base.glob("*.py") if is_root else base.rglob("*.py")
    for p in it:
        if is_root and p.parent != base:
            continue
        name = p.name
        if name.startswith(("test_", "conftest.", "_", "setup")):
            continue
        out[str(p.relative_to(HERE)).replace(chr(92), "/")] = \
            p.read_text(encoding="utf-8", errors="strict")
    return out


def _direct_row_hits(files):
    # A direct literal-key access: db.get_setting("KRAKEN_REFERRAL_...",
    # ...) or db.set_setting("KRAKEN_REFERRAL_...", ...) in any module
    # outside pool/kraken_referral.py — the bypass this contract forbids.
    return [f"{label}: direct get_setting/set_setting on a KRAKEN_REFERRAL "
            "row (read through kr.endpoint_mode(), write through "
            "kr.set_endpoint_mode())"
            for label, text in files.items()
            for _m in ROW_ACCESS.finditer(text)]


def _const_hits(files):
    # A piggyback read: kr.PROJECTION_ENDPOINT_KEY used from outside —
    # the same row by another route, so the same Not Allowed applies.
    return [f"{label}: uses kr's PROJECTION_(ENDPOINT|MODE)_KEY from "
            "outside the accessor"
            for label, text in files.items() if KEY_CONST.search(text)]


def _row_violations(files):
    return _direct_row_hits(files) + _const_hits(files)


def run(check):
    """Run the three contract checks through the caller's check()
    callback (the numbering stays 7.x to match the delegating drive,
    test_kraken_referral_unit.py §7). Returns nothing; the caller
    aggregates into its own results."""
    prog_files = {}
    for _d in SCAN_DIRS:
        prog_files.update(_production_texts(HERE / _d))
    prog_files.update(_production_texts(HERE, is_root=True))
    prog_files.pop("pool/kraken_referral.py", None)

    v = _row_violations(prog_files)
    check(7.1, not v,
          "no production module outside pool/kraken_referral.py reads or "
          "writes the measured KRAKEN_REFERRAL rows directly"
          + ("; violations: " + "; ".join(v)[:200] if v else ""))

    acc_path = HERE / "pool" / "kraken_referral.py"
    tree = ast.parse(acc_path.read_text(encoding="utf-8"), "kraken_referral.py")
    touched = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for c in ast.walk(node):
                if (isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr in ("get_setting", "set_setting")):
                    for a in c.args:
                        nm = None
                        if isinstance(a, ast.Name):
                            nm = a.id
                        elif (isinstance(a, ast.Constant)
                              and isinstance(a.value, str)):
                            nm = a.value
                        if nm in ROW_ARG_NAMES:
                            touched.setdefault(node.name, set()).add(nm)
    check(7.2, set(touched) == {"endpoint_mode", "set_endpoint_mode"},
          "inside the accessor itself, the rows are touched ONLY inside "
          "endpoint_mode (the read) and set_endpoint_mode (the write)"
          + ("; functions seen touching the rows: " + str(sorted(touched))
             if set(touched) != {"endpoint_mode", "set_endpoint_mode"}
             else ""))

    # RED-FIRST: a probe copy of pool/server.py holding one raw row read
    # and one piggybacked key constant must catch both, not either.
    probe = dict(prog_files)
    probe["pool/server.py (probe copy)"] = (prog_files.get("pool/server.py", "")
                                            or "") + (
        "\n_p = db.get_setting(\"KRAKEN_REFERRAL_MODE\", \"\")"
        "\n_q = kr.PROJECTION_ENDPOINT_KEY\n")
    c = _row_violations(probe)
    check(7.3, len(c) == 2,
          "RED-FIRST: a raw read AND a piggybacked const in a probe copy of "
          "pool/server.py are both caught"
          + ("; caught: " + "; ".join(c)[:200] if len(c) != 2 else ""))


if __name__ == "__main__":
    print("== 7. THE ACCESSOR IS THE ONLY READER (static contract) ==")
    run(check)
    passed, failed = results.count(True), results.count(False)
    print(f"RESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
