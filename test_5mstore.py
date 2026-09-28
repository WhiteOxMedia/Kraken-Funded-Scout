"""Test THE 5M STORE EXTENDER (Sep 11 — the deep-store recalibration):
the fetch pagination contract, the strictly-new merge, the seam QA math,
and the volume-unit reconciliation — all OFFLINE (a fake exchange, a
sandbox store dir; no ccxt, no network). The live tool is
fetch_kraken_5m_history.py; the store it maintains feeds the tide
learner, the fleet study, and the Sure Thing replay."""
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import fetch_kraken_5m_history as fk

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


TF = fk.TF_MS
NOW_FLOOR = (time.time() // 60) * 60 * 1000 - TF   # the forming-candle cut


def _candles(start_ms, n, close=100.0, vol=10.0):
    return [[start_ms + i * TF, close, close, close, close, vol]
            for i in range(n)]


class FakeExchange:
    """Paginated fetch: each call returns up to `page` candles from
    `since`, advancing; pages repeat until the data runs out."""

    def __init__(self, batches, page=720):
        self.batches = batches          # {since_key: rows} — served in order
        self.calls = []
        self.page = page

    def fetch_ohlcv(self, pair, timeframe="5m", since=0, limit=720):
        self.calls.append((pair, timeframe, since, limit))
        if not self.batches:
            return []
        take = min(self.page, len(self.batches[0]))
        out = self.batches[0][:take]
        self.batches[0] = self.batches[0][take:]
        if not self.batches[0]:
            self.batches.pop(0)
        return out


# ── 1. the pagination contract ────────────────────────────────────────
start = NOW_FLOOR - 800 * TF
fx = FakeExchange([_candles(start, 720), _candles(start + 720 * TF, 100)])
rows, err = fk.fetch_spot_5m(fx, "XPL/USD", since_ms=start, max_pages=5)
expected_kept = sum(1 for i in range(820) if start + i * TF <= NOW_FLOOR)
check("pagination walks forward until the data runs out",
      err is None and len(rows) == expected_kept, (len(rows), expected_kept))
check("every page advances `since` monotonically past the last candle",
      len(fx.calls) == 2 and fx.calls[1][2] > fx.calls[0][2]
      and fx.calls[0][2] == start, fx.calls)
check("the still-forming candle is dropped (never a fake close)",
      max(r[0] for r in rows) <= NOW_FLOOR)

fx = FakeExchange([[[start + i * TF] + [100.0] * 5 for i in range(50)]])
rows, err = fk.fetch_spot_5m(fx, "XPL/USD", since_ms=start, max_pages=5)
check("a short tail ends the walk (len < 720)",
      err is None and len(rows) == 50, (len(rows), err))


class BoomExchange:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("network down")


rows, err = fk.fetch_spot_5m(BoomExchange(), "XPL/USD", since_ms=start)
check("a fetch error is returned, never raised", err is not None and rows == [])

# ── 2. the strictly-new merge ─────────────────────────────────────────
base = pd.Timestamp(NOW_FLOOR - 3000 * TF, unit="ms", tz="UTC")
native = pd.DataFrame({
    "date": pd.date_range(base, periods=1000, freq="5min", tz="UTC"),
    "open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0, "volume": 10.0})
# fresh: 100 overlap candles + 200 new ones
fresh_ts = pd.date_range(base + pd.Timedelta(minutes=5 * 900), periods=300,
                         freq="5min", tz="UTC")
fresh = pd.DataFrame({
    "date": fresh_ts, "open": 1.0, "high": 1.0, "low": 1.0,
    "close": 100.0, "volume": 10.0})
merged, appended = fk.merge_extension(native, fresh)
check("only strictly-new candles append (overlap excluded)",
      appended == 200, appended)
check("no duplicates and ascending after merge",
      not merged["date"].duplicated().any()
      and merged["date"].is_monotonic_increasing)
check("the native store wins on overlap (keep the earlier write)",
      len(merged) == 1200 and str(merged['date'].min()) == str(native['date'].min()))

# ── 3. the seam QA math ───────────────────────────────────────────────
qa = fk.seam_qa(native, fresh)
check("identical closes -> zero diff, full payer agreement, vol ratio 1",
      qa and qa["close_diff_pct"] == 0.0 and qa["payer_agree_pct"] == 100.0
      and qa["vol_ratio"] == 1.0, qa)

fresh2 = fresh.copy()
fresh2["volume"] = fresh2["volume"] * 2.0
qa2 = fk.seam_qa(native, fresh2)
check("doubled fresh volume -> vol ratio 0.5 (the units flag)",
      qa2 and abs(qa2["vol_ratio"] - 0.5) < 1e-9, qa2)

n_shift = native.copy()
n_shift["close"] = n_shift["close"] * 1.02
qa3 = fk.seam_qa(n_shift, fresh)
check("2% store-vs-fresh close gap -> close_diff ~1.96% (relative to the store)",
      qa3 and abs(qa3["close_diff_pct"] - 1.9608) < 0.01, qa3)

# ── 4. extend_pair end-to-end in a sandbox (volume rescale + fail-safe)
sandbox = Path("_test_5mstore_dir")
sandbox.mkdir(exist_ok=True)
real_dir, real_prov, real_report = fk.DEEP_DIR, fk.PROVENANCE, fk.REPORT
try:
    # GUARDED SWAPS (testleaks, Sep 23): inside the try whose finally
    # restores all three — a crash mid-extend never strands the sandbox.
    fk.DEEP_DIR = sandbox
    fk.PROVENANCE = sandbox / "_prov.json"
    fk.REPORT = sandbox / "_report.txt"
    # native volume unit is 100x the spot feed's (the seam flag); the
    # fake fetch re-covers the last 100 store candles + 600 new ones
    nat = native.copy()
    nat["volume"] = nat["volume"] * 100.0
    nat.to_feather(sandbox / "XPL_USD-5m.feather")
    last_ms = int(nat["date"].max().timestamp() * 1000)
    fx = FakeExchange([_candles(last_ms - 99 * TF, 700, close=100.0, vol=10.0)])
    res = fk.extend_pair(fx, "XPL/USD", overlap_h=1, max_pages=3)
    check("extension appends and reports the seam",
          res.get("appended") == 600 and res.get("seam"), res)
    check("the volume-unit mismatch triggers the rescale",
          res.get("vol_rescaled") is True
          and abs(res["seam"]["vol_ratio"] - 100.0) < 1e-6, res.get("seam"))
    out = pd.read_feather(sandbox / "XPL_USD-5m.feather")
    tail = out.tail(600)
    check("appended candles land on the store's volume scale (x100)",
          bool((tail["volume"] / 10.0 - 100.0).abs().max() < 1e-6),
          float(tail["volume"].iloc[-1]))
    check("the merged store keeps the 5m cadence",
          int((pd.to_datetime(out['date'], utc=True).diff().dropna()
               .dt.total_seconds() != 300).sum()) == 0)

    # fail-safe: an exchange that dies leaves the store untouched
    before = out.copy()
    res2 = fk.extend_pair(BoomExchange(), "XPL/USD", overlap_h=1, max_pages=2)
    after = pd.read_feather(sandbox / "XPL_USD-5m.feather")
    check("a dead fetch is skipped and named, store untouched",
          "err" in res2 and len(after) == len(before)
          and after["date"].equals(before["date"]))
finally:
    fk.DEEP_DIR, fk.PROVENANCE, fk.REPORT = real_dir, real_prov, real_report
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)

# ── 5. the report + provenance land in the repo convention ────────────
src = Path("fetch_kraken_5m_history.py").read_text(encoding="utf-8")
check("the tool reports to _5m_extension_report.txt",
      'REPORT.write_text' in src and "_5m_extension_report.txt" in src)
check("the provenance carries the extension block",
      '"_extension"' in src and "seam_qa" in src)

# ── 6. THE DAILY DRIVER (Sep 11): the scanner thread keeps the store fresh
real_refresh = fk.STORE_REFRESH_S
sandbox = Path("_test_5mstore_driver_dir")
sandbox.mkdir(exist_ok=True)
real_dir = fk.DEEP_DIR
real_ext = fk.extend_pair
real_pairs = fk._native_pairs
real_prov6, real_report6 = fk.PROVENANCE, fk.REPORT
try:
    # sandbox dir with ONE native pair whose extension the fake serves
    fk.DEEP_DIR = sandbox
    # SANDBOX THE LEDGERS TOO (fix, Sep 11): extend_all() rewrites the
    # report + provenance — without the swap the test clobbered the
    # live _5m_extension_report.txt / _provenance.json with a 1-pair
    # sandbox run.
    fk.PROVENANCE = sandbox / "_prov.json"
    fk.REPORT = sandbox / "_report.txt"
    nat = native.iloc[:500].copy()
    nat.to_feather(sandbox / "AAA_USD-5m.feather")
    last_ms = int(nat["date"].max().timestamp() * 1000)
    fake = FakeExchange([_candles(last_ms, 60, close=100.0, vol=10.0)])
    fk._native_pairs = lambda: ["AAA/USD"]
    fk.extend_pair = lambda ex, p, ov, mp: real_ext(fake, p, ov, mp)

    # extend_all drives the fake end to end and returns the summary
    summ = fk.extend_all()
    check("extend_all appends over the sandboxed store",
          summ.get("appended") == 59 and summ.get("failed") == [], summ)

    # the loop body: first pass fires after first_delay_s, then idles on
    # the stop event — no exception escapes the thread
    import threading as _th
    stop = _th.Event()
    th = _th.Thread(target=fk.store_driver_loop, args=(stop,),
                    kwargs={"first_delay_s": 0.1, "refresh_s": 30.0},
                    daemon=True)
    th.start()
    th.join(timeout=5)
    check("the driver thread idles alive on the stop event (no crash)",
          th.is_alive() or stop.is_set())
    stop.set(); th.join(timeout=5)

    real_bg = Path("background_scanner.py").read_text(encoding="utf-8")
    check("the scanner wires the deep-store driver thread",
          "store_driver_loop" in real_bg
          and "deep-store-5m-driver" in real_bg)
    check("the driver is a daemon that never takes the funnel down",
          "daemon=True" in real_bg and "_fk5m_stop" in real_bg)
    check("the daily cadence constant exists (24h)",
          fk.STORE_REFRESH_S == 24 * 3600.0)
finally:
    fk.DEEP_DIR = real_dir
    fk.extend_pair = real_ext
    fk._native_pairs = real_pairs
    fk.STORE_REFRESH_S = real_refresh
    fk.PROVENANCE, fk.REPORT = real_prov6, real_report6
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)

check("feather writes are atomic swaps (readers never see a partial)",
      "os.replace(tmp, fn)" in src
      and Path("build_deepstore.py").read_text(encoding="utf-8").count("os.replace(tmp, fn)") == 1)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_5mstore_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
