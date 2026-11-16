"""EDGE ENGINE — the missing research-to-live loop for Kraken Scout.

The scanner can already produce candidates, size them and guard the death
line.  What it could not do is answer, with one repeatable command, *which
entry selection has positive expectancy in every contest window after the
fees and with the live exits* — and it had no registry that remembers the
answer between runs.

This module is that loop.  It is deliberately research-only: it reads the
cached public 1h candles and never writes the contest ledger, a watch row or
an order.  A policy only becomes `promoted` when the SAME entry rule closes
all three chained windows profitable, with at least 10 chained trades in
each window, the live exits/sizing, and the death line armed.  The +12%
contest target is reported separately: at live 9%/6% sizing one slot cannot
reach it in these 32-day windows, so making that the promotion gate would
promote nobody by construction.  A promotion is written to
`edge_registry.json`; the scanner reads it before every buy, while
`allow_new_risk` remains an independent refusal.

What is measured
----------------
* Every candidate is an ENTRY selection rule.  Exits, stop/target geometry,
  fees, one-position-at-a-time and the 3% death line stay exactly the shipped
  live rules.  That is the only way to tell whether a selector is useful
  without also claiming an exit improvement the scanner does not have.
* Features are computed at the signal bar's CLOSE from bars up to and
  including that bar.  The trade is resolved from the NEXT bar onward by
  `_chain_lab.Exits` (stop first on an ambiguous bar), so no future candle
  reaches a signal.
* Window 3 is held out from ranking: a candidate is screened on windows 1
  and 2 and only then reported on window 3.  A candidate is "robust" only
  when its net mean AND its 95% lower bound are positive in all three.
* The shortlist is replayed through `_chain_lab.run` with one carried
  balance: +12% inside a window is a pass, -3% from that window's start is a
  death.  Per-trade expectancy alone is not a promotion.

Usage
-----
    python -X utf8 edge_engine.py --search
    python -X utf8 edge_engine.py --search --top 10 --chain-top 8
    python -X utf8 edge_engine.py --status
    python -X utf8 edge_engine.py --watch --hours 24

Outputs `edge_engine_report.json`, `edge_engine_report.md` and
`edge_registry.json`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "ohlcv_cache.db"
REPORT = BASE / "edge_engine_report.json"
REPORT_MD = BASE / "edge_engine_report.md"
REGISTRY = BASE / "edge_registry.json"
FEE_RT_PCT = 0.08
WINDOWS = [
    ("2026-08-17", "2026-09-18"),
    ("2026-09-18", "2026-10-19"),
    ("2026-10-19", "2026-11-10"),
]
MIN_TRADES = 25
DEFAULT_SIZE_FRAC = 0.09
CHAIN_START = 10_000.0


@dataclass(frozen=True)
class Candidate:
    """One entry selector.  The rule gets a feature mapping and returns bool."""

    id: str
    label: str
    rule: Callable[[dict], bool]


@dataclass(frozen=True)
class WindowStats:
    n: int
    mean: float | None
    lower: float | None
    win_rate: float | None
    payoff: float | None
    net: float | None
    t: float | None

    @property
    def positive(self) -> bool:
        return bool(
            self.n > 0
            and self.mean is not None
            and self.mean > 0
            and self.lower is not None
            and self.lower > 0
        )


# ── data ──────────────────────────────────────────────────────────────────


def load_pairs(db: Path = DB) -> dict[str, pd.DataFrame]:
    """Read the public cache read-only, exactly as the backtest labs do."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        pairs = [r[0] for r in con.execute(
            "select distinct pair from ohlcv order by pair")]
        out: dict[str, pd.DataFrame] = {}
        for p in pairs:
            df = pd.read_sql_query(
                "select ts, open, high, low, close, volume from ohlcv "
                "where pair=? order by ts", con, params=(p,))
            if len(df) < 80:
                continue
            df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
            out[p] = df[["open", "high", "low", "close", "volume"]].astype(float)
        return out
    finally:
        con.close()


def atr14(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def cache_events(pairs: dict[str, pd.DataFrame]) -> list[tuple]:
    """The scanner's cached entry events, resolved against this cache."""
    import tempfile
    import pickle
    import backtest_12pct_compare as bt

    path = Path(tempfile.gettempdir()) / "kraken_scout_entries.pkl"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing — run backtest_12pct_compare.py once to build "
            "the cached event stream")
    raw = pickle.loads(path.read_bytes())
    return sorted(bt.resolve_events(pairs, raw), key=lambda e: e[0])


def _frame_arrays(df: pd.DataFrame, atr: pd.Series) -> dict[str, np.ndarray]:
    c, h, l, o, v = (df["close"], df["high"], df["low"], df["open"],
                     df["volume"])
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    sma200 = c.rolling(200).mean()
    high20 = h.rolling(20).max().shift(1)
    low20 = l.rolling(20).min().shift(1)
    high4 = h.rolling(4).max().shift(1)
    low20 = l.rolling(20).min().shift(1)
    low4 = l.rolling(4).min().shift(1)
    vol72 = v.rolling(72).mean()
    ret1 = c.pct_change() * 100.0
    ret4 = c.pct_change(4) * 100.0
    ret24 = c.pct_change(24) * 100.0
    ret72 = c.pct_change(72) * 100.0
    ret168 = c.pct_change(168) * 100.0
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rng = (c - low20) / (high20 - low20).replace(0, np.nan)
    # THE ROTATION IS A DENVER-LOCAL 4h BLOCK (Nov 16). The tape is UTC and the
    # contest's rotations are local (00-04 .. 20-24), so the index hour is NOT
    # the rotation. DST-aware: the same bar maps to the same rotation in August
    # (MDT) and in November (MST), which is what a rotation gate must mean.
    _idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    rot = np.asarray(_idx.tz_convert("America/Denver").hour // 4, dtype=int)
    return {
        "open": o.to_numpy(float), "high": h.to_numpy(float),
        "low": l.to_numpy(float), "close": c.to_numpy(float),
        "ema9": ema9.to_numpy(float), "ema21": ema21.to_numpy(float),
        "ema50": ema50.to_numpy(float), "sma200": sma200.to_numpy(float),
        "high20": high20.to_numpy(float), "high4": high4.to_numpy(float),
        "low20": low20.to_numpy(float), "low4": low4.to_numpy(float),
        "vol72": vol72.to_numpy(float), "ret1": ret1.to_numpy(float),
        "ret4": ret4.to_numpy(float), "ret24": ret24.to_numpy(float),
        "ret72": ret72.to_numpy(float), "ret168": ret168.to_numpy(float),
        "rsi": rsi.to_numpy(float), "rsi_prev": rsi.shift(1).to_numpy(float),
        "range_pos": rng.to_numpy(float), "atr": atr.to_numpy(float),
        "volume": v.to_numpy(float), "rot": rot,
    }


def frame_features(df: pd.DataFrame, atr: pd.Series) -> dict[str, np.ndarray]:
    """All per-pair features at once.  Pure and lookahead-free."""
    return _frame_arrays(df, atr)


def _panel_ranks(pairs: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    closes = pd.DataFrame({p: d["close"] for p, d in pairs.items()})
    ret24 = closes.pct_change(24)
    ret72 = closes.pct_change(72)
    ret168 = closes.pct_change(168)
    ema50 = closes.ewm(span=50, adjust=False).mean()
    btc = closes.get("BTC/USD")
    btc24 = (btc.pct_change(24) * 100.0 if btc is not None
             else pd.Series(0.0, index=closes.index))
    return {
        "xs24": ret24.rank(axis=1, pct=True),
        "xs72": ret72.rank(axis=1, pct=True),
        "xs168": ret168.rank(axis=1, pct=True),
        "breadth": (closes > ema50).mean(axis=1),
        "btc24": btc24,
    }


def _fnum(v, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def features_at(fa: dict[str, np.ndarray], ranks: dict[str, pd.Series],
                t, i: int) -> dict:
    """The signal-bar feature mapping.  Every value uses bars <= i."""
    c = _fnum(fa["close"][i])
    atr = _fnum(fa["atr"][i])
    e9 = _fnum(fa["ema9"][i])
    e21 = _fnum(fa["ema21"][i])
    e50 = _fnum(fa["ema50"][i])
    sma200 = _fnum(fa["sma200"][i])
    v72 = _fnum(fa["vol72"][i])
    r24 = _fnum(fa["ret24"][i])
    f = {
        "close": c, "open": _fnum(fa["open"][i]),
        "atr": atr, "atr_pct": (atr / c if c else float("nan")),
        "ema9": e9, "ema21": e21, "ema50": e50, "sma200": sma200,
        "ema_up": bool(e9 > e21),
        "above200": bool(c > sma200) if math.isfinite(sma200) else False,
        "ret1": _fnum(fa["ret1"][i]),
        "ret4": _fnum(fa["ret4"][i]), "ret24": r24,
        "ret72": _fnum(fa["ret72"][i]),
        "ret168": _fnum(fa["ret168"][i]),
        "rsi": _fnum(fa["rsi"][i]),
        "rsi_prev": _fnum(fa["rsi_prev"][i]),
        "range_pos": _fnum(fa["range_pos"][i]),
        "vol_surge": (_fnum(fa["volume"][i]) / v72
                      if v72 and v72 > 0 else float("nan")),
        "dist_ema_atr": ((c - e9) / atr if e9 == e9 and atr else float("nan")),
        "break20": (c > _fnum(fa["high20"][i])
                    if math.isfinite(_fnum(fa["high20"][i])) else False),
        "break4": (c > _fnum(fa["high4"][i])
                   if math.isfinite(_fnum(fa["high4"][i])) else False),
        "breakdown20": (c < _fnum(fa["low20"][i])
                        if math.isfinite(_fnum(fa["low20"][i])) else False),
        "breakdown4": (c < _fnum(fa["low4"][i])
                       if math.isfinite(_fnum(fa["low4"][i])) else False),
        "btc24": _fnum(ranks["btc24"].get(t)),
        "breadth": _fnum(ranks["breadth"].get(t)),
        "xs24": _fnum(ranks["xs24"].get(t)),
        "xs72": _fnum(ranks["xs72"].get(t)),
        "xs168": _fnum(ranks["xs168"].get(t)),
        "hour": pd.Timestamp(t).hour,
        # the Denver-local rotation index 0..5; -1 when the frame predates the
        # field, so a rotation gate fails CLOSED rather than open
        "rot": int(fa["rot"][i]) if "rot" in fa else -1,
        "atr_rank": float("nan"),
    }
    return f


def build_rows(pairs: dict[str, pd.DataFrame], events: list[tuple],
               cfg=None, fee: float = FEE_RT_PCT,
               max_hold: int | None = None) -> dict:
    """Resolve every cached entry once and attach the signal-bar features.

    `net` is the live exit's net return in percent, including the round trip.
    A row with no resolvable exit is omitted; it never becomes a fake zero.
    """
    import _chain_lab as cl
    from config import TradingConfig

    cfg = cfg or TradingConfig()
    atrs = {p: atr14(df) for p, df in pairs.items()}
    feats = {p: frame_features(df, atrs[p]) for p, df in pairs.items()}
    ranks = _panel_ranks(pairs)
    ex = cl.Exits(pairs, atrs, cfg,
                  max_hold=max_hold if max_hold is not None else None)
    rows = []
    for t, p, i in events:
        fa = feats.get(p)
        if fa is None or i < 200 or i >= len(fa["close"]) - 1:
            continue
        res = ex.resolve(p, i)
        if res is None:
            continue
        (j, px, why), stop, tp, entry, exit_ts = res
        if exit_ts >= pd.Timestamp(WINDOWS[-1][1], tz="UTC"):
            continue
        atr = _fnum(fa["atr"][i])
        if not (entry and entry > 0 and atr and atr > 0):
            continue
        f = features_at(fa, ranks, t, i)
        rows.append({"ts": pd.Timestamp(t), "pair": p, "i": i,
                     "net": (px / entry - 1.0) * 100.0 - fee,
                     "exit": why, "hold": j - i, "f": f})
    return {"rows": rows, "atrs": atrs, "exits": ex,
            "pairs": pairs, "features": feats, "ranks": ranks}


def fill_atr_rank(rows: list[dict]) -> None:
    """Within each timestamp, rank atr_pct 0..1.  Uses only that bar."""
    by_ts: dict[pd.Timestamp, list[dict]] = {}
    for r in rows:
        by_ts.setdefault(r["ts"], []).append(r)
    for bucket in by_ts.values():
        vals = sorted(v["f"]["atr_pct"] for v in bucket
                      if math.isfinite(v["f"]["atr_pct"]))
        if len(vals) < 3:
            continue
        for r in bucket:
            v = r["f"]["atr_pct"]
            if not math.isfinite(v):
                continue
            lo = sum(1 for x in vals if x < v)
            eq = sum(1 for x in vals if x == v)
            r["f"]["atr_rank"] = (lo + 0.5 * eq) / len(vals)


# ── candidate space ───────────────────────────────────────────────────────


def _is(x, lo=-math.inf, hi=math.inf) -> bool:
    return math.isfinite(x) and lo <= x <= hi


def candidate_specs() -> list[Candidate]:
    """The hypothesis grid.  Every rule uses only features known at the close."""
    regimes: dict[str, Callable[[dict], bool]] = {
        "all": lambda f: True,
        "btc_up": lambda f: f["btc24"] > 0.0,
        "btc_strong": lambda f: f["btc24"] > 1.5,
        "btc_notdown": lambda f: f["btc24"] > -1.0,
        "breadth_hi": lambda f: f["breadth"] > 0.50,
        "breadth_lo": lambda f: f["breadth"] < 0.45,
        "risk_on": lambda f: f["btc24"] > 0.0 and f["breadth"] > 0.50,
    }
    entries: dict[str, Callable[[dict], bool]] = {
        "trend": lambda f: f["ema_up"] and f["close"] > f["ema21"],
        "trend200": lambda f: f["ema_up"] and f["above200"],
        "pullback": lambda f: (
            f["ema_up"] and f["close"] > f["ema50"] and f["ret1"] < 0
            and f["close"] <= f["ema9"] and _is(f["rsi"], hi=55)),
        "break20": lambda f: (
            f["break20"] and _is(f["vol_surge"], 1.2)),
        "ignition": lambda f: (
            _is(f["ret24"], 3.0) and _is(f["vol_surge"], 1.5)
            and f["close"] > f["ema9"]),
        "dip": lambda f: (
            _is(f["ret24"], hi=-4.0) and f["close"] > f["open"]
            and _is(f["rsi"], hi=45)),
        "leader": lambda f: (
            _is(f["xs72"], 0.80) and f["ema_up"] and f["ret1"] < 0),
        "lag": lambda f: (
            _is(f["xs168"], hi=0.20) and f["break4"]
            and _is(f["rsi"], 45)),
        "squeeze": lambda f: (
            _is(f["atr_rank"], hi=0.35) and f["break20"]
            and _is(f["vol_surge"], 1.2)),
        "volbreak": lambda f: (
            _is(f["vol_surge"], 2.0) and f["break20"]),
        "range_reclaim": lambda f: (
            _is(f["range_pos"], hi=0.20) and f["close"] > f["open"]
            and _is(f["rsi"], 40)),
        "rsi_reclaim": lambda f: (
            _is(f["rsi_prev"], hi=35) and _is(f["rsi"], 40)
            and f["close"] > f["open"]),
    }
    out: list[Candidate] = []
    for rk, rf in regimes.items():
        for ek, ef in entries.items():
            out.append(Candidate(
                id=f"{rk}.{ek}", label=f"{rk} + {ek}",
                rule=lambda f, rf=rf, ef=ef: bool(rf(f) and ef(f))))
    # The two halves on their own are controls: a regime that only works
    # when paired with one entry is not a regime edge.
    for rk, rf in regimes.items():
        out.append(Candidate(id=f"market.{rk}", label=f"market: {rk}",
                             rule=rf))
    for ek, ef in entries.items():
        out.append(Candidate(id=f"entry.{ek}", label=f"entry: {ek}",
                             rule=ef))
    return out


# ── statistics ────────────────────────────────────────────────────────────


def _stats(vals: Iterable[float]) -> WindowStats:
    x = np.array([float(v) for v in vals if math.isfinite(float(v))])
    n = len(x)
    if not n:
        return WindowStats(0, None, None, None, None, None, None)
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    wins = x[x > 0]
    losses = x[x <= 0]
    payoff = (float(wins.mean()) / abs(float(losses.mean()))
              if len(wins) and len(losses) else None)
    return WindowStats(
        n=n, mean=round(mean, 4),
        lower=round(mean - 1.96 * se, 4) if n > 1 else None,
        win_rate=round(len(wins) / n, 4),
        payoff=round(payoff, 3) if payoff is not None else None,
        net=round(float(x.sum()), 2),
        t=round(mean / se, 3) if se > 0 else None,
    )


def split_rows(rows: list[dict]) -> list[list[dict]]:
    bounds = [pd.Timestamp(a, tz="UTC") for a, _ in WINDOWS] + [
        pd.Timestamp(WINDOWS[-1][1], tz="UTC")]
    out = [[], [], []]
    for r in rows:
        for k in range(3):
            if bounds[k] <= r["ts"] < bounds[k + 1]:
                out[k].append(r)
                break
    return out


def _bh_q(values: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values for one-sided positive tests."""
    m = len(values)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: values[i])
    q = [1.0] * m
    prev = 1.0
    for rank, i in enumerate(reversed(order), 1):
        raw = values[i] * m / (m - rank + 1)
        prev = min(prev, raw)
        q[i] = min(1.0, prev)
    return q


def _p_value(t: float | None) -> float:
    if t is None:
        return 1.0
    # normal upper tail; the sample sizes here are far above the small-n
    # regime, and the lower bound is reported alongside it.
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def evaluate_candidates(rows: list[dict],
                        candidates: list[Candidate] | None = None) -> list[dict]:
    candidates = candidates or candidate_specs()
    split = split_rows(rows)
    out = []
    for cand in candidates:
        selected = [r for r in rows if cand.rule(r["f"])]
        s = [_stats([r["net"] for r in w if cand.rule(r["f"])])
             for w in split]
        if not s[0].n or not s[1].n or not s[2].n:
            continue
        # rank only on the first two windows; w3 is the reported holdout
        if s[0].n < MIN_TRADES or s[1].n < MIN_TRADES:
            continue
        score = min(s[0].mean or -99, s[1].mean or -99)
        out.append({
            "id": cand.id, "label": cand.label,
            "n": len(selected), "w1": s[0].__dict__, "w2": s[1].__dict__,
            "w3": s[2].__dict__, "score": round(score, 4),
            "screen_positive": bool(s[0].mean and s[0].mean > 0
                                    and s[1].mean and s[1].mean > 0),
            "robust": bool(s[0].positive and s[1].positive and s[2].positive),
            "p": _p_value(s[2].t),
        })
    q = _bh_q([r["p"] for r in out])
    for r, qv in zip(out, q):
        r["q"] = round(qv, 4)
    out.sort(key=lambda r: (not r["robust"], not r["screen_positive"],
                            -r["score"], -(r["w3"]["mean"] or -99)))
    return out


# ── chained contest replay + promotion ────────────────────────────────────


def chain_result(candidate: Candidate, rows: list[dict], data: dict,
                 cfg=None, size_frac: float = DEFAULT_SIZE_FRAC) -> dict:
    """Replay the selector through one carried balance and the live exits."""
    import _chain_lab as cl
    from config import TradingConfig

    cfg = cfg or TradingConfig()
    events = [(r["ts"], r["pair"], r["i"]) for r in rows
              if candidate.rule(r["f"])]
    if not events:
        return {"id": candidate.id, "windows": [], "passes": 0,
                "died": False, "final": CHAIN_START,
                "status": "rejected", "reason": "no events"}
    res, final = cl.run(
        data["pairs"], data["atrs"], events, cfg, data["exits"],
        slots=1, size_frac=size_frac, rot_guard=True,
        fee_rt_pct=FEE_RT_PCT, start_balance=CHAIN_START,
        anchor_balance=CHAIN_START, **cl.LIVE_ENTRY)
    passes = sum(1 for w in res if w.get("passed"))
    died = any(w.get("died") for w in res)
    status, reason = classify_chain(passes, died, res)
    return {
        "id": candidate.id, "label": candidate.label,
        "windows": res, "passes": passes, "died": bool(died),
        "final": round(float(final), 2), "status": status,
        "status_reason": reason,
    }


def classify_chain(passes: int, died: bool, windows: list[dict]) -> tuple[str, str]:
    """The promotion gate, pure so the rule is testable without a replay."""
    if died:
        return "rejected", "a chained window touched the death line"
    if passes == 3:
        return "promoted", "all three contest windows passed with no death"
    if passes == 2:
        return "watch", "two of three windows passed; needs forward evidence"
    if passes == 1:
        return "watch", "one window passed; not enough for live risk"
    return "rejected", "no contest window passed"


# ── registry/report ───────────────────────────────────────────────────────


def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, doc) -> None:
    tmp = str(path) + f".tmp.{os.getpid()}"
    Path(tmp).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def update_registry(report: dict, path: Path = REGISTRY) -> dict:
    """Merge one search into the durable candidate registry.

    A promoted policy stays visible.  A later run that no longer clears all
    three windows DEMOTES it: the registry is evidence, not a trophy case.
    Nothing here touches the live scanner.
    """
    old = _read_json(path, {})
    candidates = old.get("candidates") if isinstance(old.get("candidates"), dict) else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = report.get("outcomes") or report.get("candidates", [])
    current_ids = {c.get("id") for c in source}
    for cid in list(candidates):
        if cid not in current_ids:
            del candidates[cid]
    for c in source:
        prev = candidates.get(c["id"], {})
        ch = c.get("chain") or {}
        candidates[c["id"]] = {
            "id": c["id"], "label": c.get("label"),
            "status": c.get("status", "rejected"),
            "status_reason": c.get("status_reason", ""),
            "spec": c.get("spec"),
            "screen": {k: c.get(k) for k in
                       ("n", "score", "robust", "screen_positive", "q", "p")},
            "w1": (c.get("w1") or {}).get("mean"),
            "w2": (c.get("w2") or {}).get("mean"),
            "w3": (c.get("w3") or {}).get("mean"),
            "chain": {k: ch.get(k) for k in
                      ("status", "passes", "contest_passes", "died", "final",
                       "final_return_pct", "status_reason", "worst_window",
                       "worst_headroom", "death_window", "trades", "wins",
                       "losses", "win_rate", "net", "expectancy_usd",
                       "expectancy_pct", "max_drawdown_usd",
                       "max_drawdown_pct", "min_headroom_pct", "worst_day_usd",
                       "worst_day_pct_of_start", "min_chain_trades")},
            "stress": c.get("stress"),
            "first_seen": prev.get("first_seen", now),
            "last_seen": now,
        }
        if c.get("status") == "promoted":
            candidates[c["id"]]["chain"]["windows"] = ch.get("windows")
    # deterministic live rank: final balance, then passes, then id
    promoted = [v for v in candidates.values() if v.get("status") == "promoted"]
    promoted.sort(key=lambda v: (
        -(v.get("chain", {}).get("final") or 0),
        -(v.get("chain", {}).get("passes") or 0),
        v.get("id", "")))
    doc = {
        "schema": 2,
        "updated_at": now,
        "dataset": report.get("dataset"),
        "active": promoted[0]["id"] if promoted else None,
        "status_counts": report.get("status_counts"),
        "candidates": candidates,
    }
    _write_json(path, doc)
    return doc


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.3f}%"


def render_markdown(report: dict) -> str:
    d = report.get("dataset", {})
    counts = report.get("status_counts") or {}
    lines = [
        "# EDGE ENGINE — full policy search",
        "",
        f"- Candles: **{d.get('candles', 0):,}** across **{d.get('pairs', 0)}** pairs",
        f"- Data through: `{d.get('through', '?')}` "
        f"({d.get('data_age_hours', '?')}h old"
        f"{' — STALE' if d.get('stale') else ''})",
        f"- Policy space: **{report.get('policy_space', 0):,}** "
        f"(entry x long-only x exit x regime x ATR x hour x size)",
        f"- Evaluated: **{report.get('tested', 0):,}** "
        f"(decision-grade **{report.get('decision_grade', report.get('tested', 0)):,}**, "
        f"insufficient window trades **{report.get('insufficient', 0):,}**)",
        f"- Promoted / watch / research / rejected: "
        f"**{counts.get('promoted', 0)} / {counts.get('watch', 0)} / "
        f"{counts.get('research', 0)} / {counts.get('rejected', 0)}**",
        f"- Chained decision-grade policies: **{report.get('chain_run', 0):,}**",
        f"- Robust (positive net + lower bound in all 3 windows): "
        f"**{report.get('robust', 0)}**",
        "",
        "Ranking uses windows 1–2 only; window 3 is the reported holdout.",
        "",
        "## Promotion gate",
        "",
        f"A policy is promoted only when **all three** chained windows close "
        f"above their start balance, each window takes at least "
        f"**{report.get('min_chain_trades', 0)} trades**, max peak-to-trough "
        f"drawdown stays under **3%**, the death-line cushion stays at least "
        f"**1% of the window start**, no window touches the 3% death line, "
        f"and the windows 1-2 signal screen is positive. The +12% contest "
        f"target is reported "
        f"separately (`contest_passes`), never used to manufacture or withhold "
        f"a promotion: at the live one-slot 9%/6% sizing it is arithmetically "
        f"out of reach in these windows even when the edge is real.",
        "",
        "## Shortlist",
        "",
        "| policy | side/exit/size | gates | w1 | w2 | w3 | robust | chain | fail |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for c in report.get("candidates", [])[:20]:
        w1, w2, w3 = c.get("w1") or {}, c.get("w2") or {}, c.get("w3") or {}
        chain = c.get("chain") or {}
        gates = f"{c.get('regime')}/{c.get('vol_gate')}/{c.get('hour_gate')}"
        lines.append(
            f"| `{c['id']}` | {c.get('side')}/{c.get('exit')}/"
            f"{int(float(c.get('size_frac') or 0) * 100)}% | {gates} "
            f"| {_pct(w1.get('mean'))} | {_pct(w2.get('mean'))} "
            f"| {_pct(w3.get('mean'))} | {'YES' if c.get('robust') else 'no'} "
            f"| {chain.get('status', 'not run')} prof {chain.get('passes', 0)}/3 "
            f"· contest {chain.get('contest_passes', 0)}/3 "
            f"· n {chain.get('trades', 0)} "
            f"| {c.get('fail_window') or c.get('death_window') or '—'} |")
    lines += ["", "## Verdict", ""]
    if report.get("promoted"):
        lines.append(
            "Long-only policies with three profitable chained windows, enough "
            "trades, a positive discovery screen and every risk limit intact "
            "are `promoted` in `edge_registry.json`. The scanner reads the "
            "registry; `allow_new_risk` remains a separate gate and is never "
            "overridden by a promotion.")
        promoted = [c for c in report.get("candidates", [])
                    if c.get("status") == "promoted"]
        for c in promoted:
            ch = c.get("chain") or {}
            ws = ch.get("windows") or []
            window_txt = " · ".join(
                f"{w.get('window')}: {_pct(w.get('return_pct'))} "
                f"({w.get('trades')} trades, DD {w.get('max_drawdown_pct')}%)"
                for w in ws)
            lines += [
                "",
                f"### `{c['id']}`",
                "",
                f"- Trades: **{ch.get('trades', 0)}** "
                f"({ch.get('wins', 0)}W / {ch.get('losses', 0)}L, "
                f"win rate **{float(ch.get('win_rate') or 0) * 100:.1f}%**)",
                f"- Expectancy: **${ch.get('expectancy_usd', 0):+.2f}/trade** "
                f"({float(ch.get('expectancy_pct') or 0):+.3f}% of position)",
                f"- Max drawdown: **{ch.get('max_drawdown_pct')}%** "
                f"(${ch.get('max_drawdown_usd', 0):,.2f}); "
                f"worst day **${ch.get('worst_day_usd', 0):+,.2f}** "
                f"({ch.get('worst_day_pct_of_start')}% of start)",
                f"- Chained result: **${ch.get('final', 0):,.2f}** "
                f"({float(ch.get('final_return_pct') or 0):+.2f}%), "
                f"worst death-line headroom **${ch.get('worst_headroom', 0):,.2f}** "
                f"({ch.get('min_headroom_pct')}% of start)",
                f"- Contest +12% target: **{ch.get('contest_passes', 0)}/3** "
                f"(reported, not the promotion gate)",
                f"- Windows: {window_txt}",
            ]
    else:
        lines.append(
            "No long policy earned three profitable chained windows with "
            "enough trades and no death. The gate held: screen-positive "
            "failures are `research` only, and no live size follows from them.")
        if report.get("next_surface"):
            lines += ["", f"**Narrowed next surface:** {report['next_surface']}."]
        worst = [c for c in report.get("candidates", [])
                 if c.get("chain", {}).get("status") in ("rejected", "watch")][:6]
        if worst:
            lines += ["", "### Where the failures break", ""]
            for c in worst:
                ch = c.get("chain") or {}
                lines.append(
                    f"- `{c['id']}`: {ch.get('status')} {ch.get('passes', 0)}/3, "
                    f"death window {ch.get('death_window') or 'none'}, "
                    f"worst headroom ${ch.get('worst_headroom', '—')} "
                    f"({ch.get('worst_window', '—')})")
    return "\n".join(lines) + "\n"


def search(db: Path = DB, top: int = 20, chain_top: int = 8,
           min_trades: int = MIN_TRADES) -> dict:
    """Search the full policy surface and write a fresh registry + report.

    Only a long policy with three profitable chained windows, >=10 chained
    trades per window and no death is `promoted`.  Every decision-grade policy
    is chained, not just the top slice.  The surface is long-only by contract;
    there are no short policies to replay, serialize, or promote.
    """
    global MIN_TRADES
    import edge_policy as ep
    from config import TradingConfig

    MIN_TRADES = max(1, int(min_trades))
    pairs = load_pairs(db)
    rows = ep.build_base_rows(pairs)
    if not rows:
        raise RuntimeError("no entry-selector bars in the cache")
    resolver = ep.Resolver(pairs, TradingConfig())
    # The same rows, filled at the next bar's open: the arm that measured the
    # promotion rule's noise band (Nov 16).  A promotion must survive it.
    resolver_open = ep.Resolver(pairs, TradingConfig(), fill="next_open")
    policies = ep.all_policies()
    print(f"policy rows={len(rows)} pairs={len(pairs)} "
          f"surface={len(policies)}", flush=True)
    evals = ep.evaluate_policies(rows, resolver, policies)
    print(f"evaluated={len(evals)}", flush=True)
    if not evals:
        raise RuntimeError("policy surface produced no evaluable candidates")
    # Chain EVERY decision-grade policy.  The old top-slice-only shortlist
    # silently dropped 162 of the 336 decision-grade candidates as
    # "outside chain shortlist" — they could never promote no matter how
    # good their evidence was.  Ranking may choose the display order, but
    # it must never decide who gets a promotion hearing.
    shortlist = [e for e in evals if not e.get("insufficient")]
    for _n, e in enumerate(shortlist, 1):
        pol = ep.policy_from_spec(e["spec"])
        if pol is None:
            e["chain"] = {"status": "rejected", "passes": 0,
                          "died": False,
                          "status_reason": "spec is not executable"}
            e["status"] = "rejected"
            e["status_reason"] = "spec is not executable"
            continue
        ch = ep.chain_result(pol, ep.selected_for(pol, rows, resolver))
        if _n % 10 == 0 or _n == len(shortlist):
            print(f"chain {_n}/{len(shortlist)} {e['id']} "
                  f"{ch['status']} {ch['passes']}/3 "
                  f"(contest {ch.get('contest_passes', 0)}/3)", flush=True)
        e["chain"] = ch
        e["status"] = ch["status"]
        e["status_reason"] = ch["status_reason"]
        if ch["status"] == "promoted" and not e.get("screen_positive"):
            e["status"] = "watch"
            e["status_reason"] = (
                "chain windows were profitable, but the windows 1-2 signal "
                "screen was not both positive; forward paper only")
        if e["status"] == "promoted":
            stress = ep.stress_passes(pol, rows, resolver_open)
            e["stress"] = {"fill": "next_open", "passes": stress.get("passes"),
                           "final": stress.get("final"),
                           "windows": [w.get("return_pct")
                                       for w in stress.get("windows") or []]}
            if int(stress.get("passes") or 0) < 3:
                e["status"] = "watch"
                e["status_reason"] = (
                    "cleared the window margin at the signal-bar close but not "
                    "under next-open fills - the promotion is inside the "
                    "fill-timing noise band")
                e["fail_window"] = (e.get("fail_window") or
                                    "next-open fill stress")
        e["fail_window"] = next((w["window"] for w in ch["windows"]
                                 if not w.get("qualified")), None)
        e["worst_headroom"] = ch.get("worst_headroom")
        e["death_window"] = ch.get("death_window")
    for e in evals:
        if "chain" not in e:
            e["chain"] = {"status": "not_run", "passes": 0, "died": False,
                          "status_reason": "outside chain shortlist"}
            e["status"] = "rejected"
            e["status_reason"] = "outside chain shortlist"
            e["fail_window"] = None
            e["worst_headroom"] = None
            e["death_window"] = None
            if e.get("insufficient"):
                e["status_reason"] = "insufficient window trades"
    # Screen-positive but not chain-cleared policies are forward-paper only.
    research = [e for e in evals
                if e.get("screen_positive") and e.get("status") == "rejected"]
    for e in research[:6]:
        e["status"] = "research"
        e["status_reason"] = (
            "screen-positive on windows 1-2, but the chained contest did not "
            "clear; forward paper only")
    for e in evals:
        e.pop("selected", None)
    rank = {"promoted": 0, "watch": 1, "research": 2, "rejected": 3}
    evals.sort(key=lambda e: (
        rank.get(e.get("status"), 9), not e.get("robust"),
        not e.get("screen_positive"),
        -(e.get("chain", {}).get("final") or 0), -e.get("score", -99),
        e["id"]))
    counts = {s: sum(1 for e in evals if e["status"] == s) for s in
              ("promoted", "watch", "research", "rejected")}
    # The narrowed next surface: what the screen-positive failures agree on.
    next_surface = ""
    pos = [e for e in evals if e.get("screen_positive")][:12]
    if pos:
        from collections import Counter
        groups = Counter((e["side"], e["exit"], e["regime"], e["entry"])
                         for e in pos)
        key, n = groups.most_common(1)[0]
        next_surface = (f"{n}/12 screen-positive policies share {key[0]} "
                        f"{key[3]} with {key[1]} exits in {key[2]} regime; "
                        "test that family with wider entry thresholds")
    through = max(d.index.max() for d in pairs.values())
    age_h = (pd.Timestamp.now(tz="UTC") - through).total_seconds() / 3600.0
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "candles": int(sum(len(d) for d in pairs.values())),
            "pairs": len(pairs), "event_rows": len(rows),
            "through": str(through),
            "data_age_hours": round(age_h, 1),
            "stale": bool(age_h > 48.0),
            "fee_rt_pct": FEE_RT_PCT, "windows": WINDOWS,
            "size_fracs": list(ep.SIZE_FRACS),
        },
        "policy_space": len(policies), "tested": len(evals),
        "decision_grade": sum(1 for e in evals if not e.get("insufficient")),
        "chain_run": len(shortlist),
        "min_chain_trades": ep.MIN_CHAIN_TRADES,
        "insufficient": sum(1 for e in evals if e.get("insufficient")),
        "robust": sum(1 for e in evals if e["robust"]),
        "promoted": counts["promoted"], "status_counts": counts,
        "next_surface": next_surface,
        "candidates": evals[:max(top, chain_top)],
        "outcomes": evals,
    }
    _write_json(REPORT, report)
    update_registry(report)
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    return report


def status(path: Path = REGISTRY) -> dict:
    doc = _read_json(path, {})
    if not doc:
        return {"active": None, "candidates": {}, "note": "no search yet"}
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kraken Scout edge engine")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--chain-top", type=int, default=8)
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    args = ap.parse_args(argv)
    if args.status:
        print(json.dumps(status(), indent=1)[:12000])
        return 0
    if not (args.search or args.watch):
        print("use --search, --status or --watch")
        return 2
    while True:
        try:
            rep = search(top=args.top, chain_top=args.chain_top,
                         min_trades=args.min_trades)
            print(json.dumps({
                "tested": rep["tested"], "robust": rep["robust"],
                "promoted": rep["promoted"],
                "active": status().get("active"),
                "report": str(REPORT),
            }, indent=1), flush=True)
            for c in rep["candidates"][:args.top]:
                ch = c.get("chain") or {}
                print(f"{c['id']:28s} w1 {_pct(c['w1'].get('mean'))} "
                      f"w2 {_pct(c['w2'].get('mean'))} "
                      f"w3 {_pct(c['w3'].get('mean'))} "
                      f"robust={c['robust']} chain={ch.get('status')} "
                      f"({ch.get('passes', 0)}/3)", flush=True)
        except Exception:
            if not args.watch:
                raise
            import traceback
            traceback.print_exc()
        if not args.watch:
            return 0
        time.sleep(max(300.0, args.hours * 3600.0))


if __name__ == "__main__":
    raise SystemExit(main())
