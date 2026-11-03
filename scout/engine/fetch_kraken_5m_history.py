"""THE 5M STORE EXTENDER (Sep 11) — recalibrates the deep 5m store
against the LIVE Kraken spot API (the venue the gates actually trade).

WHY (the recalibration): the native 5m feathers were fetched Sep 8 from
the Kraken PERP feed (_provenance.json: "kraken-perp") and end there —
2.4 days stale and a different venue than the funnel trades. This tool
appends the missing span from ccxt.kraken SPOT, paginating the OHLC
endpoint forward (720 candles = 60h per call), and measures the SEAM:
on the re-fetched overlap it compares spot closes/volumes vs the
perp-store values so the venue change is priced, not hidden.

  1. per pair: fetch (last_ts - overlap) -> now, forward pagination
  2. SEAM QA on the overlap: close diff %, 60-min payer agreement,
     volume ratio -> rescale appended volumes if the units disagree
  3. append strictly-new candles (drop the still-forming one), dedupe,
     verify the 5m cadence, write back the same schema
  4. report to _5m_extension_report.txt + _provenance.json['_extension']

Fail-safe: a pair that errors is skipped and named; the store is never
left half-written (the feather write is the last step, per pair).
Run:  .venv/Scripts/python.exe -X utf8 fetch_kraken_5m_history.py
      [--pairs XPL/USD,...] [--overlap-h 24] [--max-pages 40]
Rerun anytime — it only appends past the store's own last candle.
"""
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DEEP_DIR = ROOT / "surething_data" / "kraken"
PROVENANCE = DEEP_DIR / "_provenance.json"
REPORT = ROOT / "_5m_extension_report.txt"
TF_MS = 5 * 60 * 1000          # the 5m candle, in ms
GOAL_PCT = 1.5                 # "pays like a popup" (the store's contract)
VOL_RESCALE_LO, VOL_RESCALE_HI = 0.5, 2.0   # no rescale inside this band


def _log(msg: str) -> None:
    print(msg, flush=True)


def fetch_spot_5m(exchange, pair: str, since_ms: int, max_pages: int = 40):
    """Forward-paginate Kraken's 5m OHLC from since_ms to now. Returns
    (rows, err) — rows as [(ts_ms, o, h, l, c, v)], ascending, the
    still-forming candle dropped."""
    rows, since = [], since_ms
    for _page in range(max_pages):
        try:
            batch = exchange.fetch_ohlcv(pair, timeframe="5m",
                                         since=since, limit=720)
        except Exception as exc:
            return rows, str(exc)
        if not batch:
            break
        rows.extend(batch)
        new_since = int(batch[-1][0]) + TF_MS
        if new_since <= since or len(batch) < 720:
            break
        since = new_since
        time.sleep(0.15)   # be polite to the public endpoint
    # drop the still-forming candle (its close would be a lie)
    now_floor = (time.time() // 60) * 60 * 1000 - TF_MS
    rows = [r for r in rows if int(r[0]) <= now_floor]
    return rows, None


def merge_extension(native: pd.DataFrame, fresh: pd.DataFrame):
    """Append the strictly-new candles to the native frame. Returns
    (merged, appended_n) — deduped on ts, ascending; native wins on any
    residual overlap (same-store convention: keep the earlier write)."""
    a = native.copy()
    b = fresh.copy()
    a["ts"] = pd.to_datetime(a["date"], utc=True)
    b["ts"] = pd.to_datetime(b["date"], utc=True)
    last = a["ts"].max()
    b = b[b["ts"] > last]
    b = b[~b["ts"].isin(a["ts"])]
    cols = ["date", "open", "high", "low", "close", "volume"]
    merged = (pd.concat([a[cols], b[cols]], ignore_index=True)
                .sort_values("date").reset_index(drop=True))
    return merged, len(b)


def seam_qa(native: pd.DataFrame, fresh: pd.DataFrame):
    """Spot-vs-store comparison on the re-fetched overlap. Returns
    dict(close_diff_pct, payer_agree_pct, vol_ratio, overlap_n)."""
    a = native.copy()
    b = fresh.copy()
    a["ts"] = pd.to_datetime(a["date"], utc=True)
    b["ts"] = pd.to_datetime(b["date"], utc=True)
    last = a["ts"].max()
    ov_b = b[b["ts"] <= last].set_index("ts")
    ov_a = a.set_index("ts")
    j = ov_a[["close", "volume"]].join(
        ov_b[["close", "volume"]], rsuffix="_new", how="inner").dropna()
    if len(j) < 50:
        return None
    close_diff = float(((j["close"] - j["close_new"]).abs()
                        / j["close"]).mean() * 100)
    fk = j["close"].shift(-12) / j["close"] - 1.0
    fn = j["close_new"].shift(-12) / j["close_new"] - 1.0
    ok = fk.notna() & fn.notna()
    payer_agree = float(((fk[ok] >= GOAL_PCT / 100.0)
                         == (fn[ok] >= GOAL_PCT / 100.0)).mean() * 100) if ok.any() else None
    vr = (j["volume"] / j["volume_new"]).replace([float("inf")], float("nan")).dropna()
    vol_ratio = float(vr.median()) if len(vr) else None
    return {"close_diff_pct": round(close_diff, 4),
            "payer_agree_pct": round(payer_agree, 1) if payer_agree is not None else None,
            "vol_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
            "overlap_n": int(len(j))}


def extend_pair(exchange, pair: str, overlap_h: float, max_pages: int):
    """One pair: fetch the extension, QA the seam, append, write.
    Returns a report dict (never raises on fetch errors — 'err' set)."""
    fn = DEEP_DIR / f"{pair.replace('/', '_')}-5m.feather"
    native = pd.read_feather(fn)
    native["date"] = pd.to_datetime(native["date"], utc=True)
    last_ms = int(native["date"].max().timestamp() * 1000)
    since_ms = last_ms - int(overlap_h * 3.6e6)
    rows, err = fetch_spot_5m(exchange, pair, since_ms, max_pages)
    if err:
        return {"pair": pair, "err": f"fetch: {err}"}
    if not rows:
        return {"pair": pair, "err": "empty fetch", "appended": 0}
    fresh = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low",
                                        "close", "volume"])
    fresh["date"] = pd.to_datetime(fresh["ts_ms"], unit="ms", utc=True)
    qa = seam_qa(native, fresh)
    if qa is None or qa["overlap_n"] < 100:
        # Kraken's OHLC endpoint clamps to the most recent 720 candles,
        # so a long gap squeezes the natural overlap thin. Anchor a
        # dedicated seam fetch AT the store's last candle — the window
        # [last, last+60h] is exactly the seam the spot append must
        # agree with.
        seam_rows, seam_err = fetch_spot_5m(exchange, pair, last_ms, 2)
        if not seam_err and seam_rows:
            seam_df = pd.DataFrame(
                seam_rows, columns=["ts_ms", "open", "high", "low",
                                    "close", "volume"])
            seam_df["date"] = pd.to_datetime(seam_df["ts_ms"], unit="ms",
                                             utc=True)
            qa2 = seam_qa(native, seam_df)
            if qa2 is not None and (qa is None
                                    or qa2["overlap_n"] > qa["overlap_n"]):
                qa = qa2
    appended, merged = 0, native
    new = fresh[fresh["ts_ms"] > last_ms]
    if len(new):
        # volume-unit reconciliation: if the appended candles' volume
        # scale disagrees with the store's on the seam, rescale to fit
        if qa and qa["vol_ratio"] and not (VOL_RESCALE_LO <= qa["vol_ratio"] <= VOL_RESCALE_HI):
            new = new.copy()
            new["volume"] = new["volume"] * qa["vol_ratio"]
        merged, appended = merge_extension(native, new)
        t = merged["date"]
        gaps = int((t.diff().dropna().dt.total_seconds() != 300).sum())
        dupes = int(t.duplicated().sum())
        if dupes:
            return {"pair": pair, "err": f"{dupes} duplicate ts after merge"}
        merged = merged[["date", "open", "high", "low", "close", "volume"]]
        # ATOMIC SWAP (the driver's rule): readers (the tide learner, the
        # fleet study) glob the dir any moment — never let them see a
        # half-written feather. Write to .tmp then os.replace.
        tmp = fn.with_suffix(".feather.tmp")
        merged.to_feather(tmp)
        os.replace(tmp, fn)
        return {"pair": pair, "appended": appended, "gaps": gaps,
                "rows": len(merged), "new_end": str(merged["date"].max()),
                "seam": qa, "vol_rescaled": bool(
                    qa and qa["vol_ratio"]
                    and not (VOL_RESCALE_LO <= qa["vol_ratio"] <= VOL_RESCALE_HI))}
    return {"pair": pair, "appended": 0, "seam": qa, "note": "nothing new"}


def main(argv):
    """The CLI: single pairs / tuning flags, then one extend_all() pass
    (which writes the report + provenance itself)."""
    overlap_h = 24.0
    max_pages = 40
    pairs = None
    if "--overlap-h" in argv:
        overlap_h = float(argv[argv.index("--overlap-h") + 1])
    if "--max-pages" in argv:
        max_pages = int(argv[argv.index("--max-pages") + 1])
    if "--pairs" in argv:
        pairs = [p.strip() for p in
                 argv[argv.index("--pairs") + 1].split(",") if p.strip()]
    if pairs:
        import ccxt
        exchange = ccxt.kraken({"enableRateLimit": True})
        t0 = time.time()
        results = [extend_pair(exchange, p, overlap_h, max_pages)
                   for p in pairs]
        bad = [r for r in results if "err" in r]
        _log("pair pass: +%d candles, %d failed"
             % (sum(r.get("appended", 0) for r in results), len(bad)))
        return 0 if not bad else 1
    summary = extend_all(max_pages=max_pages, overlap_h=overlap_h)
    return 0 if not summary.get("failed") else 1


# ── THE DAILY DRIVER (Sep 11): the scanner's outcome-driver thread
# calls store_driver_loop so the deep 5m store NEVER goes stale — the
# seam QA is only meaningful while the gap is small (Kraken's OHLC
# endpoint serves the most recent 720 candles per call; past ~2.5 days
# of gap the overlap thins out). Same shape as tide_memory's live
# driver: daemon, stop-event, never raises, failure logs and retries
# next cycle.
STORE_REFRESH_S = 24 * 3600.0   # once a day


def _write_report(summary: dict, seconds: float, overlap_h: float) -> None:
    """The run's ledger: _5m_extension_report.txt + provenance."""
    seams = summary.get("seams") or []
    agg = {}
    if seams:
        real = [s for s in seams if s.get("payer_agree_pct") is not None]
        agg = {
            "pairs_with_seam_qa": len(seams),
            "mean_close_diff_pct": round(sum(s["close_diff_pct"] for s in seams)
                                         / len(seams), 4),
            "mean_payer_agree_pct": round(sum(s["payer_agree_pct"] for s in real)
                                          / max(1, len(real)), 2) if real else None,
            "mean_vol_ratio": round(sum(s["vol_ratio"] for s in seams if s["vol_ratio"] is not None)
                                    / max(1, sum(1 for s in seams if s["vol_ratio"] is not None)), 4),
        }
    lines = [
        "THE 5M STORE EXTENSION — live Kraken spot recalibration",
        f"run at          : {time.strftime('%Y-%m-%d %H:%M')}",
        f"pairs attempted : {summary['attempted']}",
        f"pairs extended  : {summary['extended']}",
        f"pairs failed    : {len(summary['failed'])}",
        f"candles appended: {summary['appended']:,}",
        f"seconds         : {round(seconds, 1)}",
        f"seam qa         : {agg}",
        *[f"FAILED {p}" for p in summary["failed"]],
    ]
    try:
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        prov["_extension"] = {
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "source": "ccxt.kraken spot 5m (live OHLC, forward pagination)",
            "candles_appended": summary["appended"],
            "seam_qa": agg,
            "failed_pairs": summary["failed"],
        }
        PROVENANCE.write_text(json.dumps(prov, indent=1), encoding="utf-8")
    except Exception:
        pass   # the report is a ledger, never a failure


def extend_all(max_pages: int = 8, overlap_h: float = 24.0) -> dict:
    """One extension pass over every native 5m pair (a fresh exchange
    client per pass): fetch, seam-QA, atomic append, report + provenance.
    Returns the summary dict; never raises."""
    t0 = time.time()
    try:
        import ccxt
        exchange = ccxt.kraken({"enableRateLimit": True})
    except Exception as exc:
        return {"err": f"exchange unavailable: {exc}"}
    results = [extend_pair(exchange, p, overlap_h, max_pages)
               for p in _native_pairs()]
    ok = [r for r in results if "err" not in r]
    bad = [r for r in results if "err" in r]
    summary = {
        "attempted": len(results),
        "extended": sum(1 for r in ok if r.get("appended")),
        "failed": [r["pair"] for r in bad],
        "appended": sum(r.get("appended", 0) for r in ok),
        "seams": [r["seam"] for r in ok if r.get("seam")],
    }
    _write_report(summary, time.time() - t0, overlap_h)
    _log("5m store extension pass: +%d candles across %d/%d pairs%s"
         % (summary["appended"], summary["extended"], summary["attempted"],
            (", FAILED: " + ",".join(summary["failed"])) if summary["failed"] else ""))
    return summary


def _native_pairs() -> list:
    return sorted(f.name[:-len("-5m.feather")].replace("_", "/")
                  for f in DEEP_DIR.glob("*-5m.feather")
                  if not f.stem.endswith(".coinbase"))


def store_driver_loop(stop, refresh_s: float = STORE_REFRESH_S,
                      first_delay_s: float = 90.0) -> None:
    """The scanner thread body: extend the store every refresh_s.
    First pass after first_delay_s (let the scanner finish booting).
    Never raises; dies with the stop event."""
    time.sleep(first_delay_s)
    while not stop.is_set():
        try:
            extend_all()
        except Exception as exc:      # never take the thread down
            try:
                _log(f"5m store extension pass failed: {exc}")
            except Exception:
                pass
        stop.wait(refresh_s)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
