"""The fast path cannot book one exit twice — proven against the REAL apply_pnl.

The account is flat tonight, so the live scanner has no row to breach and no
order to place; fabricating a position in the live book to watch it close would
put invented money through the real ledger, which is not a test anyone should
run on the live account. Instead the guard is exercised directly: the REAL
`contest_account.apply_pnl` — the same call, with the same dedupe key shape the
close writer uses (`{pair}|{added}|getout`) — against a SANDBOX copy of the
state, driven by two consecutive `_fast_stop_tick` calls on a breached row.

Two worlds:
  A) the close succeeds and removes the row -> tick 2 sees an empty book;
  B) the close crashes AFTER apply_pnl (the row stays) -> tick 2 re-attempts the
     same exit and apply_pnl refuses it as a replay.

Nothing here touches the live state, ledger, watch file or the venue.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import background_scanner as bs
import contest_account as ca

PASS = FAIL = 0


def check(name, ok, why=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(why) if why else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


class Ex:
    def __init__(self, price):
        self.price = price
        self.bulk_calls = 0
        self.single_calls = 0

    def fetch_tickers(self, pairs=None):
        self.bulk_calls += 1
        return {p: {"last": self.price} for p in (pairs or [])}

    def fetch_ticker(self, pair):
        self.single_calls += 1
        return {"last": self.price}


# captured ONCE, before any rebind: every world starts from the LIVE files
# (copying from ca.STATE_FILE after a rebind would inherit world A's applied keys)
LIVE_STATE = ca.STATE_FILE
LIVE_TRADES = ca.TRADES_FILE


def sandbox():
    tmp = Path(tempfile.mkdtemp(prefix="faststop_sbx_"))
    shutil.copy(LIVE_STATE, tmp / "contest_account.json")
    shutil.copy(LIVE_TRADES, tmp / "contest_trades.jsonl")
    ca.STATE_FILE = tmp / "contest_account.json"
    ca.TRADES_FILE = tmp / "contest_trades.jsonl"
    return tmp


def balance():
    return ca.load_state().get("balance")


ROW = {"pair": "AAA/USD", "entry": 100.0, "size": 1000.0, "hard_stop_usd": 30.0,
       "added": "2026-11-15T12:00:00-07:00", "hard_stop_note": "x"}

_orig = (bs._load_contest_watch, bs.death_floor_gate, bs._run_light_pass,
         bs.FAST_STOP_ENABLED)
bs.death_floor_gate = lambda *a, **k: {}
bs.FAST_STOP_ENABLED = True

print("== A. the close succeeds and removes the row ==")
tmp_a = sandbox()
book_a = {"AAA/USD": dict(ROW)}
applied_a = []


def _closer_ok(*a, **k):
    for p in (k.get("only_pairs") or []):
        row = book_a.get(p)
        if not row:
            continue
        ok, bal_now, note = ca.apply_pnl(net=-30.0,
                                         dedupe_key=f"{p}|{row['added']}|getout")
        applied_a.append((ok, note, bal_now))
        if ok:
            book_a.pop(p, None)             # the close writer's row removal


bs._load_contest_watch = lambda: dict(book_a)
bs._run_light_pass = _closer_ok
bal_before = ca.load_state().get("balance") or ca.load_state().get("cash")
t1 = bs._fast_stop_tick(Ex(96.90), None)
t2 = bs._fast_stop_tick(Ex(96.90), None)
check("A1 the breach is reported on the first tick", t1[0] == ["AAA/USD"], t1[0])
check("A2 the second tick sees the row gone (no repeat breach)", t2[0] == [], t2[0])
check("A3 exactly ONE apply_pnl was applied", [x[0] for x in applied_a] == [True],
      applied_a)
_bal_a = applied_a[0][2]
check("A4 the balance moved exactly once (-30.00 from the pre-trade read)",
      _bal_a is not None and abs(_bal_a - (ca.load_state().get("balance") or _bal_a)) < 1e-12,
      (bal_before, _bal_a))

print("== B. the close crashes after apply_pnl; the next tick retries and is refused ==")
tmp_b = sandbox()
book_b = {"AAA/USD": dict(ROW)}
applied_b = []


def _closer_crashes_after(*a, **k):
    for p in (k.get("only_pairs") or []):
        row = book_b.get(p)
        ok, bal_now, note = ca.apply_pnl(net=-30.0,
                                         dedupe_key=f"{p}|{row['added']}|getout")
        applied_b.append((ok, note, bal_now))
        # the row is deliberately NOT removed: the cycle died before the write


bs._load_contest_watch = lambda: dict(book_b)
bs._run_light_pass = _closer_crashes_after
bal_before_b = ca.load_state().get("balance") or ca.load_state().get("cash")
t3 = bs._fast_stop_tick(Ex(96.90), None)
t4 = bs._fast_stop_tick(Ex(96.90), None)
check("B1 the row still breaches on the retry (the book never lost it)",
      t3[0] == ["AAA/USD"] and t4[0] == ["AAA/USD"], (t3[0], t4[0]))
check("B2 apply_pnl applied the first, refused the replay", [x[0] for x in applied_b] == [True, False],
      applied_b)
check("B3 the balance moved only once across both ticks",
      applied_b[0][2] is not None and applied_b[1][2] == applied_b[0][2],
      (applied_b[0][2], applied_b[1][2]))

bs._load_contest_watch, bs.death_floor_gate, bs._run_light_pass, bs.FAST_STOP_ENABLED = _orig
shutil.rmtree(tmp_a, ignore_errors=True)
shutil.rmtree(tmp_b, ignore_errors=True)

print("\nRESULT: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
