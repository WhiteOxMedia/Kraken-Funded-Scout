"""Timing tournament: buy-low depth, champion-mover gate, explicit sell-high.

Reuses the concurrency harness (same data load, `build_base_rows`, the live 1%
participation floor, the $150 gap guard / $5,000 book cap) and makes the
ENTRY/EXIT quality the axes:

    entry_max   how far into the pair's own 24-bar range the buy may sit
                (0.20 = only near the low, 0.50 = the middle)
    mover       decision-time cross-sectional 24h excess return (`xs24`):
                "leader" = top tercile that bar, "laggard" = bottom tercile,
                "any" = no gate.  The terciles are computed from the same
                timestamp's rows only - nothing after the signal bar is used.
    sell_high   the exit must book at/above this fraction of the pair's own
                24-bar range (0.50 mid, 0.70 upper), on top of the policy's own
                exit mode (fixed / breakeven / chandelier / scalp / revert).

Stage 1 screens every promoted+watch policy at 9% x 5 slots on 10 pairs.
Stage 2 takes the forward leaders and prices concurrency: (6%,8), (9%,5),
(12%,4) slots at 12/10/6 pairs.  Stage 3 re-runs them with the newest cached
window (2026-11-10 -> 2026-11-17) appended: a death there disqualifies the
candidate regardless of its 53-day total.

Window 1 is in-sample and never used in any ranking.  A death HALTS the chain
(`chain_result` stops trading there), so every later window is marked
`evidence: false` and the row carries `forward_ret_pct: null`: a dead candidate
has no forward return to rank, only `forward_ret_pct_until_death` for diagnosis.

Every artifact carries `tape` (the cache's newest bar, bars and pair count) so a
re-run against newer data is identifiable as a different tape, not code drift.

TIMING QUALITY IS SCORED, not assumed.  Each candidate's forward signals are
scored on (a) where the buy sat in its own bar's range, (b) how far below the
pair's 24-bar high the buy sat, and (c) the pair's own 24-bar momentum, credited
only when it was negative - so a buy after weakness outranks a buy after
extension.  The exit side is scored on where the sell sat in its bar's range and
whether it banked the gain (target, `bank_r` R-multiple, or a window-rollover
trim) instead of timing out.  `rank_key` orders by +12% weeks, then that combined
timing score, then best week, then forward return, and every row carries the
components so the ranking is auditable.

Stage 1b re-prices the stage-1 leaders with the sell-high exit arms
(`bank_r` x `trim_rollover`): book the gain at N x risk and never carry a
position across a contest window boundary.

Usage: python scratch/_timing_tournament.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "edge_registry.json"   # experiments may pin a snapshot
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import edge_engine as ee                             # noqa: E402
import edge_policy as ep                             # noqa: E402
import contest_account as ca                         # noqa: E402
import spread_table as st                            # noqa: E402
from config import TradingConfig                     # noqa: E402

# THE CHALLENGE'S ASSETS, NOT THE TAPE'S.  Only these 54 can be bought in the
# contest; the other 145 pairs on the cache are never candidates.
CONTEST = frozenset(st._clean(p) for p in TradingConfig().trading_pairs)

CHAIN = ee.CHAIN_START
BOOK_CAP = ca.stress_book_cap_usd(CHAIN)
GAP_ALLOW = ca.stress_allowance_usd(CHAIN)
GAP_PCT = ca.GAP_STRESS_PCT
TARGET = 12.0

ENTRY_MAX = (0.20, 0.50)
MOVERS = ("any", "leader", "laggard")
SELL_HIGH = (None, 0.50)
# SELL-HIGH EXIT ARMS: bank the gain at N x risk and/or never carry a position
# across a contest window boundary (the rollover trim marks out at the last bar
# before it, instead of letting a trailing exit give the move back).
BANK_R = (None, 1.5, 2.5)
TRIM = (False, True)
EXIT_ARMS = tuple((b, tr) for b in BANK_R for tr in TRIM)
EXIT_STAGE = 30
# THE SHIPPED EXITS ADAPT TO THE DEATH LINE.  Rather than hand-picking a book
# that happened to survive the newest week, every resolver in this tournament
# caps its stop at `RISK_CAP` of the entry price: a fully deployed book whose
# every slot stops out then loses about that much of the account, not whatever
# eight 1.5-ATR stops add up to.  The exits are the lever; the selection is not.
RISK_CAP = ep.RISK_CAP_PCT
# ...and the other half of the same adaptation: once the window has lost this
# much of its start, the book stops replacing its stopped-out slots.  Eight
# capped stops plus a second round of them is how a 100% book still lost 3.5%
# in the newest week; the stand-down is what makes the cap bind on the WEEK.
STAND_DOWN = 1.0            # stage-1 leaders re-priced with the exit arms
TIMING_SHARE = 0.55        # rank score: entry timing vs exit quality
# THE BOOK CEILING.  `contest_account.stress_book_cap_usd` is OUR guard (a
# user rule: half the remaining death-line buffer at one 3% adverse gap, i.e.
# $5,000 on a fresh $10k), not a Kraken rule - the platform states only the
# -3% death line, the +12% target and the 0.08% round-trip cost.  What the
# platform does impose is the account's own cash: a spot book cannot hold more
# than 100% of the balance without margin it does not offer.  So the stretch
# stage prices books up to that cash ceiling and leaves the guard as a flag.
CASH_CAP = CHAIN
STRETCH = ((0.125, 8), (0.15, 6), (0.20, 5), (0.25, 4))   # all 100% books
STRETCH_STAGE = 12         # highest-pace families priced at the cash ceiling
SCREEN_SIZE, SCREEN_SLOTS, SCREEN_BREADTH = 0.09, 5, 10
CONCURRENCY = ((0.06, 8), (0.09, 5), (0.12, 4))
BREADTHS = (12, 10, 6)
EXT_WINDOW = ("2026-11-10", "2026-11-17")
FWD_START = date.fromisoformat(ee.WINDOWS[1][0])


def weeks_between(a: date, b: date):
    out, s = [], a
    while s < b:
        e = min(s + timedelta(days=7), b)
        out.append((s, e))
        s = e
    return out


def equity_on(curve, d):
    best = None
    for k, v in curve.items():
        if k <= d and (best is None or k > best[0]):
            best = (k, v)
    return best[1] if best else CHAIN


class Timing:
    def __init__(self):
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.reg = reg
        self.tape = ee.tape_stamp()
        self.pool = [v["id"] for v in reg["candidates"].values()
                     if v.get("status") in ("promoted", "watch")
                     and v.get("windows")]
        self.pairs = ee.load_pairs()
        self.base = ep.build_base_rows(self.pairs)
        ee.fill_atr_rank(self.base)
        # one resolver per exit variant; `sel` keys on the variant so a cached
        # row exit is never reused for a different sell-high rule
        self._res: dict = {}
        self._hi24, self._cl, self._mom24 = {}, {}, {}
        for p_, d_ in self.pairs.items():
            self._hi24[p_] = d_["high"].rolling(24).max().to_numpy()
            self._cl[p_] = d_["close"].to_numpy(float)
            self._mom24[p_] = (d_["close"] / d_["close"].shift(24) - 1.0).to_numpy()
        ep.set_entry_timing(ep.ENTRY_TIMING_MAX)
        # LIQUIDITY IS A PROPERTY OF THE ORDER, NOT OF THE ENGINE.  A single
        # $900 fill test answers a question no book asks: at 25% x 4 on a $10k
        # balance the order is $2,500, and a pair whose bar cannot absorb that
        # had no fill at any price.  Each book gets the universe its own order
        # size earns.  The spread table has the last word: a pair whose
        # MEASURED round trip is at or above the engine's 0.09% gross edge is
        # refused by name (`cost_gate`), and an unmeasured pair is not a cheap
        # pair - it is a placeholder - so it is refused too.
        self.spread = st.load_table()
        # ONLY THE CONTEST'S ASSETS ARE CANDIDATES.  Base rows are still built
        # on the whole tape (so the ATR and depth ranks stay comparable with
        # the earlier tournaments), but every universe, cost gate, mover
        # tercile and trade count below is the contest's own 54 assets: a pair
        # outside that list cannot be bought at any spread.
        self.contest = {p: d for p, d in self.pairs.items()
                        if st._clean(p) in CONTEST}
        self.cost_gate = {p: st.cost_gate(p, table=self.spread)
                          for p in self.contest}
        self._liq = {p: float((d["close"] * d["volume"]).tail(2000).median())
                     for p, d in self.pairs.items()}
        self._uni: dict = {}
        self._order_pairs: dict = {}
        self.fill = ep.universe_for(self.contest, CHAIN * 0.09,
                                    ep.UNIVERSE_PARTICIPATION)
        # CHAMPION-MOVER GATE: the pair's own 24-bar return, ranked across the
        # CONTEST'S panel AT the signal bar (percentile 0..1) - a pair is a
        # leader against the assets the book can actually buy, not against 145
        # pairs it can never hold.  `f["xs24"]` is NaN
        # in this cache because the panel index and the row timestamps sit on
        # different grids, so the rank is rebuilt here from closes up to the
        # signal bar - no later bar is touched.
        import pandas as pd
        closes = pd.DataFrame({p_: d["close"] for p_, d in self.contest.items()})
        self.rk = closes.pct_change(24).rank(axis=1, pct=True)
        self.rk_idx = self.rk.index
        self.col = {p_: i for i, p_ in enumerate(self.rk.columns)}
        for r in self.base:
            j = int(self.rk_idx.searchsorted(r["ts"], side="right")) - 1
            v = float("nan")
            i = self.col.get(r["pair"])
            if j >= 0 and i is not None:
                v = float(self.rk.iat[j, i])
            r["mv"] = ("leader" if v >= 2.0 / 3.0 else
                       "laggard" if v <= 1.0 / 3.0 else "mid")
        # per-row buy-low inputs, computed once: how far the signal bar sat
        # below its pair's own 24-bar high, and the pair's own 24-bar return
        for r in self.base:
            p_, i_ = r["pair"], int(r["i"])
            cl = float(self._cl[p_][i_]) if i_ < len(self._cl[p_]) else 0.0
            hi24 = (float(self._hi24[p_][i_])
                    if i_ < len(self._hi24[p_]) else float("nan"))
            mom = (float(self._mom24[p_][i_])
                   if i_ < len(self._mom24[p_]) else float("nan"))
            r["_dh24"] = ((hi24 / cl - 1.0) * 100.0
                          if cl > 0 and math.isfinite(hi24) and hi24 > 0
                          else None)
            r["_mom24"] = mom * 100.0 if math.isfinite(mom) else None
        self._met: dict = {}
        self._sel = {}
        self.end = date.fromisoformat(ee.WINDOWS[-1][1])
        self.reset_weeks()

    def reset_weeks(self):
        self.weeks = weeks_between(FWD_START, self.end)

    def pairs_for(self, order: float) -> list[str]:
        """What an order of `order` dollars can fill, cheapest-cost first.

        Fillability is `universe_for` at the book's own notional; the cost gate
        then refuses every pair whose own measured round trip cannot pay the
        engine's gross edge.  Remaining pairs are ordered by bar liquidity so a
        `breadth` cap keeps the most fillable ones.
        """
        order = float(order)
        if order not in self._order_pairs:
            u = ep.universe_for(self.contest, order, ep.UNIVERSE_PARTICIPATION)
            keep = [p for p in u.pairs
                    if self.cost_gate[p]["measured"]
                    and self.cost_gate[p]["verdict"] != "refuse"]
            self._order_pairs[order] = sorted(keep, key=lambda p: -self._liq[p])
        return self._order_pairs[order]

    def uni_for(self, order: float, breadth: int) -> "ep.Universe":
        key = (float(order), int(breadth))
        if key not in self._uni:
            pairs = self.pairs_for(order)[:int(breadth)]
            self._uni[key] = ep.Universe(frozenset(pairs), float(order),
                                         ep.UNIVERSE_PARTICIPATION,
                                         bar_minutes=self.fill.bar_minutes)
        return self._uni[key]

    def res_for(self, sell_high, bank_r, trim, risk_cap=RISK_CAP):
        key = (sell_high, bank_r, trim, risk_cap)
        if key not in self._res:
            r = ep.Resolver(self.pairs, TradingConfig(), fill="close",
                            fee_fn=ep.pair_cost, sell_high=sell_high,
                            bank_r=bank_r, trim_rollover=trim,
                            risk_cap_pct=risk_cap)
            r.use_cache = True
            self._res[key] = r
        return self._res[key]

    def _entry_metrics(self, sel, key=None):
        if key is not None and key in self._met:
            return self._met[key]
        out = self._entry_metrics_uncached(sel)
        if key is not None:
            self._met[key] = out
        return out

    def _entry_metrics_uncached(self, sel):
        """Buy-low entry score and sell-high exit evidence for one policy.

        Measured over the policy's OWN forward signals (after every policy
        gate), not the slot-capped subset the chain executed: this is the
        timing quality of the rule, and it is comparable across concurrency
        arms because it does not depend on how many slots opened.
        """
        lo = pd.Timestamp(FWD_START.isoformat(), tz="UTC")
        hi = pd.Timestamp(ee.WINDOWS[-1][1], tz="UTC")
        parts, sells, banked, n = [], [], [], 0
        for r, res in sel:
            if not (lo <= r["ts"] < hi):
                continue
            ex = res.get("exit_ts")
            if ex is None or pd.Timestamp(ex) >= hi:
                continue
            dh = r.get("_dh24")
            if dh is None:
                continue
            n += 1
            parts.append(ep.entry_timing_parts(res.get("entry_from_low"),
                                               dh, r.get("_mom24")))
            q = ep.exit_quality_parts(res.get("exit_from_high"),
                                      res.get("why"), res.get("net"))
            sells.append(q["sell"])
            banked.append(1.0 if q["banked"] else 0.0)
        good = [x for x in parts if x]
        mean = ({k: round(sum(d[k] for d in good) / len(good), 4)
                 for k in ("low", "dip", "weak")} if good else None)
        entry = ep.entry_timing_score(mean)
        sell = round(sum(sells) / len(sells), 4) if sells else None
        bank = round(sum(banked) / len(banked), 4) if banked else None
        return {"signals": n, "entry_timing_score": entry,
                "entry_timing_parts": mean, "sell_high_score": sell,
                "bank_rate": bank,
                "timing_score": (round(TIMING_SHARE * entry
                                       + (1.0 - TIMING_SHARE) * sell, 4)
                                 if entry is not None and sell is not None
                                 else None)}

    def sel(self, pid, breadth, mover, sell_high, bank_r=None, trim=False,
            risk_cap=RISK_CAP, order=None):
        order = CHAIN * 0.09 if order is None else float(order)
        key = (pid, breadth, mover, sell_high, bank_r, trim, risk_cap, order)
        if key not in self._sel:
            pol = ep.policy_from_spec(self.reg["candidates"][pid]["spec"])
            ep.set_universe(self.uni_for(order, breadth))
            rows = (self.base if mover == "any"
                    else [r for r in self.base if r["mv"] == mover])
            res = self.res_for(sell_high, bank_r, trim, risk_cap)
            self._sel[key] = ep.selected_for(pol, rows, res)
        return self._sel[key]

    @staticmethod
    def fam(pid):
        f = pid.split(".")
        return (f[1], f[2], f[3], f[5], f[6])

    def run(self, pid, entry_max, mover, sell_high, size, slots, breadth,
            bank_r=None, trim=False, risk_cap=RISK_CAP,
            stand_down=STAND_DOWN):
        pol = ep.policy_from_spec(self.reg["candidates"][pid]["spec"])
        order = CHAIN * float(size)
        sel = self.sel(pid, breadth, mover, sell_high, bank_r, trim, risk_cap,
                       order)
        timing = self._entry_metrics(
            sel, (pid, breadth, mover, sell_high, bank_r, trim, risk_cap, order))
        ch = ep.chain_result(pol, sel,
                             size_frac=size, slots=slots, entry_max=entry_max,
                             stop_after_pass=False, keep_days=True,
                             keep_trades=True, stand_down_pct=stand_down)
        nwin = len(ee.WINDOWS)
        # EVIDENCE MASK: window 0 is in-sample and never ranked; every window
        # the chain did not reach (a death halted it) is not evidence either.
        # Nothing below may read a non-evidence window.
        ev = [bool(ch["windows"][k].get("evidence", True)) for k in range(nwin)]
        ev[0] = False
        curve, run = {}, CHAIN
        last_day = None
        for k in range(nwin):
            if not ev[k]:
                continue
            w = ch["windows"][k]
            f = run / float(w["start_balance"])
            for d in sorted(w.get("day_pnl") or {}):
                run += float(w["day_pnl"][d]) * f
                curve[date.fromisoformat(d)] = run
                last_day = date.fromisoformat(d)
        start_fwd = CHAIN * (1.0 + ch["windows"][0]["return_pct"] / 100.0)
        fwd_curve = {d: v for d, v in curve.items() if d >= FWD_START}
        fwd_curve[FWD_START] = start_fwd
        end = max(curve) if curve else FWD_START
        fwd = (curve.get(end, start_fwd) / start_fwd - 1.0) * 100.0
        fwd_idx = [k for k in range(1, nwin) if ev[k]]
        # A week is evidence only if a forward window was still alive on it:
        # a chain that died before any forward window has no weekly curve at
        # all, rather than a flat one.
        weeks = ([w for w in self.weeks
                  if last_day is not None and w[0] <= last_day]
                 if fwd_idx else [])
        wret = [round((equity_on(fwd_curve, b) / equity_on(fwd_curve, a) - 1.0)
                      * 100.0, 3) for a, b in weeks]
        dists = [float(ch["windows"][k]["min_balance"])
                 - float(ch["windows"][k]["start_balance"]) * 0.97
                 for k in fwd_idx
                 if ch["windows"][k].get("headroom_to_death") is not None]
        worst = min(dists) if dists else None
        died = [bool(ch["windows"][k].get("died"))
                for k in range(1, nwin)]
        pnl, trades = [], 0
        for k in fwd_idx:
            for t in ch["windows"][k].get("trades_log") or []:
                spent = float(t["spend"])
                if spent <= 0:
                    continue
                trades += 1
                pnl.append(spent * float(t["net"]) / 100.0)
        # WHICH PAIRS THE BOOK ACTUALLY BOUGHT, so the artifact itself proves
        # the scope: `pairs_traded_outside_contest` must be empty on every row.
        traded_pairs = {t_["pair"] for k in fwd_idx
                        for t_ in (ch["windows"][k].get("trades_log") or [])}
        wins = len([p for p in pnl if p > 0])
        book = slots * size * CHAIN
        fwd_ok = bool(ch.get("forward_evidence", True))
        died_at = ch.get("dead_at_window")

        def label(k):
            return f"{ee.WINDOWS[k][0]}->{ee.WINDOWS[k][1]}"

        return {
            "id": pid, "entry_max": entry_max, "mover": mover,
            "sell_high": sell_high, "size": size, "slots": slots,
            "breadth": breadth, "book_usd": round(book, 2),
            "gap_usd": round(book * GAP_PCT / 100.0, 2),
            "guard_ok": bool(book <= BOOK_CAP + 1e-9),
            "guard_self": bool(book <= BOOK_CAP + 1e-9),
            "guard_cash": bool(book <= CASH_CAP + 1e-9),
            "risk_cap_pct": risk_cap, "stand_down_pct": stand_down,
            "order_usd": round(order, 2),
            "pairs_universe": len(self.pairs_for(order)),
            "pairs_universe_list": sorted(self.pairs_for(order)[:int(breadth)]),
            "pairs_traded": len(traded_pairs),
            "pairs_traded_outside_contest": sorted(
                st._clean(p) for p in traded_pairs
                if st._clean(p) not in CONTEST),
            "stand_down_skips": sum(int(ch["windows"][k].get(
                "stand_down_skips") or 0) for k in fwd_idx),
            "forward_ret_pct": round(fwd, 3) if fwd_ok else None,
            "forward_ret_pct_until_death": (round(fwd, 3) if not fwd_ok
                                            else None),
            "forward_evidence": fwd_ok,
            "disqualified": f"death in {died_at}" if not fwd_ok else None,
            "dead_at_window": died_at,
            "evidence_windows": [label(k) for k in fwd_idx],
            "not_evidence_windows": [label(k) for k in range(1, nwin)
                                     if not ev[k]],
            "window_rets": [(round(float(ch["windows"][k]["return_pct"]), 3)
                             if ch["windows"][k].get("return_pct") is not None
                             else None) for k in range(1, nwin)],
            "weeks": wret, "weeks_12": len([w for w in wret if w >= TARGET]),
            "best_week": max(wret) if wret else None,
            "weeks_not_evidence": len(self.weeks) - len(weeks),
            "worst_distance_usd": (round(worst, 2) if worst is not None
                                   else None),
            "died_windows": died, "died_anywhere": any(died),
            "died_newest": bool(died[-1]) if died else None,
            "trades": trades,
            "trades_per_week": round(trades / max(len(weeks), 1), 2),
            "win_rate": round(wins / trades, 4) if trades else None,
            "per_trade_points": (round(statistics.mean(pnl) / CHAIN * 100.0, 4)
                                 if pnl else None),
            "bank_r": bank_r, "trim_rollover": bool(trim),
            **timing,
            "tape_max_ts": self.tape["max_ts"],
        }


def rank_key(r):
    """+12% weeks first (the contest rule), then TIMING QUALITY, then the
    measured week and the forward return.  A row with no timing score sorts
    below any row that has one."""
    return (r["weeks_12"],
            r["timing_score"] if r.get("timing_score") is not None else -1.0,
            r["best_week"] if r["best_week"] is not None else -99.0,
            r["forward_ret_pct"] if r["forward_ret_pct"] is not None else -999)


def clean(r):
    """The contest gates, and nothing self-imposed.

    Rankable means: every forward window is evidence, the chain never touched
    the death line, and the book fits inside the account's own cash.  The
    $5,000 gap-stress cap is OUR guard (`stress_book_cap_usd`), recorded per
    row as `guard_self`, not a Kraken rule, so it does not disqualify a row -
    it is reported.  A dead row carries forward_ret_pct None, so it can never
    enter a sort, a median or the champion selection.
    """
    return bool(r.get("guard_cash", r["guard_ok"])
                and r.get("forward_evidence", True)
                and r["forward_ret_pct"] is not None
                and not any(r["died_windows"])
                and r["forward_ret_pct"] > 0)


# THE CHAMPION HAS TO BUY LOW AND SELL HIGH, not merely survive.  The timing
# score has existed since this tournament was built, but it was only a
# TIE-BREAK inside `rank_key`: a row that bought every extension and sold every
# low still promoted if a +12% week happened to land inside its tape.  The user
# rule (Nov 18) is that the tournament PICK better timed movers, so promotion
# now needs both halves in the top third of the clean rows that carry a
# measurement.  The bars are quantiles of the same run, not hand-picked
# numbers, and a row with no measurement can never promote.
# ...AND THE BARS ARE MEASURED ON THE CONTENDERS, NOT ON THE WHOLE FIELD.
# A top-third bar over all clean rows selected rare deep-dip books that never
# produced a +12% week - the gate replaced the contenders instead of ranking
# them, and nothing promoted at all.  The bars are the MEDIAN of the rows that
# can actually win (promoted rows when any exist, clean rows otherwise), so the
# gate asks "better timed than the field it is competing in".
CHAMPION_ENTRY_Q = 0.5
CHAMPION_EXIT_Q = 0.5


def _q(xs, q):
    ys = sorted(float(x) for x in xs if x is not None)
    if not ys:
        return None
    i = min(len(ys) - 1, max(0, int(round(q * (len(ys) - 1)))))
    return round(ys[i], 4)


def timing_bars(rows) -> dict:
    """The buy-low / sell-high bars a champion must clear.

    Measured on the contender rows of the same run: promotion means "better
    than the field at buying the low" AND "better than the field at selling the
    high", so the two halves cannot be traded off against each other by one
    strong side.  `rows` is the contender set (promoted rows, or the clean
    field when nothing has won a +12% week yet).
    """
    ent = [r.get("entry_timing_score") for r in rows if clean(r)]
    ext = [r.get("sell_high_score") for r in rows if clean(r)]
    return {"entry": _q(ent, CHAMPION_ENTRY_Q),
            "exit": _q(ext, CHAMPION_EXIT_Q),
            "entry_quantile": CHAMPION_ENTRY_Q,
            "exit_quantile": CHAMPION_EXIT_Q,
            "n_entry": len([x for x in ent if x is not None]),
            "n_exit": len([x for x in ext if x is not None])}


def timing_ok(r, bars: dict) -> bool:
    """Did this row buy low AND sell high, by its own run's bars?"""
    e, x = r.get("entry_timing_score"), r.get("sell_high_score")
    be, bx = bars.get("entry"), bars.get("exit")
    if None in (e, x, be, bx):
        return False
    return float(e) + 1e-9 >= float(be) and float(x) + 1e-9 >= float(bx)


def rank_key_old(r):
    """The pre-timing rank: +12% weeks, then the measured best week."""
    return (1000.0 * r["weeks_12"]
            + (r["best_week"] if r["best_week"] is not None else -99.0))


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    rx = pd.Series(xs).rank().to_numpy(float)
    ry = pd.Series(ys).rank().to_numpy(float)
    mx, my = rx.mean(), ry.mean()
    den = float(np.sqrt(((rx - mx) ** 2).sum() * ((ry - my) ** 2).sum()))
    if not den:
        return None
    return round(float(((rx - mx) * (ry - my)).sum()) / den, 4)


def _ordering(rows):
    """Does the timing score order forward returns better than the old rank?

    Same rows, same tape.  Spearman of each ranking signal against the forward
    return, plus the mean forward return of each signal's top decile.  The old
    rank is the pre-timing `(weeks_12, best_week)` key.
    """
    rs = [r for r in rows if clean(r) and r.get("timing_score") is not None]
    if len(rs) < 3:
        return {"n": len(rs)}
    fwd = [r["forward_ret_pct"] for r in rs]
    dec = max(1, len(rs) // 10)

    def top_mean(key):
        top = sorted(rs, key=key, reverse=True)[:dec]
        return round(sum(r["forward_ret_pct"] for r in top) / len(top), 3)

    return {
        "n": len(rs), "top_decile_rows": dec,
        "old_rank_definition": "weeks_12 then best_week (pre-timing rank_key)",
        "new_rank_definition": "weeks_12, then timing_score, then best_week",
        "spearman_timing_vs_forward": _spearman(
            [r["timing_score"] for r in rs], fwd),
        "spearman_entry_vs_forward": _spearman(
            [r["entry_timing_score"] for r in rs], fwd),
        "spearman_exit_vs_forward": _spearman(
            [r["sell_high_score"] for r in rs], fwd),
        "spearman_old_rank_vs_forward": _spearman(
            [rank_key_old(r) for r in rs], fwd),
        "spearman_timing_vs_per_trade": _spearman(
            [r["timing_score"] for r in rs],
            [r["per_trade_points"] or 0.0 for r in rs]),
        "spearman_entry_vs_per_trade": _spearman(
            [r["entry_timing_score"] for r in rs],
            [r["per_trade_points"] or 0.0 for r in rs]),
        "spearman_old_rank_vs_per_trade": _spearman(
            [rank_key_old(r) for r in rs],
            [r["per_trade_points"] or 0.0 for r in rs]),
        "top_decile_forward_timing": top_mean(lambda r: r["timing_score"]),
        "top_decile_forward_old": top_mean(rank_key_old),
        "mean_forward": round(sum(fwd) / len(fwd), 3),
    }


def _gate(ext, rows, promoted):
    """If nothing promoted, name and quantify the one gate that forbids it."""
    clean_rows = [r for r in rows if clean(r)]
    if promoted:
        return {"binding_gate": None, "promoted": len(promoted),
                "clean_rows": len(clean_rows)}
    best_week = (max((r["best_week"] if r["best_week"] is not None else -99.0)
                     for r in clean_rows) if clean_rows else None)
    top = (max(clean_rows, key=lambda r: r["forward_ret_pct"])
           if clean_rows else None)
    need = (TARGET / top["per_trade_points"]
            if top and top["per_trade_points"] else None)
    return {
        "binding_gate": ("a +12% week while every forward window is evidence "
                         "(no death anywhere in the chain)"),
        "clean_rows": len(clean_rows),
        "max_weeks_12": max((r["weeks_12"] for r in clean_rows), default=0),
        "best_week_pct": round(best_week, 3) if best_week is not None else None,
        "finalists": len(ext),
        "finalists_surviving": sum(1 for r in ext if clean(r)),
        "finalists_died_newest": sum(1 for r in ext if r["died_newest"]),
        "finalists_with_12pct_week": sum(1 for r in ext if r["weeks_12"] >= 1),
        "best_row": top["id"] if top else None,
        "best_row_forward_pct": top["forward_ret_pct"] if top else None,
        "best_row_best_week_pct": top["best_week"] if top else None,
        "best_row_per_trade_points": top["per_trade_points"] if top else None,
        "best_row_trades_per_week": top["trades_per_week"] if top else None,
        "trades_per_week_needed_for_12pct": (round(need, 1) if need else None),
        "multiple_of_current": (round(need / top["trades_per_week"], 2)
                                if need and top and top["trades_per_week"]
                                else None),
    }


def main() -> int:
    t = Timing()
    print(f"contest {len(t.contest)} assets, 1% universe {len(t.fill.pairs)} "
          f"pairs, book cap ${BOOK_CAP:,.0f}, "
          f"gap allowance ${GAP_ALLOW:,.0f}; pool {len(t.pool)} policies; "
          f"expiry-resolved rows {len(t.base)}", flush=True)
    rows = []
    for pid in t.pool:
        for em in ENTRY_MAX:
            for mv in MOVERS:
                for sh in SELL_HIGH:
                    rows.append(t.run(pid, em, mv, sh, SCREEN_SIZE,
                                      SCREEN_SLOTS, SCREEN_BREADTH))
    base_rows = rows[:]
    dead1 = sum(1 for r in rows if not r["forward_evidence"])
    print(f"stage 1 rows {len(rows)} ({dead1} halted at a death: not evidence, "
          f"never ranked)", flush=True)
    ok = sorted([r for r in rows if clean(r)], key=rank_key, reverse=True)
    for r in ok[:8]:
        print(f"  {r['id'][:38]:38s} e{r['entry_max']} {r['mover']:7s} "
              f"sh={r['sell_high']} fwd {r['forward_ret_pct']:+.2f}% "
              f"wk12 {r['weeks_12']} best {r['best_week']:+.2f}% "
              f"timing {r['timing_score']} (entry {r['entry_timing_score']} "
              f"exit {r['sell_high_score']} bank {r['bank_rate']}) "
              f"win {r['win_rate']} pt {r['per_trade_points']}", flush=True)

    # stage 1b: sell-high exit arms (bank at N x risk, trim at rollover)
    arms = []
    for r in ok[:EXIT_STAGE]:
        for bank, trim in EXIT_ARMS:
            if bank is None and not trim:
                continue          # the stage-1 row already is this arm
            arms.append(t.run(r["id"], r["entry_max"], r["mover"],
                              r["sell_high"], r["size"], r["slots"],
                              r["breadth"], bank, trim))
    rows.extend(arms)
    print(f"stage 1b rows {len(arms)} (exit arms on "
          f"{min(len(ok), EXIT_STAGE)} leaders)", flush=True)
    for r in sorted([x for x in arms if clean(x)], key=rank_key,
                    reverse=True)[:5]:
        print(f"  arm {r['id'][:32]:32s} bank={r['bank_r']} trim="
              f"{r['trim_rollover']} fwd {r['forward_ret_pct']:+.2f}% "
              f"bank_rate {r['bank_rate']} timing {r['timing_score']}",
              flush=True)

    # stage 2: concurrency on the timing leaders, carrying their exit arm
    lead, seen = [], set()
    for r in sorted([x for x in rows if clean(x)], key=rank_key, reverse=True):
        k = (t.fam(r["id"]), r["entry_max"], r["mover"], r["sell_high"],
             r["bank_r"], r["trim_rollover"])
        if k in seen:
            continue
        seen.add(k)
        lead.append(r)
        if len(lead) >= 10:
            break
    stage2 = []
    for r in lead:
        for size, slots in CONCURRENCY:
            for n in BREADTHS:
                stage2.append(t.run(r["id"], r["entry_max"], r["mover"],
                                    r["sell_high"], size, slots, n,
                                    r["bank_r"], r["trim_rollover"]))
    rows.extend(stage2)
    print(f"stage 2 rows {len(stage2)}", flush=True)

    # stage 2b: THE CEILING TEST.  Rank the clean stage-1/2 rows by expected
    # weekly pace (points per trade x trades per week) and re-price the leaders
    # at books sized to the account's own cash - the only book limit the spot
    # contest actually imposes.  Same death line, same evidence rules.
    pace, seen_pace = [], set()
    for r in sorted([x for x in rows if clean(x)],
                    key=lambda x: ((x["per_trade_points"] or 0.0)
                                   * (x["trades_per_week"] or 0.0)),
                    reverse=True):
        k = (t.fam(r["id"]), r["entry_max"], r["mover"], r["sell_high"],
             r["bank_r"], r["trim_rollover"])
        if k in seen_pace:
            continue
        seen_pace.add(k)
        pace.append(r)
        if len(pace) >= STRETCH_STAGE:
            break
    stretch = []
    for r in pace:
        for size, slots in STRETCH:
            stretch.append(t.run(r["id"], r["entry_max"], r["mover"],
                                 r["sell_high"], size, slots, 12,
                                 r["bank_r"], r["trim_rollover"]))
    rows.extend(stretch)
    ok_stretch = [x for x in stretch if clean(x)]
    print(f"stage 2b rows {len(stretch)} (books up to ${CASH_CAP:,.0f} = 100% "
          f"of the account; {len(ok_stretch)} clean, "
          f"{sum(1 for x in stretch if not x['forward_evidence'])} halted)",
          flush=True)
    for r in sorted(ok_stretch, key=rank_key, reverse=True)[:6]:
        print(f"  ceiling {r['id'][:32]:32s} {r['size']:.1%}x{r['slots']} "
              f"n{r['breadth']} bank={r['bank_r']} trim={r['trim_rollover']} "
              f"book ${r['book_usd']:,.0f} fwd {r['forward_ret_pct']:+.2f}% "
              f"wk12 {r['weeks_12']} best {r['best_week']:+.2f}% "
              f"pace {r['per_trade_points'] * r['trades_per_week']:+.2f}%/wk",
              flush=True)

    # stage 2c: the sell-high arms AT the cash ceiling.  A 100% book can reach
    # a +12% week on the shipped exits, so the bank/trim arms must be priced
    # there too - banking the gain and refusing to carry a winner across a
    # window is exactly what should keep such a book alive in the newest week.
    fams, seen_fam = [], set()
    for r in sorted([x for x in rows if clean(x)],
                    key=lambda x: ((x["per_trade_points"] or 0.0)
                                   * (x["trades_per_week"] or 0.0)),
                    reverse=True):
        k = t.fam(r["id"])
        if k in seen_fam:
            continue
        seen_fam.add(k)
        fams.append(r)
        if len(fams) >= 6:
            break
    seen_runs = {(x["id"], x["entry_max"], x["mover"], x["sell_high"],
                  x["size"], x["slots"], x["breadth"], x["bank_r"],
                  x["trim_rollover"]) for x in rows}
    ceiling_arms = []
    for r in fams:
        for size, slots in STRETCH:
            for bank, trim in EXIT_ARMS:
                k = (r["id"], r["entry_max"], r["mover"], r["sell_high"],
                     size, slots, 12, bank, trim)
                if k in seen_runs:
                    continue
                seen_runs.add(k)
                ceiling_arms.append(t.run(r["id"], r["entry_max"],
                                          r["mover"], r["sell_high"], size,
                                          slots, 12, bank, trim))
    rows.extend(ceiling_arms)
    ok_ceiling = [x for x in ceiling_arms if clean(x)]
    print(f"stage 2c rows {len(ceiling_arms)} (all exit arms at 100% books; "
          f"{len(ok_ceiling)} clean, "
          f"{sum(1 for x in ceiling_arms if x['weeks_12'] >= 1)} with a +12% "
          f"week before the newest-window test)", flush=True)
    for r in sorted([x for x in ceiling_arms if clean(x)],
                    key=lambda x: (x["weeks_12"], x["best_week"] or -99.0),
                    reverse=True)[:6]:
        print(f"  ceiling-arm {r['id'][:28]:28s} {r['size']:.1%}x{r['slots']} "
              f"bank={r['bank_r']} trim={r['trim_rollover']} "
              f"fwd {r['forward_ret_pct']:+.2f}% wk12 {r['weeks_12']} "
              f"best {r['best_week']:+.2f}%", flush=True)

    # stage 3: newest cached window as its own failure test
    ee.WINDOWS = list(ee.WINDOWS) + [EXT_WINDOW]
    t.end = date.fromisoformat(EXT_WINDOW[1])
    t.reset_weeks()
    def _fin_key(r):
        return (t.fam(r["id"]), r["entry_max"], r["mover"], r["sell_high"],
                r["size"], r["slots"], r["breadth"], r["bank_r"],
                r["trim_rollover"])

    # SELECTION IS THE RANK, UNADJUSTED.  No row is hand-carried into the
    # failure test because it happened to reach a +12% week: if the exits are
    # right, the rank finds the book that survives; if they are not, the
    # no-promotion verdict is the honest answer.
    finalists, seen = [], set()
    for r in sorted([x for x in rows if clean(x)], key=rank_key, reverse=True):
        k = _fin_key(r)
        if k in seen:
            continue
        seen.add(k)
        finalists.append(r)
        if len(finalists) >= 10:
            break
    ext = [t.run(r["id"], r["entry_max"], r["mover"], r["sell_high"],
                 r["size"], r["slots"], r["breadth"], r["bank_r"],
                 r["trim_rollover"]) for r in finalists]
    print(f"stage 3 rows {len(ext)}", flush=True)

    # BASELINE ON THE SAME TAPE: the pre-timing rank, run through the same
    # newest-week failure test, so the two selection rules are comparable.
    def _dedupe_key(r):
        return (t.fam(r["id"]), r["entry_max"], r["mover"], r["sell_high"],
                r["size"], r["slots"], r["breadth"], r["bank_r"],
                r["trim_rollover"])

    old_sel, seen_old = [], set()
    for r in sorted([x for x in base_rows if clean(x)],
                    key=lambda x: (x["weeks_12"],
                                   x["best_week"] if x["best_week"] is not None
                                   else -99.0,
                                   x["forward_ret_pct"]),
                    reverse=True):
        k = _dedupe_key(r)
        if k in seen_old:
            continue
        seen_old.add(k)
        old_sel.append(r)
        if len(old_sel) >= 10:
            break
    ext_old = [t.run(r["id"], r["entry_max"], r["mover"], r["sell_high"],
                     r["size"], r["slots"], r["breadth"], r["bank_r"],
                     r["trim_rollover"]) for r in old_sel]
    old_ev = [r for r in ext_old if clean(r)]
    old_rank_baseline = {
        "rank": "weeks_12, then best_week, then forward_ret_pct "
                "(the pre-timing rank_key)",
        "finalists": len(ext_old),
        "survivors": len(old_ev),
        "deaths": sum(1 for r in ext_old if not r["forward_evidence"]),
        "died_newest": sum(1 for r in ext_old if r["died_newest"]),
        "mean_forward_pct": (round(sum(r["forward_ret_pct"] for r in old_ev)
                                   / len(old_ev), 3) if old_ev else None),
        "best_week_pct": (round(max((r["best_week"] or -99.0)
                                   for r in old_ev), 3) if old_ev else None),
        "rows": [{"id": r["id"], "forward_ret_pct": r["forward_ret_pct"],
                  "best_week": r["best_week"], "forward_evidence":
                  r["forward_evidence"], "died_newest": r["died_newest"],
                  "timing_score": r["timing_score"]} for r in ext_old],
    }
    print(f"old-rank baseline: {old_rank_baseline['survivors']}/"
          f"{old_rank_baseline['finalists']} survive the newest week, "
          f"mean forward {old_rank_baseline['mean_forward_pct']}", flush=True)

    def fwd_txt(r):
        return (f"{r['forward_ret_pct']:+.2f}%"
                if r["forward_ret_pct"] is not None
                else f"n/a (died {r['dead_at_window']})")

    def last_txt(r):
        for v in reversed(r["window_rets"]):
            if v is not None:
                return f"{v:+.2f}%"
        return "n/a"

    for r in ext:
        print(f"  {r['id'][:36]:36s} e{r['entry_max']} {r['mover']:7s} "
              f"sh={r['sell_high']} bank={r['bank_r']} trim={r['trim_rollover']} "
              f"{r['size']:.0%}x{r['slots']} n{r['breadth']} "
              f"fwd {fwd_txt(r)} ext {last_txt(r)} died_newest "
              f"{r['died_newest']} evidence {r['forward_evidence']} "
              f"wk12 {r['weeks_12']} timing {r['timing_score']} "
              f"win {r['win_rate']}", flush=True)

    promoted_all = [r for r in ext if clean(r) and r["weeks_12"] >= 1]
    # THE CONTENDER SET: rows that can win the contest.  When none has yet, the
    # timing-first shortlist is still named (labelled as such) instead of the
    # tournament returning "no champion" and the live path having nothing to
    # read - the mission needs a ranked, timed contender at every run.
    contenders = promoted_all or [r for r in rows if clean(r)]
    bars = timing_bars(contenders)
    promoted = [r for r in promoted_all if timing_ok(r, bars)]
    champion_status = "promoted"
    if promoted:
        champion = max(promoted, key=rank_key)
    else:
        shortlist = [r for r in contenders if timing_ok(r, bars)]
        champion = max(shortlist, key=rank_key) if shortlist else None
        champion_status = ("timing-first shortlist (no +12% week on this tape)"
                           if shortlist else "none")
    print(f"champion timing bars: entry {bars['entry']} exit {bars['exit']} "
          f"({len(promoted_all)} rows cleared +12%/{len(promoted)} also bought "
          f"low and sold high) -> {champion_status}", flush=True)
    ordering = _ordering(base_rows)
    gate = _gate(ext, rows, promoted)
    clean_rows = [r for r in rows if clean(r)]
    gate.update({
        "shipped_guard_usd": round(BOOK_CAP, 2),
        "shipped_guard_share_of_account": round(BOOK_CAP / CHAIN, 4),
        "guard_is_self_imposed": True,
        "guard_source": ("contest_account.stress_book_cap_usd: "
                         "remaining death-line buffer x 0.50 / 3% gap "
                         "(a user rule, not a Kraken rule)"),
        "platform_rules": {"death_line_pct": 3.0, "target_pct": TARGET,
                           "cost_rt_pct": 0.08, "leverage": "none (spot)"},
        "cash_ceiling_usd": round(CASH_CAP, 2),
        "liquidity": {
            "rule": ("order size <= 1% of the pair's median bar dollars, and "
                     "its measured spread_table round trip under the 0.09% "
                     "gross edge (unmeasured refused)"),
            "gate_counts": {
                v: sum(1 for g in t.cost_gate.values()
                       if g["verdict"] == v)
                for v in ("ok", "thin", "refuse", "unmeasured")},
            "pairs_at_orders": {
                f"{o:,.0f}": len(t.pairs_for(o))
                for o in sorted({CHAIN * s for s, _ in STRETCH + CONCURRENCY})},
            "cost_measured_pairs": sum(1 for g in t.cost_gate.values()
                                       if g["measured"]),
            "cost_pairs_in_scope": len(t.cost_gate),
            "cost_days": int(t.spread.get("days_seen") or 0),
        },
        "risk_cap_pct": RISK_CAP,
        "stand_down_pct": STAND_DOWN,
        "max_book_tested_usd": round(max((r["book_usd"] for r in rows),
                                         default=0.0), 2),
        "stretch_arms": [{"size": sz, "slots": sl} for sz, sl in STRETCH],
        "stretch_rows": len(stretch),
        "stretch_clean": len(ok_stretch),
        "stretch_deaths": sum(1 for r in stretch
                              if not r["forward_evidence"]),
        "best_week_any_clean_book": round(
            max((r["best_week"] if r["best_week"] is not None else -99.0)
                for r in clean_rows), 3) if clean_rows else None,
        "clean_rows_at_full_book": sum(
            1 for r in clean_rows
            if r["book_usd"] >= CASH_CAP * 0.99),
        "clean_12pct_weeks": [r["id"] for r in clean_rows
                              if r["weeks_12"] >= 1][:10],
    })
    print(f"ordering vs forward return: timing {ordering.get('spearman_timing_vs_forward')} "
          f"entry {ordering.get('spearman_entry_vs_forward')} "
          f"exit {ordering.get('spearman_exit_vs_forward')} "
          f"old {ordering.get('spearman_old_rank_vs_forward')} "
          f"(n={ordering.get('n')})", flush=True)
    out = {
        "tape": t.tape,
        "ceiling_tested": (
            "books up to 100% of the account (the cash limit a spot contest "
            "imposes); the $5,000 gap-stress cap is our own guard and is "
            "recorded per row as guard_self"),
        "evidence_policy": (
            "window 1 is in-sample and never ranked; a window where the chain "
            "touched the death line halts it, so every later window is emitted "
            "with evidence=false, no trades and no return, and a dead row has "
            "forward_ret_pct=null so it cannot enter a ranking"),
        "timing_model": {
            "entry_weights": {"low": ep.TIMING_W_LOW, "dip": ep.TIMING_W_DIP,
                              "weak": ep.TIMING_W_WEAK},
            "dip_full_pct": ep.TIMING_DIP_FULL_PCT,
            "weak_full_pct": ep.TIMING_WEAK_FULL_PCT,
            "rank_mix": {"entry": TIMING_SHARE,
                         "exit": round(1.0 - TIMING_SHARE, 2)},
            "entry_measured_on": "the policy's forward signals (after every "
                                 "policy gate), not the slot-capped subset",
            "exit_bank_reasons": list(ep.EXIT_BANK_REASONS),
            "rank_key": "weeks_12, then timing_score, then best_week, then "
                        "forward_ret_pct",
        },
        "exit_arms": [{"bank_r": b, "trim_rollover": tr}
                      for b, tr in EXIT_ARMS],
        "ordering": ordering,
        "gate": gate,
        "old_rank_baseline": old_rank_baseline,
        "setup": {
            "contest_pairs": len(CONTEST),
            "scope": ("contest-only: base rows carry the whole tape so ranks "
                      "stay comparable, but universes, cost gates, mover "
                      "terciles and trade counts are restricted to "
                      "TradingConfig.trading_pairs"),
            "universe_pairs": len(t.fill.pairs),
            "liquidity_rule": ("per book: size x $10,000 must be at most "
                               f"{ep.UNIVERSE_PARTICIPATION:.0%} of the pair's "
                               "median bar dollars, and the pair's measured "
                               "spread_table round trip must be under the "
                               "0.09% gross edge (unmeasured pairs refused)"),
            "participation": ep.UNIVERSE_PARTICIPATION,
            "participation_label": t.fill.describe(),
            "book_cap_usd": BOOK_CAP, "gap_allowance_usd": GAP_ALLOW,
            "gap_pct": GAP_PCT,
            "entry_max": list(ENTRY_MAX), "movers": list(MOVERS),
            "sell_high": list(SELL_HIGH),
            "bank_r": list(BANK_R), "trim_rollover": list(TRIM),
            "exit_stage": EXIT_STAGE,
            "screen": {"size": SCREEN_SIZE, "slots": SCREEN_SLOTS,
                       "breadth": SCREEN_BREADTH},
            "concurrency": [{"size": s, "slots": n} for s, n in CONCURRENCY],
            "breadths": list(BREADTHS),
            "windows": [list(w) for w in ee.WINDOWS],
            "bar_minutes": t.fill.bar_minutes,
            "tape": t.tape,
            "weeks": [f"{a}->{b}" for a, b in t.weeks],
            "baselines": {"per_trade_points": 0.0976,
                          "median_win_rate": 0.318, "best_win_rate": 0.59},
        },
        "stage1_rows": rows[:],
        "finalists": ext,
        "promoted": promoted,
        "promoted_before_timing": len(promoted_all),
        "champion_status": champion_status,
        "champion_timing_bars": bars,
        "champion": champion,
        "registry_untouched": not promoted,
    }
    (ROOT / "scratch" / "edge_timing_tournament.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    # TAPE LEDGER: every run records the data it stood on, so a later run can
    # say "different tape" instead of mistaking new bars for a regression.
    ledger = ROOT / "scratch" / "edge_tapes.json"
    hist = (json.loads(ledger.read_text(encoding="utf-8"))
            if ledger.exists() else [])
    hist.append({"run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "artifact": "edge_timing_tournament.json", **t.tape})
    ledger.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    print(f"tape {t.tape['max_ts']} ({t.tape['bars']} bars); ledger "
          f"{len(hist)} run(s)", flush=True)
    print("wrote edge_timing_tournament.json; promoted", len(promoted),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
