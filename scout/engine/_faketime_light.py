"""One-off harness variant (scratch convention): shift time.time ONLY.

The full harness (_faketime_run.py) swaps the datetime class, which
crashes pandas/streamlit-heavy suites on CPython 3.14/Windows (stack
overflow at import, shift-independent). This variant shifts the epoch
alone — enough for every flagged site in the five heavy suites, whose
clock reads are all time.time()-derived; MST/datetime reads in those
suites are verified by dataflow triage instead.
"""
import sys
import time as _real_time_mod

spec, suite = sys.argv[1], sys.argv[2]
shift = 0.0
if spec not in ("0", "base"):
    n = float(spec[1:-1])
    unit = spec[-1].lower()
    shift = n * {"h": 3600.0, "d": 86400.0}[unit]

_ORIG_TIME = _real_time_mod.time
_real_time_mod.time = lambda: _ORIG_TIME() + shift
print(f"### epoch shifted by {spec} (+{shift:.0f}s; time.time only)")

code = open(suite, encoding="utf-8").read()
g = {"__name__": "__main__", "__file__": suite}
try:
    exec(compile(code, suite, "exec"), g)
    rc = 0
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else (0 if e.code in (None, False) else 1)
except Exception:
    import traceback
    traceback.print_exc()
    rc = 2
sys.exit(rc)
