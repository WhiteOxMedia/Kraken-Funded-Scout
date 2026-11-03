"""THE DEEP STORE BUILDER (Sep 10) — extends the coinbase-deep candle
store from the 54-pair 30-day accident to ALL live pairs over the full
window the scanner has been caching.

THE INSIGHT: ohlcv_cache.db (the live engine's own candle cache) is a
complete 1h archive — 101 pairs, exact hourly cadence, zero gaps, 49+
days and growing every day the scanner runs. The 7.34 GB Kraken archive
download died on Google Drive quota (raw/_download.failed); it is not
needed. This builder merges, per pair:

  1. ohlcv_cache.db     — the primary (the venue the gates actually trade)
  2. Kraken 1h OHLC     — a fresh top-up when the cache's last candle is
                          stale (detached runs, a dead scanner)
  3. *.coinbase.feather — the Sep 8 5m fetches, resampled to 1h, used
                          ONLY to fill spans the cache does not reach
                          (the cache wins on overlap — same-venue truth)

and writes surething_data/kraken/{PAIR}-1h.feather (date/o/h/l/c/v,
UTC). The 1h grain is exactly what the fleet study and the tide learner
sample; the 5m files stay untouched for diag_replaylive.

Run:  .venv/Scripts/python.exe -X utf8 build_deepstore.py [--limit N]
Rerun anytime — the store grows with the live cache.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CACHE_DB = ROOT / "ohlcv_cache.db"
OUT_DIR = ROOT / "surething_data" / "kraken"
STALE_H = 2.0        # cache last candle older than this -> top up from Kraken
MIN_HOURS = 200      # a pair needs this many hourly rows to earn a file
COINBASE_FILL = True # fill cache-less spans from the 5m coinbase feathers


def _log(msg: str) -> None:
    print(msg, flush=True)


def _cache_frame() -> pd.DataFrame:
    """The live cache as (pair, ts_ms, o/h/l/c/v) long frame."""
    cc = sqlite3.connect(CACHE_DB)
    try:
        return pd.read_sql(
            "SELECT pair, ts, open, high, low, close, volume FROM ohlcv "
            "ORDER BY pair, ts", cc)
    finally:
        cc.close()


def _fetch_kraken_1h(exchange, pair: str) -> pd.DataFrame:
    """Recent Kraken 1h candles (the top-up source) — [] on any error."""
    try:
        rows = exchange.fetch_ohlcv(pair, timeframe="1h", limit=720)
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low",
                                     "close", "volume"])
    df["pair"] = pair
    return df


def _coinbase_1h(pair: str) -> pd.DataFrame:
    """The Sep 8 coinbase 5m fetch resampled to 1h, ts in ms — [] when
    absent."""
    fn = OUT_DIR / f"{pair.replace('/', '_')}-5m.coinbase.feather"
    if not fn.exists():
        return pd.DataFrame()
    try:
        df = pd.read_feather(fn)
    except Exception:
        return pd.DataFrame()
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("date") or cols.get("ts")
    if tcol is None or "close" not in cols:
        return pd.DataFrame()
    s = df[[tcol, "open", "high", "low", "close", "volume"]].copy()
    s.columns = ["ts", "open", "high", "low", "close", "volume"]
    s["ts"] = pd.to_datetime(s["ts"], utc=True)
    s = s.set_index("ts").sort_index()
    agg = s.resample("1h").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum")).dropna(subset=["close"])
    agg = agg.reset_index()
    # -> ms (feather dates may carry us/ns units; force ns first)
    agg["ts"] = (agg["ts"].astype("datetime64[ns, UTC]")
                 .astype("int64") // 10**6)
    agg["pair"] = pair
    return agg


def build(limit: int | None = None) -> dict:
    """Merge every source per pair and write the -1h.feather store.
    Returns the summary dict (also written to _deepstore_build.txt)."""
    t0 = time.time()
    long = _cache_frame()
    pairs = sorted(long["pair"].unique())
    if limit:
        pairs = pairs[:limit]
    _log(f"cache: {len(long):,} rows, {long['pair'].nunique()} pairs, "
         f"{pd.to_datetime(long['ts'].min(), unit='ms', utc=True)} -> "
         f"{pd.to_datetime(long['ts'].max(), unit='ms', utc=True)}")

    # fresh top-up source (one client, only if the cache is stale)
    exchange = None
    cache_max_ms = int(long["ts"].max())
    age_h = (time.time() * 1000 - cache_max_ms) / 3.6e6
    if age_h > STALE_H:
        try:
            import ccxt
            exchange = ccxt.kraken({"enableRateLimit": True})
            _log(f"cache is {age_h:.1f}h stale -> topping up from Kraken")
        except Exception as exc:
            _log(f"kraken top-up unavailable ({exc}) — cache only")

    written, skipped, notes = [], [], []
    for pair in pairs:
        rows = long[long["pair"] == pair]
        parts = [rows]
        # 2. the fresh top-up (same venue as the cache; the dedupe below
        # keeps the LATER write on overlap)
        if exchange is not None:
            fresh = _fetch_kraken_1h(exchange, pair)
            if len(fresh):
                parts.append(fresh)
        # 3. coinbase fills ONLY spans the cache does not reach
        if COINBASE_FILL:
            cb = _coinbase_1h(pair)
            if len(cb):
                cache_min_ms = int(rows["ts"].min())
                cb_early = cb[cb["ts"] < cache_min_ms]
                if len(cb_early):
                    parts.append(cb_early)
                    notes.append(f"{pair}: +{len(cb_early)}h coinbase "
                                 f"pre-cache fill")
        df = pd.concat(parts, ignore_index=True)
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        # SANITY (the 1970 lesson): a unit bug anywhere upstream lands
        # rows in 1970 — drop anything implausibly old instead of
        # letting it poison the store
        df = df[df["dt"] >= pd.Timestamp("2020-01-01", tz="UTC")]
        df = df.sort_values("dt").drop_duplicates(subset="dt", keep="last")
        df = df.set_index("dt").resample("1h").agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum")).dropna(subset=["close"])
        if len(df) < MIN_HOURS:
            skipped.append((pair, len(df)))
            continue
        out = df.reset_index().rename(columns={"dt": "date"})
        fn = OUT_DIR / f"{pair.replace('/', '_')}-1h.feather"
        try:
            # ATOMIC SWAP: readers (the tide learner) glob this dir any
            # moment — write to .tmp then replace, never a half-written
            # feather.
            tmp = fn.with_suffix(".feather.tmp")
            out.to_feather(tmp)
            os.replace(tmp, fn)
            written.append((pair, len(out), str(out["date"].min()),
                            str(out["date"].max())))
        except Exception as exc:
            skipped.append((pair, f"write failed: {exc}"))

    span = (min(w[2] for w in written), max(w[3] for w in written)) if written \
        else ("-", "-")
    summary = {
        "written": len(written), "skipped": len(skipped),
        "window": span, "notes": notes,
        "seconds": round(time.time() - t0, 1),
        "written_detail": written, "skipped_detail": skipped,
    }
    (ROOT / "_deepstore_build.txt").write_text(
        "THE DEEP STORE BUILD\n"
        f"pairs written : {summary['written']}\n"
        f"pairs skipped : {summary['skipped']}\n"
        f"window        : {span[0]} -> {span[1]}\n"
        f"seconds       : {summary['seconds']}\n"
        + ("".join(f"note          : {n}\n" for n in notes))
        + ("".join(f"SKIPPED {p}: {d}\n" for p, d in skipped)),
        encoding="utf-8")
    _log(f"written={summary['written']} skipped={summary['skipped']} "
         f"window={span} in {summary['seconds']}s")
    return summary


if __name__ == "__main__":
    _lim = None
    if "--limit" in sys.argv:
        _lim = int(sys.argv[sys.argv.index("--limit") + 1])
    build(_lim)
