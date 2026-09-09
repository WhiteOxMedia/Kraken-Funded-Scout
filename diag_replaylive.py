# diag_replaylive.py -- THE REPLAY-LIVE DIAG (user spec, Sep 8: "extend
# the replay engine to grade the recorded live snapshots (champion +
# mover) against the deep candle archive as a first pass before any
# refactor").
#
# The Sure Thing got a candle-driven detector, a download, and a replay
# engine. The OTHER funnels are not replayable yet -- their gate chains
# live inline in the scanner -- but their VERDICTS were recorded with
# prices and timestamps, and the verdicts replay fine:
#
#   POPUPS    signals.db (action='buy', executed_at IS NOT NULL, with
#             entry/take_profit/stop): sources scout_auto (the champion
#             QC-passed popup), scout_mover, scout_gainer, scout_dca,
#             bot_strategy (the bot's own retired self-strategy: stamped
#             Sep 9 with the exit engine's config rails via bot_levels(),
#             after the fact for the 77 rows that shipped tp-null;
#             graded under the popup 120m clock -- the bot itself held
#             without a clock, so treat its verdicts as the popup-style
#             read).
#             Graded at the recorded TP: did price touch the take before
#             the stop, in-window? (the goal-call graded on its own
#             terms -- exactly what the funnel promised)
#   BURIALS   popup_suppressions.jsonl (every gate that blocked a
#             would-be popup, with the fire-time quick score): graded
#             what the gate BURIED -- would the candidate have paid its
#             goal call within the window? A gate is earning its keep
#             when what it buries would mostly NOT have paid.
#
# The candle source is the deep 5m archive in surething_data/ (Kraken-
# native spans + the Coinbase deep fill, ~30 days), falling back to the
# live public OHLC for pairs/times the archive does not cover (report
# states the split honestly).
#
# Grading core is PURE (load_archive, outcome_of, grade_popup,
# grade_burial, summarize) -- test_surethingdiag-style suite grades it
# on synthetic candles; the report section just reads its output.
#
# Run: .venv\\Scripts\\python.exe diag_replaylive.py  ->  _replaylive_check.txt
import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

lines = []

ARCHIVE = Path(__file__).parent / "surething_data" / "kraken"
OUT_PATH = "_replaylive_check.txt"
WINDOW_MIN = 120.0        # the goal window the popups promised (the clock)
BURIAL_GOAL = 1.5         # burials carry no goal call; graded at the default

# The bot_strategy rails (user rule, Sep 9): the bot's exit engine runs
# config.stop_loss_pct=5.0 / config.take_profit_pct=10.0 against the
# filled price, so those are the levels its own-strategy rows promise --
# stamped at insert (bot repo bot_engine.py) and backfilled for the
# historical tp-null rows. ONE definition: the backfill and the tests
# grade the same numbers the graders see.
BOT_STOP_PCT = 5.0
BOT_TAKE_PCT = 10.0


def bot_levels(entry):
    """The config-rail tp/sl a bot_strategy row promises off its entry."""
    e = float(entry)
    return (round(e * (1.0 - BOT_STOP_PCT / 100.0), 8),
            round(e * (1.0 + BOT_TAKE_PCT / 100.0), 8))


def out(s=""):
    print(s)
    lines.append(s)


def _median(vals):
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _fmt_pct(x):
    return f"{x:.0%}" if x is not None else "-"


# ── 1. THE ARCHIVE (pure loader) ───────────────────────────────────────


def load_archive(archive_dir=None):
    """surething_data/kraken/*.feather -> {pair: [ts_ms, o, h, l, c, v]}
    sorted by ts. The Kraken-native file wins per pair; the .coinbase
    file extends the SPAN backwards (deep history the live API forgot).
    Pure file reads; missing dir -> {}. Pair key: 'XMR_USD-5m.feather'
    -> 'XMR/USD'."""
    d = Path(archive_dir) if archive_dir else ARCHIVE
    if not d.exists():
        return {}
    try:
        import pandas as pd
    except Exception:
        return {}
    out = {}
    for f in sorted(d.glob("*.feather")):
        pair = None
        stem = f.name
        if stem.endswith("-5m.coinbase.feather"):
            pair = stem[:-len("-5m.coinbase.feather")].replace("_", "/")
            deep = True
        elif stem.endswith("-5m.feather"):
            pair = stem[:-len("-5m.feather")].replace("_", "/")
            deep = False
        if not pair:
            continue
        try:
            df = __import__("pandas").read_feather(f)
        except Exception:
            continue
        cols = {c.lower() for c in df.columns}
        if not {"date", "open", "high", "low", "close"} <= cols:
            continue
        dcol = "date" if "date" in df.columns else df.columns[0]
        dates = df[dcol]
        rows = []
        for i, rec in enumerate(df.itertuples(index=False)):
            g = rec._asdict()
            try:
                _v = int(getattr(g[dcol], "value"))
                # pandas Timestamp .value is NS since epoch (feather
                # datetime[us] files read back through ns); the threshold
                # separates ns (19 digits) from us (16 digits) -- both
                # normalize to MILLISECONDS, the archive's unit
                ts = _v // 1_000_000 if _v > 10**17 else _v // 1000
            except Exception:
                ts = int(dates.iloc[i].timestamp() * 1000)
            rows.append([ts, float(g["open"]), float(g["high"]),
                         float(g["low"]), float(g["close"]),
                         float(g.get("volume", 0.0) or 0.0)])
        rows.sort(key=lambda r: r[0])
        prev = out.get(pair)
        if prev is None or deep:
            if prev is None:
                out[pair] = rows
            else:
                # merge: deep file fills older spans, native wins overlaps
                seen = {r[0] for r in prev}
                out[pair] = sorted(prev + [r for r in rows
                                           if r[0] not in seen],
                                   key=lambda r: r[0])
        else:
            seen = {r[0] for r in rows}
            out[pair] = sorted(rows + [r for r in prev
                                       if r[0] not in seen],
                               key=lambda r: r[0])
    return out


def candles_for(archive, pair, t0_s, before_min=120.0, after_min=WINDOW_MIN):
    """The candle window around the event, archive first. Returns
    (rows, source) where source is 'archive' | 'none'."""
    rows = archive.get(pair)
    if not rows:
        return [], "none"
    lo = int((t0_s - before_min * 60) * 1000)
    hi = int((t0_s + after_min * 60) * 1000)
    w = [r for r in rows if lo <= r[0] <= hi]
    return w, ("archive" if w else "none")


# ── 2. THE OUTCOME (pure -- candles in, verdict out) ──────────────────


def outcome_of(candles, t0_s, entry, tp, sl, win_min=WINDOW_MIN,
               now_s=None):
    """Walk forward from the event: first touch wins (the honest
    5m-bar approximation -- if tp and sl sit inside one candle, the
    candle is ambiguous and grades 'ambig' rather than a win).
    Verdicts: tp | sl | timeout | open | no-data.
    'open' (Sure Thing diag's convention): the window has not fully
    elapsed yet (now_s given, still inside it) and nothing touched --
    NOT a timeout, the verdict is simply not ripe.
    Entry is only a sanity floor, NOT required -- burials carry a
    hypothetical goal price and no entry, and the walk needs neither
    (the pure tp-vs-candle-high comparison below)."""
    if entry is not None and entry <= 0:
        return "no-data"
    if tp is None and sl is None:
        return "no-data"
    t0_ms = int(t0_s * 1000)
    fwd = [r for r in candles if r[0] >= t0_ms]
    if not fwd:
        return "no-data"
    hi_ms = t0_ms + int(win_min * 60 * 1000)
    for r in fwd:
        # past the deadline -> the touches before it are the whole story;
        # candles EXIST after t0, so this is a timeout, not no-data
        if r[0] > hi_ms:
            break
        _ts, _o, h, l, _c, _v = r
        hit_tp = tp is not None and tp > 0 and h >= tp
        hit_sl = sl is not None and sl > 0 and l <= sl
        if hit_tp and hit_sl:
            return "ambig"
        if hit_tp:
            return "tp"
        if hit_sl:
            return "sl"
    if now_s is not None and now_s < t0_s + win_min * 60:
        return "open"
    return "timeout"


# ── 3. THE RECORDS (store readers feeding the pure core) ──────────────


def load_popups(db_path=None):
    """signals.db: the recorded popups with their own goal call."""
    p = Path(db_path) if db_path else Path("signals.db")
    if not p.exists():
        return []
    try:
        sc = sqlite3.connect(str(p))
    except Exception:
        return []
    sc.row_factory = sqlite3.Row
    try:
        rows = sc.execute("""
            SELECT pair, source, entry_price, execution_price,
                   take_profit, stop_loss,
                   position_size_usd, executed_at, confidence
            FROM signals
            WHERE action='buy' AND executed_at IS NOT NULL
              AND entry_price IS NOT NULL AND entry_price > 0
            ORDER BY executed_at
        """).fetchall()
        out = []
        for r in rows:
            try:
                t0 = datetime.fromisoformat(r["executed_at"]).timestamp()
            except Exception:
                continue
            out.append({"pair": r["pair"], "source": r["source"] or "?",
                        "entry": float(r["entry_price"]),
                        "exec": (float(r["execution_price"])
                                 if r["execution_price"] else None),
                        "tp": (float(r["take_profit"])
                               if r["take_profit"] else None),
                        "sl": (float(r["stop_loss"])
                               if r["stop_loss"] else None),
                        "spend": (float(r["position_size_usd"] or 0.0)),
                        "conf": (float(r["confidence"] or 0.0)),
                        "ts": r["executed_at"], "t0": t0})
        return out
    except sqlite3.Error:
        return []
    finally:
        try:
            sc.close()
        except Exception:
            pass


def load_burials(path=None):
    """popup_suppressions.jsonl: the gates' verdicts on would-be popups."""
    p = Path(path) if path else Path("popup_suppressions.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("pair") or not r.get("gate"):
            continue
        try:
            t0 = datetime.fromisoformat(str(r.get("ts"))).timestamp()
        except Exception:
            continue
        out.append({"pair": r["pair"], "gate": r["gate"],
                    "quick": r.get("quick"), "detail": r.get("detail"),
                    "ts": r.get("ts"), "t0": t0})
    return out


def burial_goal_price(quick, candles, t0_s):
    """The burial's hypothetical goal: 1.5% above the last close before
    the event (what the candidate was trading at when the gate buried
    it). No candles -> None."""
    pre = [r for r in candles if r[0] <= int(t0_s * 1000)]
    if not pre:
        return None
    return pre[-1][4] * (1.0 + BURIAL_GOAL / 100.0)


# ── 3b. THE LIVE FALLBACK: covering the pairs the archive forgot ──────


_LIVE_TTL_S = 90.0        # one cache per (pair, day); rebuilt on demand
_live_cache = {}


def _live_rows(pair, t0_s):
    """Public Kraken 5m OHLC around the event (hindsight_scorer's own
    fetcher), CACHED per pair so one 262-event run hits the network
    67 times, not 609. Returns [] on any failure (fail honest)."""
    day = int(t0_s // 86400)
    key = (pair, day)
    hit = _live_cache.get(key)
    if hit is not None and time.time() - hit[1] < _LIVE_TTL_S:
        return hit[0]
    rows = []
    try:
        import requests
        kpair = pair.replace("/", "")
        r = requests.get("https://api.kraken.com/0/public/OHLC",
                         params={"pair": kpair, "interval": 5},
                         timeout=20)
        data = r.json().get("result", {})
        k = [k for k in data if k != "last"]
        raw = data[k[0]] if k else []
        # Kraken rows: [ts_s, o, h, l, c, v, wick, ...] -> archive shape
        rows = [[int(rw[0]) * 1000, float(rw[1]), float(rw[2]),
                 float(rw[3]), float(rw[4]), float(rw[6] or 0.0)]
                for rw in raw]
    except Exception:
        rows = []
    _live_cache[key] = (rows, time.time())
    return rows


def _archive_covers(rows, t0_s, win_min, now_s):
    """Does this window span the event's WHOLE promise? Two traps: rows
    that stop short of the deadline, and rows that START after the event
    (the first minutes of the promise invisible). A window still running
    (now inside it) needs rows reaching ~now instead."""
    if not rows:
        return False
    t0_ms = int(t0_s * 1000)
    deadline_ms = t0_ms + int(win_min * 60 * 1000)
    if min(r[0] for r in rows) > t0_ms:
        return False
    reach = max(r[0] for r in rows)
    if now_s is not None and now_s * 1000 < deadline_ms:
        return reach >= (now_s - 15 * 60) * 1000
    return reach >= deadline_ms


def graded_live(arch_rows, fetch, pair, t0_s, entry, tp, sl,
                win_min=WINDOW_MIN, now_s=None):
    """Grade ONE event: the archive window first. A touch (tp/sl/ambig)
    or an 'open' is OBSERVABLE from any span and accepted as-is; a
    'timeout' (or no rows at all) needs span proof -- a partial view
    cannot honestly say 'never touched' -- and falls back to the LIVE
    public OHLC fetcher (the source the hindsight scorer trusts), under
    the same rule. A fetch that fails or cannot prove the span leaves
    the event ungraded ('no-data') -- never a crash, never a premature
    timeout. The report states the archive/live split honestly."""
    v = outcome_of(arch_rows, t0_s, entry, tp, sl, win_min, now_s)
    if v not in ("no-data", "timeout") or (
            v == "timeout"
            and _archive_covers(arch_rows, t0_s, win_min, now_s)):
        return {"verdict": v, "source": "archive", "candles": len(arch_rows)}
    try:
        live = fetch(pair, t0_s) or []
    except Exception:
        live = []
    v2 = outcome_of(live, t0_s, entry, tp, sl, win_min, now_s)
    if v2 in ("tp", "sl", "ambig", "open") or (
            v2 == "timeout"
            and _archive_covers(live, t0_s, win_min, now_s)):
        return {"verdict": v2, "source": "live", "candles": len(live)}
    return {"verdict": "no-data", "source": "archive", "candles": len(live)}


def graded_burial_live(arch_rows, fetch, pair, t0_s, quick,
                       win_min=WINDOW_MIN, now_s=None):
    """The burial variant: the hypothetical tp is DERIVED from the rows
    (1.5% over the last pre-event close), so the row choice happens
    BEFORE the goal is minted. Same honesty rule as graded_live: a
    touch or 'open' is observable from any span; a 'timeout' needs span
    proof, else the LIVE rows are tried wholly (re-minting the goal
    from their pre-event close -- a short fetch minting today's close
    as an old event's goal would grade a fiction, so the span rule
    guards here too). Ungradeable everywhere -> 'no-data'."""
    rows, src = arch_rows, "archive"
    tp = burial_goal_price(quick, rows, t0_s)
    v = ("no-entry" if tp is None
         else outcome_of(rows, t0_s, None, tp, None, win_min, now_s))
    if v in ("tp", "sl", "ambig", "open") or (
            v == "timeout"
            and _archive_covers(rows, t0_s, win_min, now_s)):
        return {"verdict": v, "hypothetical_tp": tp, "source": src,
                "candles": len(rows)}
    try:
        live = fetch(pair, t0_s) or []
    except Exception:
        live = []
    tp2 = burial_goal_price(quick, live, t0_s)
    v2 = ("no-entry" if tp2 is None
          else outcome_of(live, t0_s, None, tp2, None, win_min, now_s))
    if v2 in ("tp", "sl", "ambig", "open") or (
            v2 == "timeout"
            and _archive_covers(live, t0_s, win_min, now_s)):
        return {"verdict": v2, "hypothetical_tp": tp2, "source": "live",
                "candles": len(live)}
    return {"verdict": "no-data", "hypothetical_tp": None,
            "source": "archive", "candles": len(rows)}


# ── 4. THE GRADES (pure folds) ────────────────────────────────────────


def grade_popup(p, candles):
    """A bot_strategy row shipped with tp/sl=None until Sep 9; the
    backfill stamps the config rails, and any straggler without levels
    grades no-data (a promise never written can't be graded)."""
    tp, sl = p["tp"], p["sl"]
    if tp is None and sl is None and p["source"] == "bot_strategy" \
            and p.get("entry"):
        # rail off the FILLED price when the row carries one (Copilot,
        # Sep 9: the exit engine measured the fill, not the quote)
        sl, tp = bot_levels(p.get("exec") or p["entry"])
    return {"pair": p["pair"], "source": p["source"], "ts": p["ts"],
            "verdict": outcome_of(candles, p["t0"], p["entry"],
                                  tp, sl)}


def grade_burial(b, candles):
    tp = burial_goal_price(b.get("quick"), candles, b["t0"])
    v = "no-entry" if tp is None else outcome_of(
        candles, b["t0"], None, tp, None)
    return {"pair": b["pair"], "gate": b["gate"], "ts": b["ts"],
            "quick": b.get("quick"), "hypothetical_tp": tp,
            "verdict": v}


def summarize(popups, burials, covered):
    """covered: set of pairs with archive candles. Pure fold."""
    agg = {"popups": {"n": 0, "graded": 0, "verdicts": defaultdict(int),
                      "per_source": {}},
           "burials": {"n": 0, "graded": 0, "verdicts": defaultdict(int),
                       "per_gate": {}},
           "uncovered": defaultdict(int)}
    for p in popups:
        a = agg["popups"]
        a["n"] += 1
        if p["pair"] not in covered:
            agg["uncovered"]["popup:" + p["pair"]] += 1
            continue
        v = p["verdict"]
        if v != "no-data":
            a["graded"] += 1
            a["verdicts"][v] += 1
            s = a["per_source"].setdefault(p["source"], defaultdict(int))
            s[v] += 1
    for b in burials:
        a = agg["burials"]
        a["n"] += 1
        if b["pair"] not in covered:
            agg["uncovered"]["burial:" + b["pair"]] += 1
            continue
        v = b["verdict"]
        if v not in ("no-data", "no-entry"):
            a["graded"] += 1
            a["verdicts"][v] += 1
            g = a["per_gate"].setdefault(b["gate"], defaultdict(int))
            g[v] += 1
    return agg


# ── 5. THE REPORT ─────────────────────────────────────────────────────


def run(out_path=OUT_PATH, archive_dir=None):
    out(f"THE REPLAY-LIVE DIAG  ({datetime.now():%Y-%m-%d %H:%M})")
    out("the verdicts the funnels RECORDED, graded against the deep "
        "5m archive (a first pass before the gate chains are factored "
        "into candle-driven functions)")
    archive = load_archive(archive_dir)
    out()
    out(f"archive: {len(archive)} pairs, "
        f"{sum(len(v) for v in archive.values()):,} candles")
    if archive:
        spans = [(min(r[0] for r in v), max(r[0] for r in v))
                 for v in archive.values()]
        lo = datetime.fromtimestamp(min(s[0] for s in spans) / 1000,
                                    tz=timezone.utc)
        hi = datetime.fromtimestamp(max(s[1] for s in spans) / 1000,
                                    tz=timezone.utc)
        out(f"  span: {lo:%Y-%m-%d} -> {hi:%Y-%m-%d}")
    popups = load_popups()
    burials = load_burials()
    out(f"records: {len(popups)} popups (signals.db), "
        f"{len(burials)} burials (popup_suppressions.jsonl)")
    # grade with the archive windows, falling back to the LIVE public
    # OHLC for pairs/spans the archive forgot (XMR -- the live bot's own
    # pair -- and the burial universe's non-fleet coins). The public
    # endpoint serves ~720 candles (~2.5 days); older non-archive events
    # stay ungraded and the report says so.
    now_s = time.time()
    live_popups = live_burials = 0
    for p in popups:
        c, _src = candles_for(archive, p["pair"], p["t0"])
        g = graded_live(c, _live_rows, p["pair"], p["t0"],
                        p["entry"], p["tp"], p["sl"], now_s=now_s)
        p["verdict"] = g["verdict"]
        p["graded_src"] = g["source"]
        if g["source"] == "live":
            live_popups += 1
    for b in burials:
        c, _src = candles_for(archive, b["pair"], b["t0"])
        g = graded_burial_live(c, _live_rows, b["pair"], b["t0"],
                               b.get("quick"), now_s=now_s)
        b["verdict"] = g["verdict"]
        b["hypothetical_tp"] = g["hypothetical_tp"]
        b["graded_src"] = g["source"]
        if g["source"] == "live":
            live_burials += 1
    covered = set(archive.keys()) | {
        x["pair"] for x in popups + burials if x.get("graded_src") == "live"}
    agg = summarize(popups, burials, covered)
    out()
    out("── THE POPUPS (graded at their OWN goal call: tp before sl "
        f"in {WINDOW_MIN:.0f}m) ──")
    a = agg["popups"]
    if not a["graded"]:
        out("  nothing graded (no archive overlap)")
    else:
        v = a["verdicts"]
        n = a["graded"]
        out(f"  graded {n}/{a['n']}:  TP {v['tp']} "
            f"({_fmt_pct(v['tp'] / n)})  SL {v['sl']}  timeout {v['timeout']}"
            + (f"  ambig {v['ambig']}" if v["ambig"] else "")
            + (f"  (open {v['open']} -- window still running)"
               if v["open"] else ""))
        for src, s in sorted(a["per_source"].items(),
                             key=lambda kv: -sum(kv[1].values())):
            sn = sum(s.values())
            out(f"    {src}: {sn}  tp {_fmt_pct(s['tp'] / sn)}  "
                f"sl {_fmt_pct(s['sl'] / sn)}  timeout "
                f"{_fmt_pct(s['timeout'] / sn)}")
        if live_popups:
            out(f"    (of the graded, {live_popups} came from the live "
                "public-OHLC fallback -- pairs/spans the archive forgot)")
    out()
    out("── THE BURIALS (each gate's buried candidate, graded at a "
        f"hypothetical +{BURIAL_GOAL:.1f}% in {WINDOW_MIN:.0f}m) ──")
    b = agg["burials"]
    if not b["graded"]:
        out("  nothing graded (no archive overlap)")
    else:
        out("  a gate EARNED its keep when its burials mostly would NOT "
            "have paid")
        for gate, g in sorted(b["per_gate"].items(),
                              key=lambda kv: -sum(kv[1].values())):
            gn = sum(g.values())
            out(f"    {gate}: {gn}  would-have-paid {_fmt_pct(g['tp'] / gn)}"
                f"  (sl {_fmt_pct(g['sl'] / gn)}, timeout "
                f"{_fmt_pct(g['timeout'] / gn)})")
        if live_burials:
            out(f"    (of the graded, {live_burials} came from the live "
                "public-OHLC fallback)")
    if agg["uncovered"]:
        out()
        top = sorted(agg["uncovered"].items(),
                     key=lambda kv: -kv[1])[:6]
        out(f"  (uncovered by the archive: {sum(agg['uncovered'].values())}"
            f" events -- {', '.join(f'{k} x{v}' for k, v in top)})")
    out()
    out("VERDICT: this grades the RECORDED verdicts only -- the funnels' "
        "full gate chains are not candle-replayable until factored out of "
        "the scan loop (the Sure Thing remains the only end-to-end "
        "replayable funnel). The live-OHLC fallback reaches ~720 candles "
        "(~2.5 days) back; older non-archive events stay ungraded -- the "
        "deep archive is the only deep source.")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n→ {out_path}")
    return agg


if __name__ == "__main__":
    run()
