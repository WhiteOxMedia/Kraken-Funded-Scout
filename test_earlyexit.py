"""THE EARLY-EXIT WIRE (Oct 7, the dead-key cure): early_exit was the
discipline machine's only bucket with a weight, a phrase, a result
template, and a resolver join key — and NO append site anywhere. The
sold-loop classified exited-at-goal (bonus), giveback (reached the
goal, exited below the peak), and missed_exit (reached the goal, lost
money) — and a trade SOLD BEFORE THE GOAL whose coin paid AFTER the
sale counted NOTHING: not a bonus, not a bucket, not a dollar. The
audit's grep proved it: the list is born, summed (always 0), joined
(left_pairs/left_entries hardcoded empty), and never fed.

THE CHOICE, PINNED: WIRE, not retire. The evidence primitive already
exists (_coin_paid — the same 1h-candle test every bucket trusts), the
sale timestamp already rides the trade record (_sold_in_rotation's
_ts), the score/deduction/learn chain already consumes the bucket
(weights.get("early_exit") = 0.5, NUDGE-ready), the lesson vocabulary
already names it ("exited too soon"), and exit discipline is exactly
what this machine is learning right now (giveback 0.9, missed_exit
1.375 live and rising). Retiring the weight would amputate a real
lesson shape to avoid wiring one elif.

THE SHAPE: the branches above own every peak-reached outcome —
early_exit is the complement: the peak in hand never reached the
goal, the exit landed, and the coin paid the goal AFTER the sale.
_left is the unpaid goal span (goal_usd - max(realized, 0)). Only a
DETECTED post-sale payment docks (right-censoring is free — the
burial rule's DETECT-first standard), and the fail-closed candle
read can never phantom a payment.

Hermetic: module-function injection at the seam (_sold_in_rotation,
_popups_in_rotation, _suppressions_in_rotation, _fired_pairs), the
sandbox state (load/save patched — production discipline_state.json
never touched), a stubbed contest_account, an empty temp watch file,
and the worth-ladder ingest no-op'd (a synthetic rotation must never
mint a ledger row). speak=False throughout.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import discipline as dc

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


MST = timezone(timedelta(hours=-6))


class FakeExchange:
    """1h candles PER PAIR: highs after the sale pay the goal, highs
    during the hold shape peak_pct (the record's own 'high' field).
    A pair with no post-sale highs never pays (FLAT's control)."""

    def __init__(self, per_pair_highs):
        self.per_pair = per_pair_highs

    def fetch_ohlcv(self, pair, timeframe="1h", since=None, limit=10):
        return [[int(ts.timestamp() * 1000), 1, hi, 1, 1, 100]
                for ts, hi in self.per_pair.get(pair, [])]

    def fetch_ticker(self, pair):
        return {"last": 1.0}


class StubAccount:
    def load_state(self):
        return {"current_balance": 10000.0}

    def daily_pace_usd(self, acct):
        return 240.0


start = datetime(2026, 10, 6, 8, 0, tzinfo=MST)
end = datetime(2026, 10, 6, 12, 0, tzinfo=MST)
sale = datetime(2026, 10, 6, 9, 0, tzinfo=MST)
late_sale = datetime(2026, 10, 6, 11, 57, tzinfo=MST)


def _t(pair, pnl, entry=1.0, exit_=1.008, size=1000.0, goal=2.0,
       sold_at=None, high=None):
    t = {"pair": pair, "sold_at": (sold_at or sale).isoformat(),
         "entry": entry, "exit": exit_, "size": size, "pnl_net": pnl,
         "goal_pct": goal, "_ts": (sold_at or sale)}
    if high is not None:
        t["high"] = high
    return t


TRADES = [
    # A: the early exit — sold at +0.8% (goal 2%), coin paid 3% AFTER
    _t("EARLY/USD", 8.0),
    # B: control — exited AT the goal (the bonus shape, never early)
    _t("GOAL/USD", 18.0, exit_=1.018),
    # C: control — giveback (peak in hand reached the goal, exited below)
    _t("PEAKY/USD", 10.0, exit_=1.010, high=1.024),
    # D: control — sold before the goal, coin NEVER paid after: invisible
    _t("FLAT/USD", 8.0),
    # E: control — right-censoring: sold 3 min before the boundary,
    #    no horizon for the coin to prove anything: unjudged, no dock
    _t("LATE/USD", 8.0, sold_at=late_sale),
    # F: the harsh shape — sold at a LOSS before the goal, coin paid
    #    after: the unpaid goal span, floored at the goal (never above)
    _t("LOSSY/USD", -5.0),
]
POST_SALE = {"EARLY/USD": [(datetime(2026, 10, 6, 10, 0, tzinfo=MST), 1.03)],
             "LOSSY/USD": [(datetime(2026, 10, 6, 10, 0, tzinfo=MST), 1.03)],
             # the BOUNDARY candle: opens exactly at window_end — its
             # high lands after the rotation and is not in-window
             # evidence (end-exclusive coin test, CodeRabbit Oct 7)
             "LATE/USD": [(datetime(2026, 10, 6, 12, 0, tzinfo=MST), 1.03)]}

# ── the hermetic evaluation ──────────────────────────────────────────
_real = {k: getattr(dc, k) for k in
         ("_sold_in_rotation", "_popups_in_rotation",
          "_suppressions_in_rotation", "_fired_pairs", "load_state",
          "save_state", "_worth_ladder_ingest")}
_saved_states = []
_wf = Path(tempfile.mkdtemp(prefix="exw_")) / "watch.json"
_wf.write_text("{}", encoding="utf-8")
try:
    dc._sold_in_rotation = lambda s, e: [dict(t) for t in TRADES]
    dc._popups_in_rotation = lambda s, e: []
    dc._suppressions_in_rotation = lambda s, e: {}
    dc._fired_pairs = lambda s, e, voice_file=None: {"EARLY/USD"}
    dc.load_state = lambda: {"weights": dict(dc.DEFAULT_WEIGHTS),
                             "history": [], "lessons": [],
                             "burial_ledger": {}}
    dc.save_state = lambda st: _saved_states.append(st)
    dc._worth_ladder_ingest = lambda result: None
    sys.modules["contest_account"] = StubAccount()

    res = dc.evaluate_rotation(
        FakeExchange(POST_SALE), "08-12", when=end, speak=False,
        watch_file=str(_wf))

    usd = res.get("usd_left") or {}
    # the join keys ride the PERSISTED history row (_entry), not the
    # returned result — the resolver reads them from history, so the
    # pin reads them there too
    _row = (_saved_states[-1]["history"] or [{}])[-1]
    lp = (_row.get("left_pairs") or {}).get("early_exit") or []
    le = (_row.get("left_entries") or {}).get("early_exit") or {}
finally:
    for k, v in _real.items():
        setattr(dc, k, v)
    sys.modules.pop("contest_account", None)

# 1. the bucket earns its dollars: A (20 - 8 = 12) + F (20 - 0 = 20)
check("1.1 the early-exit bucket earns the unpaid goal span "
      "(A: 20-8=12 + F: 20-0=20 -> 32.0)",
      usd.get("early_exit") == 32.0, str(usd))

# 2. the controls stay out: usd sums to giveback 14 + early 32, no
#    other bucket leaks, and GOAL's +2 bonus proves it took the bonus
#    branch (FLAT never paid; LATE's only evidence is the 12:00
#    boundary candle — end-exclusive, not in-window)
check("2.1 the controls stay out of every bucket",
      usd.get("early_exit") == 32.0 and usd.get("giveback") == 14.0
      and usd.get("missed") == 0 and usd.get("missed_exit") == 0
      and res.get("bonuses") == 2.0,
      str(usd) + " bonuses=" + str(res.get("bonuses")))

# 3. the resolver's sale-join NEVER sees early-exit pairs: the bucket
#    is BORN PROVEN (the leave exists because a post-sale payment was
#    detected) — a later similar sale must never prove its own leave
#    (CodeRabbit, Oct 7: the originating sale cannot credit the $9 it
#    left as resolved)
check("3.1 early_exit stays out of left_pairs/left_entries (born "
      "proven — the resolver's inbox is for unproven leaves only)",
      lp == [] and le == {}, str(lp) + str(le))

# 4. the score actually docks: early_exit's own deduction is
#    min(50, 50*(32/48)*0.5) = 16.67, and PEAKY's giveback rides beside
#    it at min(50, 50*(14/48)*0.9) = 13.125; the only bonus is B's +2
_want = round(100.0 - (50.0 * (32.0 / 48.0) * 0.5
                       + 50.0 * (14.0 / 48.0) * 0.9) + 2.0, 1)
check(f"4.1 the deduction lands in the score at the live weight "
      f"(want {_want})",
      res.get("score") == _want, str(res.get("score")))

# 5. the machine's own money never disagrees: realized = 8+18+10+8+8-5
check("5.1 realized_pnl sums the sold trades (47.0)",
      res.get("realized_pnl") == 47.0, str(res.get("realized_pnl")))

# 6. the sandbox never touched production: save_state captured, the
#    ladder ingest was a no-op (a synthetic rotation mints nothing)
check("6.1 the evaluation persisted to the SANDBOX only",
      len(_saved_states) == 1, str(len(_saved_states)))

# ── 7. BORN-PROVEN MEANS PROVEN EVERYWHERE (CodeRabbit's isolated
#    review of 9635a5e, run Oct 8 once the quota reset — the one
#    untriaged major, cured): the bucket's dollars leave the store
#    through usd_left, but its inbox is deliberately empty (check 3.1
#    — a leave must never prove itself). The RESOLVER'S remainder and
#    _learn's agreement bar both sum the whole usd_left map, so a
#    window that carries early-exit leave could never reach "all
#    components dust": the resolver would badge it "partially
#    resolved ... early_exit $X still unproven" forever, and the
#    agreement bar would refuse the calibration — about money the
#    candle test ALREADY PROVED landed. Born-proven is proven
#    everywhere, or the bucket is born-proven only where it doesn't
#    bite. THE CONTRACT (CodeRabbit's second major, cured in-amend):
#    the early-exit gap is DETECTED opportunity, never realized money
#    — the resolver proves the window with the detection but never
#    credits it as a sale, and the DETECTED wording says which is
#    which. THE JOURNAL (CodeRabbit's first major, cured in-amend):
#    every fixture reads a TEMP journal through the resolver's
#    injectable trades_file seam — the live contest_trades.jsonl is
#    never opened, a concurrent sale can never be lost to a
#    backup/restore window.
_tj = Path(tempfile.mkdtemp(prefix="exw_journal_")) / "trades.jsonl"
try:
    _tj.write_text("", encoding="utf-8")   # no sale evidence at all
    _day = (datetime.now(MST) - timedelta(days=1)).date()
    _verdict = ("Score 50, realized $+0 — $12 left on the table: the "
                "money hasn't confirmed the score, so this is not "
                "agreement. Weights hold unproven — the calibration "
                "stands only when the leave realizes and the money "
                "left on the table becomes $0.")
    _hist = {"date": _day.isoformat(), "rot": "08-12", "score": 50.0,
             "verdict": _verdict,
             "usd_left": {"missed": 0.0, "giveback": 0.0, "missed_exit": 0.0,
                          "early_exit": 12.0, "system_burial": 0.0},
             "left_pairs": {"missed": [], "giveback": [],
                            "missed_exit": [], "early_exit": [],
                            "system_burial": []},
             "left_entries": {"missed": {}, "giveback": {},
                              "missed_exit": {}, "early_exit": {},
                              "system_burial": {}},
             "system_pairs": []}
    _mk = lambda: {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [],
                   "lessons": []}
    _st_e = _mk(); _st_e["history"].append(dict(_hist))
    _n_e = dc._resolve_prior_verdict(_st_e, trades_file=_tj)
    check("7.1 a leave of ONLY born-proven early-exit money grades "
          "PROVEN with zero sales (the candle already proved it)",
          _n_e == 1 and "proven after the fact"
          in str(_st_e["history"][0]["verdict"]),
          str(_n_e) + " " + str(_st_e["history"][0]["verdict"])[:90])
    check("7.1c the DETECTED/realized contract: the proven verdict "
          "says the gap was DETECTED by the candle, never that it "
          "was realized by a sale — and no verdict_credit rides it",
          "DETECTED" in str(_st_e["history"][0]["verdict"])
          and "realized by sale" in str(_st_e["history"][0]["verdict"])
          and not (_st_e["history"][0].get("verdict_credit") or {})
          .get("early_exit"),
          str(_st_e["history"][0]["verdict"])[:130])
    _n_e2 = dc._resolve_prior_verdict(_st_e, trades_file=_tj)
    check("7.1b the born-proven upgrade is idempotent (a re-review "
          "moves no words, appends no duplicate lesson)",
          _n_e2 == 0 and len(_st_e["lessons"]) == 1,
          str(_n_e2) + " lessons=" + str(len(_st_e["lessons"])))
    _st_m = _mk(); _st_m["history"].append(
        dict(_hist, usd_left={"missed": 500.0, "giveback": 0.0,
                              "missed_exit": 0.0, "early_exit": 12.0,
                              "system_burial": 0.0},
             left_pairs={"missed": ["M/USD"], "giveback": [],
                         "missed_exit": [], "early_exit": [],
                         "system_burial": []},
             left_entries={"missed": {"M/USD": 1.0}, "giveback": {},
                           "missed_exit": {}, "early_exit": {},
                           "system_burial": {}}))
    _row_m = json.dumps({"sold_at": _day.isoformat()
                         + "T11:00:00-06:00", "pair": "M/USD",
                         "pnl_net": 520.0, "entry": 1.0, "exit": 1.02,
                         "size": 1000.0, "applied": True}) + "\n"
    _tj.write_text(_row_m, encoding="utf-8")
    _n_m = dc._resolve_prior_verdict(_st_m, trades_file=_tj)
    check("7.2 with a live component beside it, early_exit never "
          "surfaces as 'still unproven' (it was born proven)",
          _n_m == 1 and "early_exit" not in
          str(_st_m["history"][0]["verdict"]),
          str(_st_m["history"][0]["verdict"])[:120])
    _tj.write_text("", encoding="utf-8")
    # 7.4 THE UNREADABLE-JOURNAL EXCEPTION (Copilot's low on the
    #     second cut, cured in-amend): a born-proven-only window
    #     resolves even when the journal is MISSING — the candle
    #     detection needs no sale evidence. The injectable seam makes
    #     this testable without touching the live file.
    _st_u = _mk(); _st_u["history"].append(dict(_hist))
    _n_u = dc._resolve_prior_verdict(_st_u,
                                     trades_file=_tj.parent / "absent.jsonl")
    check("7.4 an unreadable journal never strands a born-proven "
          "window (the detection IS the proof, no sale evidence "
          "needed)",
          _n_u == 1 and "proven after the fact"
          in str(_st_u["history"][0]["verdict"]),
          str(_n_u) + " " + str(_st_u["history"][0]["verdict"])[:90])
    check("7.5 the LESSONS LOG keeps the detected/realized contract "
          "too — a born-proven-only window never says 'the money "
          "landed' about a gap no sale ever banked",
          any("DETECTED by the post-sale candle test" in l["lesson"]
              and "never realized" not in l["lesson"].split("DETECTED")[0]
              for l in _st_u["lessons"])
          and not any("the money landed" in l["lesson"]
                      for l in _st_u["lessons"]),
          str(_st_u["lessons"])[-160:])
    _st_l = _mk()
    _res_l = {"score": 50.0, "realized_pnl": 60.0, "pace_share": 48.0,
              "usd_left": {"missed": 0.0, "giveback": 0.0,
                           "missed_exit": 0.0, "early_exit": 12.0,
                           "system_burial": 0.0},
              "ts": datetime.now(MST).isoformat()}
    _lesson_l = dc._learn(_st_l, _res_l)
    check("7.3 the agreement bar weighs the leave MINUS the "
          "born-proven component ($12 early-exit leave + $60 "
          "realized stands)",
          "Score and money agree" in _lesson_l, _lesson_l[:100])
finally:
    pass   # the fixture journal is a TEMP file — the live
           # contest_trades.jsonl was never opened, nothing to restore

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
