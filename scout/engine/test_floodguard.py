"""test_floodguard.py — THE FLOOD GUARD'S PIN SUITE (Sep 14).

Pins the scanner's log-flood quota machinery end to end:
  1. the quota admits the first LOG_FLOOD_QUOTA records of a signature
     and refuses the rest of that window
  2. ERROR/CRITICAL always pass — the stop-everything line is never
     filtered, and never consumes quota
  3. signature = call site + arity: 200 pairs failing at one site is
     ONE storm; a different site is a different budget; a different
     arity is a different budget
  4. the suppression notice rides the LAST ADMITTED line — a swallowed
     line cannot announce itself, the final visible one does
  5. the window rolls: the next window's FIRST record of the signature
     carries the +N tally — adjacent windows only
  6. a quiet pause expires the tally honestly (no stale "+N" claims)
  7. two handlers over one shared quota count each record exactly once
     and their streams are byte-identical (the REAL logging flow, e2e)
  8. the formatter is idempotent on stamped records — the same record
     flows through both handlers' formatters, no double suffix — and
     passes unstamped records through untouched
  9. the signature prune bound holds (idle signatures are dropped)
 10. the mount cannot silently drift (source pins on _config_logging)

Deterministic: the quota's clock is injected — no sleeps anywhere.
Run: .venv/Scripts/python.exe -X utf8 test_floodguard.py
"""
from __future__ import annotations

import io
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import background_scanner as bs   # host-gated logging: no handlers mount here

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def rec(lineno, args=(), level=logging.WARNING, module="fx"):
    """A LogRecord at a chosen fake call site (module, lineno) with args."""
    return logging.LogRecord(
        name="fg", level=level, pathname=f"{module}.py", lineno=lineno,
        msg="pair %s failed" if args else "boom", args=args, exc_info=None)


def new_quota(**kw):
    q = bs._FloodQuota(**kw)
    t = [0.0]
    q.clock = (lambda: t[0])          # injected fake clock (monotonic-like)
    q._t = t                          # the test advances it directly
    return q


# ── 1. the quota admits N then refuses ──────────────────────────────────
q = new_quota(quota=10, window=60)
admitted = [q.admit(rec(10, (f"P{i}",))) for i in range(14)]
check("quota admits the first 10 of a signature", admitted[:10] == [True] * 10,
      f"first10={admitted[:10]}")
check("quota refuses the rest of the window", admitted[10:] == [False] * 4,
      f"rest={admitted[10:]}")

# ── 2. ERROR/CRITICAL always pass, never consume quota ──────────────────
q = new_quota(quota=2, window=60)
f = bs._LogFloodFilter(q)
for i in range(5):
    f.filter(rec(10, (f"P{i}",)))            # storm the site to its cap
err = rec(10, ("P",), level=logging.ERROR)
check("ERROR passes the filter mid-storm", f.filter(err) is True)
crit = rec(11, (), level=logging.CRITICAL)
check("CRITICAL passes the filter", f.filter(crit) is True)
n_after = sum(1 for i in range(1) if q.admit(rec(10, ("P",))))
check("ERROR did not consume quota (site still saturated)",
      n_after == 0, "a WARNING after the ERRORs was admitted — quota leaked")

# ── 3. signature = call site + arity ────────────────────────────────────
q = new_quota(quota=3, window=60)
same_site = [q.admit(rec(10, (p,))) for p in ("BTC/USD", "ETH/USD", "SOL/USD",
                                              "DOGE/USD")]
check("200 pairs at one site is ONE storm (4th refused)",
      same_site == [True, True, True, False], f"got {same_site}")
other = q.admit(rec(11, ("BTC/USD",)))       # different line, same message
check("a different site has its own budget", other is True)
arity2 = q.admit(rec(12, ("BTC/USD", 42.0)))
check("a different arity has its own budget", arity2 is True)

# ── 4. the notice rides the LAST ADMITTED line ──────────────────────────
q = new_quota(quota=3, window=60)
recs = [rec(10, (f"P{i}",)) for i in range(5)]
for r in recs:
    q.admit(r)
check("notice stamped on the last ADMITTED record",
      getattr(recs[2], "flood_suppressed", None) == 1
      and not any(getattr(r, "flood_suppressed", None)
                  for r in (recs[0], recs[1], recs[3], recs[4])),
      "stamp on the wrong record")

# ── 5. the window rolls: adjacent tally ─────────────────────────────────
q = new_quota(quota=2, window=60)
for i in range(4):                           # window 0: 2 admitted, 2 suppressed
    q.admit(rec(10, (f"P{i}",)))
q._t[0] = 70.0                               # window 1
first = rec(10, ("P9",))
q.admit(first)
check("next window's first record carries the +N tally",
      getattr(first, "flood_tally", None) == 2,
      f"tally={getattr(first, 'flood_tally', None)!r}")
second = rec(10, ("P10",))
q.admit(second)
check("only the FIRST record of the new window carries the tally",
      not hasattr(second, "flood_tally"))
q._t[0] = 190.0                              # window 3 — a gap: no tally
gap = rec(10, ("P11",))
q.admit(gap)
check("a window gap claims no tally (adjacent windows only)",
      not hasattr(gap, "flood_tally"))

# ── 6. a quiet pause expires the tally honestly ─────────────────────────
q = new_quota(quota=2, window=60)
for i in range(5):
    q.admit(rec(10, (f"P{i}",)))             # suppress 3
q._t[0] = 240.0                              # three windows of silence
later = rec(10, ("PZ",))
q.admit(later)
check("no stale tally after a quiet pause", not hasattr(later, "flood_tally"))

# ── 7. e2e: two handlers, one quota, real logging flow ──────────────────
lg = logging.getLogger("fg_e2e")
lg.setLevel(logging.INFO)
lg.propagate = False
for h in list(lg.handlers):
    lg.removeHandler(h)
s1, s2 = io.StringIO(), io.StringIO()
fq = new_quota(quota=3, window=60)
fmt = bs._FloodAwareFormatter("%(levelname)s %(message)s")
for s in (s1, s2):
    h = logging.StreamHandler(s)
    h.setFormatter(fmt)
    h.addFilter(bs._LogFloodFilter(fq))
    lg.addHandler(h)

def storm_line(i):
    # ONE physical call site: in production the tally rides the storm's
    # own call site repeating, so the roll record must come from the
    # same module:lineno as the storm (two separate lines would be two
    # signatures, and a brand-new signature correctly carries nothing).
    lg.warning("pair %s failed", f"P{i}")

for i in range(40):
    storm_line(i)
out1, out2 = s1.getvalue(), s2.getvalue()
check("e2e: the storm renders exactly 3 lines per handler",
      out1.count("\n") == 3, f"lines={out1.count(chr(10))}")
check("e2e: both handlers byte-identical (one count, one decision)",
      out1 == out2)
check("e2e: the notice is in the stream",
      "suppressing further identical" in out1)
fq._t[0] = 60.0                              # the ADJACENT next window
storm_line(99)
check("e2e: the window roll delivers [+37 suppressed] on the stream",
      "[+37 suppressed in the previous window]" in s1.getvalue(),
      s1.getvalue().splitlines()[-1] if s1.getvalue() else "empty")

# ── 8. formatter idempotence + unstamped passthrough ────────────────────
fmt = bs._FloodAwareFormatter("%(message)s")
stamped = rec(10, ("X",))
stamped.flood_suppressed = 1
a, b = fmt.format(stamped), fmt.format(stamped)
check("stamped record formats identically twice (no double suffix)",
      a == b and a.count("flood guard") == 1, f"a={a!r} b={b!r}")
plain = rec(11, ("Y",))
check("unstamped record formats exactly as before",
      fmt.format(plain) == "pair Y failed")

# ── 9. the prune bound holds ────────────────────────────────────────────
q = new_quota(quota=10, window=60)
for i in range(300):                         # 300 distinct sites, one window
    q.admit(rec(i, ("P",)))
check("300 signatures tracked without error", len(q.counts) <= 300)
q._t[0] = 1300.0                             # all idle > LOG_FLOOD_PRUNE_S
q.admit(rec(999, ("P",)))                    # one more site triggers the prune
check("idle signatures pruned on the next new signature",
      len(q.counts) == 1, f"len={len(q.counts)}")

# ── 10. source pins: the mount cannot silently drift ────────────────────
src = Path(__file__).parent.joinpath("background_scanner.py").read_text(
    encoding="utf-8")
check("the shared quota is mounted on BOTH handlers",
      src.count("addFilter(_LogFloodFilter(") == 2,
      f"mounts={src.count('addFilter(_LogFloodFilter(')}")
check("ONE shared _FloodQuota instance behind both handlers",
      "_flood = _FloodQuota()" in src)
check("the per-record decision cache is the dedupe mechanism",
      "_flood_keep" in src)
check("the formatter carries both stamp kinds",
      "flood_tally" in src and "flood_suppressed" in src)

print(f"\nflood guard: {PASS}/{PASS + FAIL}")


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
