"""THE OPERATOR-OVERRIDE SEAM (Oct 7, user ask: "raise the guard factor
to 1.0 — the system's neutral, healthy operating point"). The review
proved the stored factor does NOT gate popups: guard_factor recomputes
the composite from the learned parts every cycle (disc_mod 0.353 pins
the 0.5 clamp floor — two nightly re-docks, Oct 6 and Oct 7). The
seam: the state file's operator_factor, when present and numeric, IS
the factor (clamped to GUARD_CLAMP), provenance-flagged in the result
and named in every explain() line — while the learned parts keep
computing and reporting underneath, and the nightly self_adjust keeps
owning factor/history from the MACHINE's recompute (respect_override
= False there: the learning record can never be poisoned by the
override). Deleting the key returns control to the machine instantly.

Pins, red-first (pre-cure: guard_factor ignores operator_factor —
the exact "set the file, nothing happens" defect the review found):
  1. the override is the factor, clamped to GUARD_CLAMP both ways
  2. the override wins over the 2-day carry fallback
  3. the parts still compute and ride the result (learning visible)
  4. explain() names the override on every fire line (visible safety)
  5. respect_override=False returns the MACHINE factor (the nightly
     record stays honest)
  6. invalid values fail soft (machine path stands, no flag)
  7. the merge-save never owns the key (it survives every nightly
     write; deleting it is the off switch)
  8. no key -> exactly the old behavior (flag absent)
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import profit_guard as pg

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


_MST = timezone(timedelta(hours=-6))


def _sandbox(state: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="ovr_"))
    (root / "profit_guard_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    pg.ROOT = root
    _ovc = getattr(pg, "_OV_CACHE", None)
    if _ovc is not None:
        _ovc.update(key=None, val=None)
    return root


# 1. the override IS the factor, clamped both ways
_sandbox({"operator_factor": 1.0})
r = pg.guard_factor("XRP/USD")
check("1.1 operator_factor 1.0 -> factor 1.0, provenance-flagged",
      r["factor"] == 1.0 and r.get("operator_override") == 1.0,
      str({k: r.get(k) for k in ("factor", "operator_override")}))
_sandbox({"operator_factor": 9.5})
r = pg.guard_factor("XRP/USD")
check("1.2 operator_factor clamps UP to 2.0 (GUARD_CLAMP)",
      r["factor"] == 2.0 and r.get("operator_override") == 2.0,
      str(r.get("factor")))
_sandbox({"operator_factor": -3})
r = pg.guard_factor("XRP/USD")
check("1.3 operator_factor clamps DOWN to 0.5 (GUARD_CLAMP)",
      r["factor"] == 0.5 and r.get("operator_override") == 0.5,
      str(r.get("factor")))

# 2. the override wins over the 2-day carry fallback
_yest = (datetime.now(_MST) - timedelta(days=1)).date().isoformat()
_sandbox({"operator_factor": 1.0, "factor": 1.3, "adjusted_on": _yest})
r = pg.guard_factor("ZEC/USD")
check("2.1 override beats the carry fallback (1.0, not yesterday's 1.3)",
      r["factor"] == 1.0 and r.get("operator_override") == 1.0,
      str({"factor": r.get("factor"), "carried": r.get("carried_from")}))

# 3. the learned parts still compute and ride the result
check("3.1 the parts still ride the result beside the override",
      r.get("env") is not None or r.get("mlt_corr") is not None
      or r.get("disc_mod") is not None,
      str(sorted(r.keys())))

# 4. explain() names the override (visible safety on every fire line)
line = pg.explain({"readable": True, "factor": 1.0,
                   "operator_override": 1.0}, "XRP/USD")
check("4.1 explain() names OPERATOR OVERRIDE",
      "OPERATOR OVERRIDE 1.00" in line, line)

# 5. the nightly record stays honest: respect_override=False returns
#    the MACHINE factor even with the override set
_sandbox({"operator_factor": 9.9, "factor": 1.3, "adjusted_on": _yest})
r = pg.guard_factor("ZEC/USD", None, respect_override=False)
check("5.1 respect_override=False -> the machine factor (never 9.9, "
      "never the override), no flag",
      r["factor"] != 9.9 and r.get("operator_override") is None
      and r["factor"] <= 2.0,
      str({"factor": r.get("factor"), "ovr": r.get("operator_override")}))
src = (BASE / "profit_guard.py").read_text(encoding="utf-8",
                                           errors="replace")
check("5.2 self_adjust calls with respect_override=False (source pin: "
      "the nightly record can never be poisoned)",
      "respect_override=False" in src)

# 6. invalid values fail soft
for i, bad in enumerate(["high", "", None, [1.0],
                         float("nan"), float("inf"), False, True], start=1):
    _sandbox({"operator_factor": bad})
    r = pg.guard_factor("XRP/USD")
    check(f"6.{i} invalid operator_factor {bad!r} ignored "
          "(machine path stands)",
          r.get("operator_override") is None
          and isinstance(r.get("factor"), float)
          and r["factor"] == r["factor"],
          str(r.get("operator_override")))

# 7. the merge-save never owns the key: a nightly row rides on top,
#    the override survives; deleting the key is the off switch
root = _sandbox({"operator_factor": 1.0, "factor": 0.5})
pg._save({"factor": 2.0, "adjusted_on": "2026-10-08"})
st = json.loads((root / "profit_guard_state.json").read_text())
check("7.1 the nightly write does not clear the override",
      st.get("operator_factor") == 1.0 and st.get("factor") == 2.0,
      str({k: st.get(k) for k in ("operator_factor", "factor")}))
(root / "profit_guard_state.json").write_text(json.dumps({"factor": 2.0}),
                                              encoding="utf-8")
_ovc = getattr(pg, "_OV_CACHE", None)
if _ovc is not None:
    _ovc.update(key=None, val=None)
r = pg.guard_factor("XRP/USD")
check("7.2 deleting the key returns control to the machine (no flag)",
      r.get("operator_override") is None, str(r.get("operator_override")))

# 8. no key -> exactly the old behavior
_sandbox({"factor": 1.3, "adjusted_on": _yest})
r = pg.guard_factor("XRP/USD")
check("8.1 no override key -> no flag, old semantics",
      r.get("operator_override") is None, str(r.get("operator_override")))

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")


def main():
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
