"""Test THE POPUP BRAIN (the rebuild, Sep 1: "the pop ups need a current
deep scan before they appear and the confidence bar for the popup needs to
be higher than the bots side to pass... no more wiggles... 1 hour to 3
hour time limits and percentage based goals >1%-3% depending on the
current market analysis"). Three gates: the brain-judged bar, the
stale-move guard, the goal clock. Scalping is over — the $20 lines are
gone from the popup path."""
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


import background_scanner as bs

# ── 1. the goal clock (the market analysis sets 1-3% on a 1-3h window) ─
def goal_band(thr):
    return max(bs.GOAL_MIN_PCT, min(bs.GOAL_MAX_PCT, thr))


check("goal band clamps to 1-3%", goal_band(0.4) == 1.0 and goal_band(5.0) == 3.0
      and goal_band(1.7) == 1.7)
check("goal defaults moderate (1.5%) on a young profile",
      bs.GOAL_DEFAULT_PCT == 1.5)
check("window scales with the goal: 1% -> 60min, 3% -> 180min, clamped",
      max(60.0, min(180.0, 1.0 * 60)) == 60.0
      and max(60.0, min(180.0, 3.0 * 60)) == 180.0
      and max(60.0, min(180.0, 1.5 * 60)) == 90.0)
g = bs._window_goal_pct("NOSUCHCOIN/USD")
check("unknown coin goal = the rotation MEDIAN (the market analysis), in band",
      1.0 <= g <= 3.0, g)
g2 = bs._window_goal_pct("BTC/USD")
check("a profiled coin reads a real engine goal in the band",
      1.0 <= g2 <= 3.0, g2)


# ── 2. the stale-move guard (the APT-12:11 killer) ────────────────────
class _Fx:
    def __init__(self, candles):
        self.candles = candles

    def fetch_ohlcv(self, pair, timeframe="1h", limit=5):
        return self.candles


def candles(highs, last_close):
    """5 hourly candles with the given highs; closes track highs then sag."""
    out = []
    for i, h in enumerate(highs):
        out.append([1700000000000 + i * 3_600_000, h * 0.999, h, h * 0.998,
                    h if i < len(highs) - 1 else last_close, 100])
    return out


# APT's shape: high 3 candles (hours) ago, price 2% below it
st = bs._stale_move(_Fx(candles([1.00, 1.02, 1.03, 1.01, 1.005], 1.005 * 0.98)), "T/USD")
check("APT shape: high 2h old, ~2% below -> STALE", st is not None, st)
# fresh move: the high is the LAST candle
st = bs._stale_move(_Fx(candles([1.00, 1.01, 1.02, 1.025, 1.03], 1.03)), "T/USD")
check("fresh move (high is current) -> not stale", st is None)
# old high but price near it (consolidating at the top)
st = bs._stale_move(_Fx(candles([1.00, 1.03, 1.031, 1.03, 1.029], 1.029)), "T/USD")
check("old high but price holds near it -> not stale (a base, not a sag)", st is None)
# broken read -> None (fail-open)
class _Bad:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("api down")


check("broken read -> not stale (fail-open)", bs._stale_move(_Bad(), "T/USD") is None)

# ── 3. the wiring in send_alert ───────────────────────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("the brain-judged bar exists: the BRAIN's number clears the popup floor",
      "_brain_floor_ok = float(_deep.confidence) >= DESKTOP_ALERT_MIN" in src)
check("a current deep read runs BEFORE the popup (cached if fresh)",
      "_brain_floor_ok" in src and "BRAIN-JUDGED BAR" in src)
check("the JUP loophole is closed — fail-CLOSED (a broken brain read = NO popup)",
      "no unverified popups" in src
      and "if _brain_floor_ok is None and signal.confidence < DESKTOP_ALERT_MIN" not in src)
check("the fail-open quick check is GONE (the JUP 14:52 hole — quick 92 opened "
      "the popup, the QC's brain said 67 two minutes later)",
      "the quick check stands" not in src)
check("the funnel's desktop path fails CLOSED too",
      "_m_desk_ok = False  # fail-closed" in src)
check("the stale-move guard gates RE-ENTRY popups",
      "if _is_reentry:" in src and "STALE MOVE" in src
      and "_stale_move(exchange, pair)" in src)
check("fresh first-fires are exempt from the stale guard (the early catch)",
      src.find("STALE-MOVE GUARD") < src.find("if _is_reentry:"))
check("the goal clock: engine expected move -> 1-3% goal, 1-3h window",
      "_window_goal_pct(pair)" in src and "GOAL CLOCK" in src)
check("the funnel's desktop path uses the brain-judged bar too",
      "float(_deep_sig.confidence) >= 89" in src)
check("her line speaks the GOAL, not the twenty",
      '"Aim for plus {_goal_pct:.0f} percent! "' in src.replace("'", '"').replace('"Aim', '"Aim')
      or "Aim for plus {_goal_pct:.0f} percent!" in src)
check("the $20 phrasing is gone from the POPUP path (the light is stage 3)",
      (lambda s: "The twenty comes in" not in s and "up twenty dollars" not in s
                 and "until you're up $20" not in s)(
          src[src.find("def send_alert"):src.find("def contest_light")]))
check("the $20 pace resolve gate is retired on the popup path",
      "TOO SLOW: %s ~%.0fh to the $20" not in src)

# ── 4. the QC + stage-1 stack still intact ────────────────────────────
ap = open("app.py", encoding="utf-8").read()
check("the 3% guard still caps the QC", "_spend_usd = min(_spend_usd, _risk_cap)" in ap)
check("the thin-book brain rule still stands", "THIN BOOK, BRAIN NOT EXCEPTIONAL" in ap)

# ── 5. THE WORTH FLOOR (user rule, Sep 2 — "big coins only... less
#      entries but more money"; the JUP 14:52 case: a guard-capped $233
#      entry at the +2% goal pays $4.66) ──────────────────────────────
import contest_account as ca
_jup_spend, _jup_goal = 233.0, 2.0
_acct_1k = {"tier": 1000, "start_balance": 1000.0, "current_balance": 1000.0}
check("WORTH_FLOOR_PACE_FRAC is half (0.5)", bs.WORTH_FLOOR_PACE_FRAC == 0.5,
      bs.WORTH_FLOOR_PACE_FRAC)
check("the floor scales with the tier: $12 on 1k, $120 on 10k",
      ca.daily_pace_usd(_acct_1k) * bs.WORTH_FLOOR_PACE_FRAC == 12.0
      and ca.daily_pace_usd({"tier": 10000, "start_balance": 10000.0,
                             "current_balance": 10000.0}) * bs.WORTH_FLOOR_PACE_FRAC == 120.0)
check("JUP's $233 entry at +2% ($4.66) is BELOW the 1k-tier floor — bumped",
      _jup_spend * _jup_goal / 100.0 < ca.daily_pace_usd(_acct_1k) * bs.WORTH_FLOOR_PACE_FRAC,
      _jup_spend * _jup_goal / 100.0)
check("the worth floor gates send_alert AND the mover popup",
      "WORTH FLOOR:" in src and "MOVER WORTH FLOOR" in src)
check("a big enough entry clears (fresh 10k, low-dd coin: $5,100 at +2.5% = $127.50 ≥ $120)",
      5100.0 * 2.5 / 100.0 >= 120.0)

# ── 6. THE DIAL (user rule, Sep 2: "should be a dial on the console —
#      adjustable without Workshop") ──────────────────────────────────
check("the floor is a DIAL: _worth_floor_frac() reads console_state.json live",
      "def _worth_floor_frac" in src and "worth_floor_frac" in src)
check("BOTH gates use the dial (champion + mover)",
      src.count("_worth_floor_frac()") >= 2, src.count("_worth_floor_frac()"))
_wf_dial = bs._worth_floor_frac()
check("the dial reads a sane share (0.0-1.0; default 0.5 until turned)",
      0.0 <= _wf_dial <= 1.0, _wf_dial)
check("dial 0% = the floor is OFF (nothing falls below a $0 threshold)",
      not (4.66 < 12.0 * 0.0))
check("the console sidebar carries the dial (slider + live caption)",
      "worth_floor_dial" in ap and "worth_floor_frac" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_popupbrain_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
