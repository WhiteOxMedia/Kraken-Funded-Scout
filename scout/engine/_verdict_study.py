"""_verdict_study.py — the per-asset verdict ledger, priced over ALL history.

Grades every closed trade in contest_trades.jsonl with discipline's REAL
_asset_verdict, peaks computed from the ohlcv_cache.db candle store (entry
time parsed from the exit-reason note when present). Ranks which verdict
class costs the most, then simulates the trail dial (CONTEST_TRAIL_PCT
family) at the trade level: for each trade, walk the bars with a P% trail
off the running peak and compare the hypothetical exit to the actual one.
Burial-side costs come from popup_suppressions.jsonl graded the same way
(did the buried pair reach its goal after the gate said no?).

All prices from the local candle cache; no live calls. RT cost 0.08% both
legs (contest_account's close math).
"""
import json
import re
import sqlite3
import datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

import discipline as dc

RT_COST = 0.0008
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
DB = str(_ROOT / "ohlcv_cache.db")
_DB_URI = _Path(DB).as_uri()


def _db():
    """A fresh READ-ONLY connection per call (Sep 22): the scanner owns
    ohlcv_cache.db and writes candles continuously — the old shared
    read handle collided with the writer's commit window and the
    resulting OperationalError made summary() silently return None
    (the plate's headline flickered). mode=ro never writes; timeout=2
    waits out a commit instead of failing instantly."""
    return sqlite3.connect(f"{_DB_URI}?mode=ro", uri=True, timeout=2.0)
LOOKBACK_MS = 120 * 60 * 1000        # if entry time unknown: 2h before exit
AFTER_MS = 240 * 60 * 1000           # post-exit run window (4h)


def _sold_ms(raw):
    """sold_at string -> epoch ms with the naive-stamp rule (Sep 28):
    the writer emits aware Denver stamps; a naive stamp is legacy or
    foreign and normalizes to DENVER wall time (ZoneInfo America/
    Denver), never the machine's local zone. _protected_extension_gate's
    journal key normalizes the same way - the ex_ms->reason join stays
    machine-proof by construction (fixing one side alone would break
    the join for naive rows)."""
    ts = datetime.datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("America/Denver"))
    return int(ts.timestamp() * 1000)


def _supp_run_ms(raw):
    """suppressions.ts -> epoch ms through the SAME naive-stamp rule
    (Sep 28, the burial side was the last bare fromisoformat().
    timestamp() instance): the suppression writer emits aware Denver
    stamps, so a naive ts is legacy/foreign and normalizes to DENVER
    wall time — never the machine's local zone. Same machine-proof
    reasoning as _sold_ms: identical code on both sides keeps the
    study self-consistent everywhere; machine-local would key the
    reach windows a whole hour off across DST eras."""
    return _sold_ms(raw)

# ── THE NIGHTLY DIAGNOSTIC LAYER (Sep 21): the study is no longer a
# scratch script — the cooldown review cites it, the scanner writes its
# report to logs/ at the 20:00 boundary, and the console reads the
# summary live. Same house shape as tidebar_weekly's driver: report_now
# never raises, the newest section prepends, and a failed write is a
# missing report, never a broken review.
from pathlib import Path as _Path
REPORT_PATH = _Path(__file__).parent / "logs" / "verdict_cost_report.md"
# The data ledgers too (Sep 22): the study is imported by the console,
# the scanner and the cooldown review — whatever each caller's working
# directory is, the study must grade THE book, not "whatever jsonl the
# cwd happens to hold". Module-relative like REPORT_PATH.
TRADES_PATH = _ROOT / "contest_trades.jsonl"
SUPPRESSIONS_PATH = _ROOT / "popup_suppressions.jsonl"


def summary():
    """The ranked verdict classes over the whole closed-trade ledger:
    [(verdict, n, usd_left), ...] costliest first, or None when the
    ledger cannot be graded. The ONE number the review and the console
    cite — computed here so there is a single source of truth."""
    try:
        graded = grade_trades()
        if not graded:
            return None
        by = {}
        for g in graded:
            k = str(g.get("verdict") or "?")
            n, usd = by.get(k, (0, 0.0))
            by[k] = (n + 1, usd + float(g.get("left") or 0.0))
        out = [(k, n, round(u, 2)) for k, (n, u) in by.items()]
        out.sort(key=lambda t: -t[2])
        return out
    except Exception:
        return None


def top_line():
    """The costliest verdict class as a voice-safe clause fragment,
    or None: 'stayed too long: 65 dollars across 6 trades'."""
    s = summary()
    if not s or s[0][2] <= 0:
        return None
    name = str(s[0][0]).split(" - ")[0]
    return f"{name}: {s[0][2]:,.0f} dollars across {s[0][1]} trades"


def _prev_section_usd(text):
    """The newest section's table as {full class name: (n, usd)}, for the
    per-night delta line. Returns {} on any parse problem - a garbage or
    missing section simply produces no deltas, never a failure."""
    try:
        if not text or "###" not in text:
            return {}
        sec = text.split("###")[1]
        out = {}
        for ln in sec.split("\n"):
            ln = ln.strip()
            if not ln.startswith("|") or "---" in ln \
                    or "verdict class" in ln:
                continue
            parts = [p.strip() for p in ln.strip("|").split("|")]
            if len(parts) < 3:
                continue
            try:
                out[parts[0]] = (int(parts[1]),
                                 float(parts[2].replace(",", "")))
            except ValueError:
                continue          # the 'unreadable ledger' placeholder row
        return out
    except Exception:
        return {}


def write_report():
    """Prepend tonight's section to the report (newest first, like the
    tide-bar weekly), with a per-night delta line: which classes grew
    or shrank since the last section. Never raises - a failed write is
    a missing report, the review and console keep their live reads.
    Returns the path or None."""
    try:
        s = summary()
        graded_n = len(grade_trades()) if s is not None else 0
        today = _now_stamp()
        lines = [f"### {today} - graded {graded_n} closed trades",
                 "", "| verdict class | n | $ left |",
                 "|---|---|---|"]
        if s:
            for k, n, u in s:
                lines.append(f"| {k} | {n} | {u:,.2f} |")
        else:
            lines.append("| unreadable ledger - nothing graded "
                         "tonight | - | - |")
        prev = REPORT_PATH.read_text(encoding="utf-8") \
            if REPORT_PATH.exists() else ""
        # -- the per-night delta line (user, Sep 22): which classes grew
        # or shrank since the last section. The book is cumulative, so a
        # delta is usually the night's own contribution; a shrink means
        # a regrade moved the number. Only changed classes are named -
        # an unchanged book says so in one clause.
        try:
            _prev = _prev_section_usd(prev)
            if _prev and s:
                _now_usd = {k: u for k, n, u in s}
                _now_n = {k: n for k, n, u in s}
                _dels = []
                for k, u in _now_usd.items():
                    pn, pu = _prev.get(k, (None, None))
                    if pu is None:
                        _dels.append(f"{str(k).split(' - ')[0]} "
                                     f"+${u:,.2f} (new)")
                    elif abs(u - pu) >= 0.005 or pn != _now_n[k]:
                        _d = u - pu
                        _dels.append(
                            f"{str(k).split(' - ')[0]} "
                            f"{'+' if _d >= 0 else chr(8722)}"
                            f"${abs(_d):,.2f} (n {pn}\u2192{_now_n[k]})")
                for k, (pn, pu) in _prev.items():
                    if k not in _now_usd:
                        _dels.append(f"{str(k).split(' - ')[0]} "
                                     f"{chr(8722)}${pu:,.2f} (off the book)")
                lines.append("")
                lines.append(
                    "\u0394 since the last section: "
                    + (" \u00b7 ".join(_dels) if _dels
                       else "no change on the book"))
        except Exception:
            pass
        lines += [""]
        REPORT_PATH.parent.mkdir(exist_ok=True)
        tmp = str(REPORT_PATH) + ".tmp"
        _Path(tmp).write_text("\n".join(lines) + "\n" + prev,
                              encoding="utf-8")
        import os as _os
        _os.replace(tmp, REPORT_PATH)
        return REPORT_PATH
    except Exception:
        return None
def _now_stamp():
    import datetime as _dt
    try:
        return _dt.datetime.now(dc.MST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def peak_in(pair, lo_ms, hi_ms):
    rows = _db().execute(
        "SELECT MAX(high) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
        (pair, int(lo_ms), int(hi_ms))).fetchone()
    return rows[0] if rows and rows[0] else None


def parse_sold_min(note):
    m = re.search(r"ended\s+(?:~|≈|about\s+)?(\d+)\s*min", note or "")
    return int(m.group(1)) if m else None


def net(entry, exit_px, size):
    return (exit_px / entry - 1.0) * size - size * RT_COST


def trail_exit(pair, entry_ms, end_ms, entry, size, pct):
    """Walk bars with a P% trail off the running peak; exit at the stop
    price the first bar whose low touches it. Returns (exit_px, ts) or None."""
    stop = None
    peak = entry
    for ts, hi, lo in _db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? AND ts<=? "
            "ORDER BY ts", (pair, int(entry_ms), int(end_ms))):
        if hi > peak:
            peak = hi
            stop = peak * (1 - pct / 100.0)
        if stop is not None and lo <= stop:
            return stop, ts
    return None


def _sim(trades, P):
    """(sum delta $, n) for a fixed-width trail over a trade subset."""
    d, n = [], 0
    for g in trades:
        te = trail_exit(g["pair"], g["en_ms"], g["ex_ms"] + AFTER_MS,
                        g["entry"], g["size"], P)
        if not te:
            continue
        hyp = net(g["entry"], te[0], g["size"])
        act = net(g["entry"], g["exit"], g["size"])
        d.append(hyp - act)
        n += 1
    return (sum(d), n)


def _sim_armed(trades, P, goal_mult=0.9):
    """(sum delta $, n) for a trail that ARMS only after the peak reaches
    goal*goal_mult (the live machinery's `reached` bar) — the policy the
    scanner actually runs post-goal (Sep 21 fix)."""
    d, n = [], 0
    for g in trades:
        arm = g["entry"] * (1.0 + g["goal"] * goal_mult / 100.0)
        stop = None
        peak = g["entry"]
        te = None
        for ts, hi, lo in _db().execute(
                "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
                "AND ts<=? ORDER BY ts",
                (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + AFTER_MS))):
            if hi > peak:
                peak = hi
                if peak >= arm:
                    stop = peak * (1 - P / 100.0)
            if stop is not None and lo <= stop:
                te = (stop, ts)
                break
        if not te:
            continue
        hyp = net(g["entry"], te[0], g["size"])
        act = net(g["entry"], g["exit"], g["size"])
        d.append(hyp - act)
        n += 1
    return (sum(d), n)


def _ratchet_exit(g, thr):
    """Trail exit with a ratchet policy: thr=(usd100, usd200) tier-blind,
    "live" = the Sep-21 peak-percent re-base (2% at peak >= +1% of entry,
    1.5% at >= +2%), or None = size-scaled (2% at peak >= 2.5x size,
    1.5% at >= 5x size)."""
    pair, entry, size = g["pair"], g["entry"], g["size"]
    stop = None
    peak = entry
    for ts, hi, lo in _db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? AND ts<=? "
            "ORDER BY ts", (pair, int(g["en_ms"]), int(g["ex_ms"] + AFTER_MS))):
        if hi > peak:
            peak = hi
            pu = (peak / entry - 1.0) * size
            pp = (peak / entry - 1.0) * 100.0
            if thr == "live":
                P = 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)
            elif thr is None:
                P = 1.5 if pu >= 5 * size else (2.0 if pu >= 2.5 * size else 3.0)
            else:
                P = 1.5 if pu >= thr[1] else (2.0 if pu >= thr[0] else 3.0)
            stop = peak * (1 - P / 100.0)
        if stop is not None and lo <= stop:
            return stop, ts
    return None


def grade_trades():
    """Grade every closed trade — the study's factual core, callable from
    the nightly cooldown review (which speaks the current top verdict
    class and its dollar cost). Returns the graded list; see main() for
    the printed tables."""
    rows = [json.loads(l) for l in open(TRADES_PATH,
                                        encoding="utf-8") if l.strip()]
    applied_sizes = defaultdict(list)
    for r in rows:
        if r.get("applied"):
            applied_sizes[r["sold_at"][:10]].append(float(r["size"]))
    flat = sorted(s for v in applied_sizes.values() for s in v)
    med_all = flat[len(flat) // 2] if flat else 10000.0

    graded = []
    for r in rows:
        pair = r["pair"]
        entry, exit_px = float(r["entry"]), float(r["exit"])
        size = float(r["size"]) if r.get("applied") else \
            (sorted(applied_sizes.get(r["sold_at"][:10], [med_all]))[len(applied_sizes.get(r["sold_at"][:10], [med_all])) // 2])
        goal = float(r.get("goal_pct") or 1.5)
        realized = float(r.get("pnl_net") or 0.0)
        ex_ms = _sold_ms(r["sold_at"])
        sold_min = parse_sold_min(r.get("note")) or parse_sold_min(r.get("exit_reason"))
        if sold_min is not None:
            en_ms = ex_ms - (60 + sold_min) * 60 * 1000
            basis = "entry-parsed"
        else:
            en_ms = ex_ms - LOOKBACK_MS
            basis = "exit-window"
        hi = peak_in(pair, en_ms, ex_ms + AFTER_MS)
        if not hi:
            continue
        v, left = dc._asset_verdict(entry, exit_px, hi, goal, realized, size)
        graded.append({
            "pair": pair, "date": r["sold_at"][:10], "applied": r.get("applied"),
            "entry": entry, "exit": exit_px, "size": size, "goal": goal,
            "realized": realized, "peak": hi, "verdict": v, "left": left,
            "basis": basis, "en_ms": en_ms, "ex_ms": ex_ms,
        })
    return graded


def main():
    graded = grade_trades()

    print("=" * 78)
    print(f"GRADED {len(graded)} trades  "
          f"(entry-parsed {sum(1 for g in graded if g['basis'] == 'entry-parsed')}, "
          f"exit-window {sum(1 for g in graded if g['basis'] == 'exit-window')})")
    print("=" * 78)

    by_class = defaultdict(lambda: {"n": 0, "left": 0.0, "assets": []})
    for g in graded:
        k = g["verdict"]
        by_class[k]["n"] += 1
        by_class[k]["left"] += g["left"]
        by_class[k]["assets"].append(g)
    total_left = sum(v["left"] for v in by_class.values())
    print(f"{'verdict class':<46}{'n':>3}{'$ left':>10}")
    for k, v in sorted(by_class.items(), key=lambda kv: -kv[1]["left"]):
        print(f"{k:<46}{v['n']:>3}{v['left']:>10,.0f}")
    print(f"{'TOTAL left on the table (trades side)':<49}{total_left:>10,.0f}")

    # ── trail sensitivity: the money-leaking set vs the don't-break control ──
    print()
    print("TRAIL SIMULATION — leak set (stayed-too-long + exited-too-soon),")
    print("hypothetical P% trail exit vs the actual exit, net of RT cost.")
    print("Control = took-the-goal (a good dial must not break these):")
    leak = [g for g in graded if "stayed too long" in g["verdict"]
            or "exited too soon" in g["verdict"]]
    ctrl = [g for g in graded if "took the goal" in g["verdict"]]
    print(f"{'P%':>4} | {'leak n':>6}{'leak delta $':>14}{'per $':>8} | "
          f"{'ctrl n':>6}{'ctrl delta $':>14}")
    for P in (1.5, 2.0, 3.0, 4.0):
        d, n = _sim(leak, P)
        dc_, nc = _sim(ctrl, P)
        print(f"{P:>4} | {n:>6}{d:>14,.0f}{(d / n if n else 0):>8,.2f} | "
              f"{nc:>6}{dc_:>14,.0f}")

    print()
    print("POST-GOAL-ARMED trail (the live Sep-21 policy): the stop exists")
    print("only after the peak reaches 90% of the trade's goal — leak set vs")
    print("the took-the-goal control:")
    print(f"{'P%':>4} | {'leak n':>6}{'leak delta $':>14}{'per $':>8} | "
          f"{'ctrl n':>6}{'ctrl delta $':>14}")
    for P in (1.0, 1.5, 2.0, 3.0):
        d, n = _sim_armed(leak, P)
        dc_, nc = _sim_armed(ctrl, P)
        print(f"{P:>4} | {n:>6}{d:>14,.0f}{(d / n if n else 0):>8,.2f} | "
              f"{nc:>6}{dc_:>14,.0f}")

    # ── the ratchet: the LIVE peak-percent policy vs the retired one ──
    print()
    print("RATCHET CHECK — the LIVE Sep-21 peak-percent re-base (2% at peak")
    print(">= +1% of entry, 1.5% at >= +2%) vs the retired tier-blind +$100/+$200")
    print("and the size-scaled alternative:")
    print(f"{'policy':<30}{'engaged':>8}{'delta $ (leak)':>15}")
    for name, thr in (("LIVE +1%/+2% peak-pct", "live"),
                      ("retired +100/200 tier-blind", (100.0, 200.0)),
                      ("scaled 2.5x/5x size", None)):
        d, n = 0.0, 0
        for g in leak:
            te = _ratchet_exit(g, thr)
            if not te:
                continue
            hyp = net(g["entry"], te[0], g["size"])
            act = net(g["entry"], g["exit"], g["size"])
            d += hyp - act
            n += 1
        print(f"{name:<28}{n:>8}{d:>15,.0f}")
    # engagement proof: how often each policy would tighten at all
    # (the LIVE policy's first tighten step is peak >= +1% of entry)
    eng = {"LIVE +1%/+2%": 0, "retired +100/200": 0, "scaled 2.5x/5x": 0}
    for g in graded:
        pp = (g["peak"] / g["entry"] - 1.0) * 100.0
        peak_usd = (g["peak"] / g["entry"] - 1.0) * g["size"]
        if pp >= 1.0:
            eng["LIVE +1%/+2%"] += 1
        if peak_usd >= 100.0:
            eng["retired +100/200"] += 1
        if peak_usd >= 2.5 * g["size"]:
            eng["scaled 2.5x/5x"] += 1
    print(f"  ratchet engagements across all {len(graded)} trades: {eng}")

    # ── burial side: candidate quality vs outcome, by gate ──────────
    print()
    print("BURIAL SIDE — candidate quality (quick score) vs whether the pair")
    print("reached a goal-size run (+1.5%) in the window after the gate said")
    print("no. High-quality burials are the mispriced ones:")
    sups = [json.loads(l) for l in open(SUPPRESSIONS_PATH,
                                        encoding="utf-8") if l.strip()]
    gate_stats = defaultdict(lambda: {"n": 0, "reach": 0, "reach3": 0,
                                      "hi_q": 0, "hi_q_reach": 0})
    for s in sups:
        pair = s.get("pair")
        if not pair:
            continue
        ts = s.get("ts", "")
        try:
            t_ms = _supp_run_ms(ts)
        except Exception:
            continue
        end_ms = t_ms + 7 * 3600 * 1000
        pre = _db().execute("SELECT close FROM ohlcv WHERE pair=? AND ts<? "
                           "ORDER BY ts DESC LIMIT 1", (pair, t_ms)).fetchone()
        hi = peak_in(pair, t_ms, end_ms) if t_ms else None
        if not hi or not pre or not pre[0]:
            continue
        entry = pre[0]
        run_pct = (hi / entry - 1.0) * 100.0
        q = float(s.get("quick") or 0.0)
        fl = float(s.get("floor") or 0.0)
        st_ = gate_stats[s.get("gate") or "?"]
        st_["n"] += 1
        if run_pct >= 1.5:
            st_["reach"] += 1
        if run_pct >= 3.0:
            st_["reach3"] += 1
        if q >= fl:
            st_["hi_q"] += 1
            if run_pct >= 3.0:
                st_["hi_q_reach"] += 1
    print(f"{'gate':<22}{'buried':>7}{'r1.5':>6}{'r3.0':>6}{'q>=fl':>6}{'q+r3':>6}")
    for k, v in sorted(gate_stats.items(), key=lambda kv: -kv[1]["n"]):
        print(f"{k:<22}{v['n']:>7}{v['reach']:>6}{v['reach3']:>6}"
              f"{v['hi_q']:>6}{v['hi_q_reach']:>6}")


if __name__ == "__main__":
    main()
