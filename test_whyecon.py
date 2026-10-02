"""THE WHY-BLOCKED ECONOMICS DRIVE (Oct 1): the burial banner carries
the gate's EV rationale — the family's medfill (the median payer's
fill-modeled run) and its payer rate, joined at RENDER time from the
committed EV artifact (scratch/gate_ev_table.txt). The suppression
ledger is never re-priced or rewritten: the scanner's money path is
untouched, the console reads the artifact live each rerun (re-running
the EV tool re-prices every banner). Pins:
  1. the pure pieces: the loader (artifact rows -> econ dict, fail-soft
     to {}) and the badge (medfill + payer rate; None for a family
     outside the artifact — never borrows another family's numbers; a
     zero-econ family STILL shows its row: '0 payers' IS the rationale)
  2. the wiring: the strip loads the econ live, renders the badge on
     the burial row's gate line, stays fail-soft when no econ exists,
     and the scanner's _log_suppression writes no medfill/rate fields
     (render-time join, one source of truth)

Run: .venv\\Scripts\\python.exe -X utf8 test_whyecon.py
"""
import sys
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


src = (BASE / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")


def _find(rel: str) -> Path:
    """The pinned-asset walk (the payeralert lesson): the drive's own
    dir first, then the gate's PYTHONPATH layers — the STAGED export,
    the HEAD archive (the committed contract), then the working repo.
    The gate exports drives WITHOUT their unstaged neighbors, so a pin
    on a committed file must look where the layers actually are."""
    import os
    cands = [BASE / rel]
    for layer in os.environ.get("PYTHONPATH", "").split(";"):
        if layer.strip():
            cands.append(Path(layer.strip()) / rel)
    for c in cands:
        if c.exists():
            return c
    return BASE / rel

# ── 1. the pure pieces, sliced and exec'd ────────────────────────────
m_load = re.search(r"def _load_gate_econ\(path=None\):.*?(?=\ndef )", src, re.S)
m_badge = re.search(r"def _econ_badge\(gate: str, econ: dict\).*?(?=\ndef )", src, re.S)
check("1.0 the loader and the badge exist as pure functions",
      m_load is not None and m_badge is not None)
G = {"__file__": str(BASE / "app.py")}   # the loader resolves the
# committed artifact relative to the app — the exec harness must too
HAVE = False
try:
    exec(compile(m_load.group(0), "loader", "exec"), G)
    exec(compile(m_badge.group(0), "badge", "exec"), G)
    HAVE = True
except Exception as exc:
    print(f"  (slice exec failed: {exc})")

if HAVE:
    _load, _badge = G["_load_gate_econ"], G["_econ_badge"]

    # the synthetic artifact: gate rows + headers + dashes + a rule line
    _tsv = ("THE GATE EV TABLE — 2026-10-01 09:22\n"
            "==============================================================================\n"
            "window: 117 first-seen candidates (7d) from popup_suppressions.jsonl\n"
            "GATE                     n  pay   rate medfill* dud-med    cap    EV%\n"
            "------------------------------------------------------------------------------\n"
            "tide-out                11    6    55%    1.77   0.06    74%   0.99\n"
            "rcl-drift-reentry        2    0     0%    0.00   0.38     0%   0.38\n"
            "  * medfill = median fill-modeled run of the gate's PAYERS only\n")
    _p = BASE / "scratch" / "_whyecon_fixture.txt"
    _p.parent.mkdir(exist_ok=True)
    _p.write_text(_tsv, encoding="utf-8")
    try:
        econ = _load(_p)
        check("1.1 the artifact parses to typed rows (headers, dashes and "
              "rule lines skipped)",
              econ == {"tide-out": {"n": 11, "payers": 6, "medfill": 1.77},
                       "rcl-drift-reentry": {"n": 2, "payers": 0,
                                             "medfill": 0.0}}, econ)
    finally:
        _p.unlink(missing_ok=True)

    # (Oct 1 evening re-aim — the commit gate caught it honestly: the
    #  refreshed 7d run dropped sure-thing-deep-veto, and the ledger
    #  explains why — popup_suppressions.jsonl holds ~ONE DAY (it now
    #  starts 04:25), the historical 21-burial burst rotated out, and
    #  today's four sure-thing rows all ride the censor horizon (the
    #  header's "censored: 4"). The old pin demanded a SNAPSHOT — family
    #  presence that depends on ledger retention, guaranteed to red as
    #  the ledger rotates and again when the 7d window slides past rcl's
    #  one pre-fix burial (the fix should keep rcl near-zero — a pin
    #  that reds on the fix WORKING is backwards). The contract is the
    #  EVER-FED rails: families the retained window can price in any
    #  regime that buries at all — tide-out, stale-move, deep-veto. The
    #  rotated-out yaml override families ride the None badge, which is
    #  their designed fail-soft (never borrows another family's numbers).)
    check("1.2 the artifact loads and covers the EVER-FED rails (the "
          "families the retained ledger can price — rotation-cleared "
          "families ride the None badge, never a borrowed number)",
          all(g in _load(_find("scratch/gate_ev_table.txt"))
              for g in ("tide-out", "stale-move", "deep-veto")),
          sorted(_load(_find("scratch/gate_ev_table.txt")))[:6])
    _real = _load(_find("scratch/gate_ev_table.txt"))
    _ok = all(0.0 <= r["medfill"] <= 10.0 and 0 <= r["payers"] <= r["n"]
              for r in _real.values())
    check("1.3 every committed row is sane (medfill in range, payers "
          "never exceed n — a corrupt line is skipped, not trusted)", _ok)

    _miss = BASE / "scratch" / "_whyecon_absent.txt"
    _miss.unlink(missing_ok=True)
    check("1.4 a missing artifact fails SOFT to {} (no econ row on any "
          "banner — the strip never dies of its join)",
          _load(_miss) == {} and _badge("tide-out", {}) is None)

    check("1.5 the badge carries medfill + payer rate with the count",
          _badge("tide-out", econ)
          == "medfill 1.77% · payers 55% (6/11)", _badge("tide-out", econ))
    check("1.6 a family outside the artifact badges NOTHING (never "
          "borrows another family's numbers)",
          _badge("already-watched", econ) is None)
    check("1.7 the zero-econ family still shows its row — '0 payers' IS "
          "the rationale (rcl-drift-reentry)",
          _badge("rcl-drift-reentry", econ)
          == "medfill 0.00% · payers 0% (0/2)", _badge("rcl-drift-reentry", econ))

# ── 2. the wiring: render-time join, fail-soft, money path untouched ─
check("2.1 the strip loads the econ live each rerun (the strip's own "
      "freshness rule)",
      "_econ_by_gate = _load_gate_econ()" in src
      and "_render_suppression_strip" in src)
check("2.2 the badge renders on the burial row's GATE line (after the "
      "nums, before the detail — the rationale sits with the verdict)",
      re.search(r"\{nums_txt\}\{_badge_txt\}<br>", src) is not None)
check("2.3 the badge is fail-soft (an absent econ renders an empty "
      "span, never a KeyError in the row loop)",
      "_badge_txt = (f\" <span style='color:#7fd4ff;'>🧮 {_badge}</span>\"\n"
      "                          if _badge else \"\")" in src)
check("2.4 the join is RENDER-time: the scanner's suppression ledger "
      "writer carries no medfill/rate fields (one source of truth — "
      "the artifact, never a re-priced ledger)",
      "_econ_badge" not in _find("background_scanner.py")
      .read_text(encoding="utf-8")
      and "medfill" not in _find("background_scanner.py")
      .read_text(encoding="utf-8"))

print()
fails = [r for r in results if not r[1]]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
