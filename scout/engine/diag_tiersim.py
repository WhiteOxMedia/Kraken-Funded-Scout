"""diag_tiersim.py — THE TIER SIMULATION (Nov 20).

Does the CURRENT Sure Thing coil funnel pass the contest at $1k, $5k and
$10k? This replays the REAL detector (sure_thing.analyze, unmodified) over
live Kraken 5m candles and runs the resulting book through
contest_account's OWN rules — the 3% guard (max_entry_usd/entry_size_usd),
the $1 confirmation target, the 4h contract and the 0.08% round trip.

Two arms, so the Nov 20 gate change is priced rather than assumed:
  gate       the shipped path: a pre-breakout coil ARMS a pending row and
             enters only on the $1 target or on its zone high cracking;
  pre-gate   what the code did before: enter at the pre-breakout price.

HONESTY / SCOPE
  · window, two honest ways to widen it (Kraken's public OHLC ignores
    `since`: 720 bars is all it will ever return at 5m):
      --venue kucoin --days N   another venue's REAL 5m candles, paginated
        over N days. Same granularity, same detector, same 720-bar slice
        the scanner feeds analyze — but KuCoin prices, not Kraken's, so
        treat the edge as the detector's, not the venue's fill.
      --archive                 stamp today's Kraken 720-bar window into
        scratch/tiersim_archive/<date>/; repeated runs ACCUMULATE Kraken
        history without substituting a venue (slow, no backfill).
    Neither resamples: a 15m/1h bar would change the detector's 24-bar
    (2h) coil into a 6h/24h structure, which is a different contract.
  · MANY AT ONCE (Nov 20 rebuild): the live account holds every pair it
    fired, so this book does too — a pending row and a position PER PAIR,
    each sized from the free cash (balance - open notional) through the
    live entry_size_usd. The old single slot starved the book on a long
    hold, which is why the 8h/24h runs priced the model, not the strategy.
  · the champion/mover gate chains live inline in the scanner and are NOT
    replayable — this prices the coil funnel only.
  · book-carry and the rotation gate are not modelled (no depth feed); the
    3% guard is. Missing volatility profiles fall back to the account's
    own conservative DEFAULT_WINDOW_DD_PCT.
  · read-only: candles are cached under scratch/tiersim_cache/, nothing
    else on disk changes. Run: python diag_tiersim.py [--pairs N] [--trace]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import contest_account as ca
import sure_thing as st

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "scratch" / "tiersim_cache"   # volatile: never shipped
CONFIRM_PROFIT_USD = 1.0          # background_scanner.POPUP_CONFIRM_PROFIT_USD
API = "https://api.kraken.com/0/public/OHLC"
KUCOIN = "https://api.kucoin.com/api/v1/market/candles"
GATE = "https://api.gateio.ws/api/v4/spot/candlesticks"
MEXC = "https://api.mexc.com/api/v3/klines"
BAR_WINDOW = 720                  # what the live scanner hands analyze
HOLD_MIN = st.SURE_THING_WINDOW_MIN   # the hold clock; --hold-min overrides


def _kraken(pair: str) -> str:
    return pair.replace("/", "")


def fetch(pair: str) -> list[list]:
    """The last 720 5m candles as ccxt-order rows [ts,o,h,l,c,v]."""
    dest = CACHE / f"{_kraken(pair)}_5m.json"
    if dest.exists():
        try:
            rows = json.loads(dest.read_text(encoding="utf-8"))
            if rows:
                return rows
        except Exception:
            pass
    url = f"{API}?{urllib.parse.urlencode({'pair': _kraken(pair), 'interval': 5})}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    if doc.get("error"):
        raise RuntimeError(doc["error"])
    key = next(k for k in doc["result"] if k != "last")
    # Kraken: [ts, o, h, l, c, vwap, volume, count] -> ccxt: volume fifth
    rows = [[r[0], r[1], r[2], r[3], r[4], r[6]] for r in doc["result"][key]]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows), encoding="utf-8")
    time.sleep(0.4)                     # polite: public OHLC, ~1 req/s
    return rows


def fetch_kucoin(pair: str, days: int) -> list[list]:
    """A venue that still serves real 5m history: 1,500 bars per call,
    time-ranged, so N days is a handful of requests. Same granularity as
    Kraken's window; different prices (documented in the report)."""
    sym = f"{pair.split('/')[0]}-USDT"
    dest = CACHE / f"kucoin_{sym.replace('-', '')}_{days}d.json"
    if dest.exists():
        try:
            rows = json.loads(dest.read_text(encoding="utf-8"))
            if rows:
                return rows
        except Exception:
            pass
    end = int(time.time())
    t, out = end - days * 86400, {}
    while t < end:
        seg = min(t + 1500 * 300, end)
        url = (f"{KUCOIN}?{urllib.parse.urlencode({'type': '5min', 'symbol': sym, 'startAt': t * 1000, 'endAt': seg * 1000})}")
        req = urllib.request.Request(url, headers={"User-Agent": "scout-tiersim/1"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    doc = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 3:
                    raise
                time.sleep(2.0 + attempt)   # public rate limit: back off
        if str(doc.get("code")) != "200000":
            raise RuntimeError(str(doc.get("msg") or doc.get("code"))[:40])
        for r in doc.get("data") or []:
            # KuCoin: [ts, open, close, high, low, volume] -> ccxt order
            out[int(r[0])] = [int(r[0]), r[1], r[3], r[4], r[2], r[5]]
        t = seg
        time.sleep(0.12)
    rows = [out[k] for k in sorted(out)]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def fetch_coinbase(pair: str, days: int) -> list[list]:
    """A venue that still serves real 5m history with an explicit range:
    300 bars per request, so N days is a few dozen calls per pair."""
    sym = f"{pair.split('/')[0]}-USD"
    dest = CACHE / f"coinbase_{sym.replace('-', '')}_{days}d.json"
    if dest.exists():
        try:
            rows = json.loads(dest.read_text(encoding="utf-8"))
            if rows:
                return rows
        except Exception:
            pass
    end = int(time.time())
    t, out = end - days * 86400, {}
    step = 300 * 300                      # 300 bars per request
    while t < end:
        seg = min(t + step, end)
        url = ("https://api.exchange.coinbase.com/products/"
               f"{sym}/candles?granularity=300"
               f"&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))}"
               f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(seg))}")
        req = urllib.request.Request(url, headers={"User-Agent": "scout-tiersim/1"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 3:
                    raise
                time.sleep(1.5 + attempt)
        for r in data or []:              # [ts, low, high, open, close, vol]
            out[int(r[0])] = [int(r[0]), r[3], r[2], r[1], r[4], r[5]]
        t = seg
        time.sleep(0.2)
    rows = [out[k] for k in sorted(out)]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _paged(pair: str, days: int, tag: str, call) -> list[list]:
    """Fetch `days` of 5m bars from a venue that pages by time range.
    `call(start, end)` returns ccxt-order rows; results are cached by tag."""
    dest = CACHE / f"{tag}_{pair.replace('/', '')}_{days}d.json"
    if dest.exists():
        try:
            rows = json.loads(dest.read_text(encoding="utf-8"))
            if rows:
                return rows
        except Exception:
            pass
    end = int(time.time())
    t, out = end - days * 86400, {}
    while t < end:
        seg = min(t + 1000 * 300 - 300, end)   # under the 1,000-point cap
        for r in call(t, seg) or []:
            out[int(r[0])] = r
        t = seg
    rows = [out[k] for k in sorted(out)]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def fetch_gate(pair: str, days: int) -> list[list]:
    """Gate's spot 5m candles: 1,000 per range request, and the venue
    where most of Kraken's long-tail listings actually trade. Rows are
    [ts, quote vol, close, high, low, open, base vol, closed]."""
    sym = f"{pair.split('/')[0]}_USDT"

    def call(start: int, end: int) -> list[list]:
        url = f"{GATE}?{urllib.parse.urlencode({'currency_pair': sym, 'interval': '5m', 'from': start, 'to': end})}"
        req = urllib.request.Request(url, headers={"User-Agent": "scout-tiersim/1"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(0.15)
        return [[int(r[0]), r[5], r[3], r[4], r[2], r[6]] for r in data or []]

    return _paged(pair, days, "gate", call)


def fetch_mexc(pair: str, days: int) -> list[list]:
    """MEXC spot 5m candles: 1,000 per request, millisecond range."""
    sym = f"{pair.split('/')[0]}USDT"

    def call(start: int, end: int) -> list[list]:
        url = (f"{MEXC}?{urllib.parse.urlencode({'symbol': sym, 'interval': '5m', 'startTime': start * 1000, 'endTime': end * 1000, 'limit': 1000})}")
        req = urllib.request.Request(url, headers={"User-Agent": "scout-tiersim/1"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(0.2)
        return [[int(r[0]) // 1000, r[1], r[2], r[3], r[4], r[5]] for r in data or []]

    return _paged(pair, days, "mexc", call)


def fetch_auto(pair: str, days: int) -> tuple[list[list], str]:
    """Every coin in the contest, on the best 5m feed that has it: Coinbase
    first (30 d), then Gate, then MEXC, then Kraken's own 720-bar window for
    the long tail that trades nowhere else. The source is returned, not
    assumed, and lands in the report's coverage table."""
    for name, fn in (("coinbase", fetch_coinbase), ("gate", fetch_gate),
                     ("mexc", fetch_mexc)):
        try:
            rows = fn(pair, days)
        except Exception:
            continue
        if len(rows) > st.SQUEEZE_LOOKBACK:
            return rows, name
    return fetch(pair), "kraken"


def fetch_scout(days: int, pairs: list[str]) -> tuple[dict, list[str]]:
    """THE SCOUT'S OWN STORE: ohlcv_cache.db, table ohlcv(pair, ts[ms],
    open, high, low, close, volume). It is filled by the scanner's own
    downloads and (when it has been running a while) holds far more 5m
    history than any single Kraken response. Empty store -> nothing to
    replay, said plainly rather than silently falling back."""
    import sqlite3
    db = ROOT / "ohlcv_cache.db"
    if not db.exists() or db.stat().st_size == 0:
        raise SystemExit(
            f"the scout's candle store is empty here ({db.name}, "
            f"{db.stat().st_size if db.exists() else 'missing'} bytes) — "
            "run this on the machine whose scanner has been filling it, "
            "or use --venue kucoin")
    con = sqlite3.connect(db)
    try:
        cutoff = int((time.time() - days * 86400) * 1000)
        rows = con.execute(
            "SELECT pair, ts, open, high, low, close, volume FROM ohlcv "
            "WHERE ts >= ? ORDER BY pair, ts", (cutoff,)).fetchall()
    finally:
        con.close()
    out: dict[str, list[list]] = {}
    for pair, ts, o, h, l, c, v in rows:
        out.setdefault(str(pair), []).append(
            [int(ts) // 1000, o, h, l, c, v])
    want = set(pairs)
    candles = {p: r for p, r in out.items() if p in want and len(r) > st.SQUEEZE_LOOKBACK}
    return candles, [p for p in pairs if p not in candles]


def check_5m(candles: dict[str, list[list]]) -> float:
    """The contract guard: refuse a coarser feed. A 15m or 1h bar would
    turn the detector's 24-bar (2h) coil into a 6h/24h structure — the
    question would no longer be the one the live code asks."""
    diffs = []
    for rows in list(candles.values())[:10]:
        diffs += [rows[i + 1][0] - rows[i][0]
                  for i in range(min(200, len(rows) - 1))
                  if rows[i + 1][0] > rows[i][0]]
    diffs.sort()
    med = diffs[len(diffs) // 2] if diffs else 0.0
    if abs(med - 300.0) > 1.0:
        raise SystemExit(f"refusing to run: bars are {med:.0f}s apart, not "
                         f"300s — the Sure Thing coil contract is 5m")
    return med


def archive_kraken(pair: str, rows: list[list]) -> Path:
    """Repeat captures: each run stamps its 720-bar window under a UTC
    date, so successive runs accumulate real Kraken 5m history."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    dest = ROOT / "scratch" / "tiersim_archive" / day
    dest.mkdir(parents=True, exist_ok=True)
    f = dest / f"{_kraken(pair)}_5m.json"
    f.write_text(json.dumps(rows), encoding="utf-8")
    return f


def _coiled(rows: list[list], idx: int) -> bool:
    """The detector's own first two gates, cheap: a tight zone with three
    ceiling tests. Skips ~95% of bars before the expensive analyze."""
    zone = rows[max(0, idx - st.COIL_LOOKBACK):idx]
    if len(zone) < st.COIL_LOOKBACK:
        return False
    hi = max(float(r[2]) for r in zone)
    lo = min(float(r[3]) for r in zone)
    mid = (hi + lo) / 2.0
    if mid <= 0 or (hi - lo) / mid * 100.0 > st.COIL_MAX_RANGE_PCT:
        return False
    thr = hi * (1.0 - st.CEILING_TOUCH_PCT / 100.0)
    return sum(1 for r in zone if float(r[2]) >= thr) >= st.MIN_CEILING_TESTS


class Book:
    """One contest tier: balance, the pending rows, the open positions.

    MANY AT ONCE (Nov 20 rebuild): the live account holds every pair it
    fired, so this book does too. One pending row and one position PER PAIR
    — the scanner's own shape, where pending rows are keyed by pair and a
    pair never doubles up — each sized against the FREE CASH the book still
    holds (`balance - open notional`) through the same `entry_size_usd` the
    live account uses: the min of the account, the book's carry and the 3%
    guard. The old single slot starved the book on long holds, which is why
    the 8h and 24h runs said more about the model than about the strategy.
    """

    def __init__(self, tier: int, mode: str, stop: bool = False):
        self.tier, self.mode, self.stop = tier, mode, stop
        self.start = ca.TIERS[tier]["start"]
        self.balance = self.start
        self.pending: dict[str, dict] = {}
        self.pos: dict[str, dict] = {}
        self.arms = self.confirms = self.expired = 0
        self.trades = self.wins = 0
        self.net = 0.0
        self.dead = self.hit_target = False
        self.peak_bal, self.peak_at, self.tiny = self.start, 0.0, 0
        self.peak_open = 0
        self.last_px: dict[str, float] = {}   # each pair's own last mark
        self.armed_min: list[float] = []
        self.bar_pct: list[float] = []
        self.log: list[tuple] = []
        self.log_ts: list[tuple] = []       # (entry ts, exit ts), one per log row
        self.exits: dict[str, int] = {}

    @property
    def need(self) -> float:
        return self.start * (1.0 + ca.PROFIT_TARGET_PCT / 100.0)

    @property
    def halted(self) -> bool:
        """No new risk: dead, or (contest exit) already at +12%."""
        return self.dead or (self.stop and self.hit_target)

    def open_notional(self) -> float:
        return sum(float(q["size"]) for q in self.pos.values())

    def carry(self) -> float:
        """The free cash a new entry may use — what is not committed."""
        return max(0.0, float(self.balance) - self.open_notional())

    def arm(self, pair: str, ts: float, px: float, ceiling: float,
            spend: float, dd: float):
        if pair in self.pending or pair in self.pos or self.halted:
            return
        entry = float(px)
        if spend <= 0 or entry <= 0:
            self.enter(pair, ts, entry, spend, dd)   # fail-open: fire now
            return
        self.pending[pair] = {
            "pair": pair, "entry": entry, "spend": spend, "ts": ts,
            "ceiling": float(ceiling or 0.0),
            # the gate's target: $1 of profit on THIS row's spend
            "target": entry * (1.0 + CONFIRM_PROFIT_USD / spend),
            "expires": ts + st.SURE_THING_WINDOW_MIN * 60.0,
        }
        self.arms += 1
        self.bar_pct.append(round((self.pending[pair]["target"] / entry - 1.0) * 100.0, 3))

    def enter(self, pair: str, ts: float, px: float, spend: float, dd: float):
        if pair in self.pos or self.halted or spend <= 0 or px <= 0:
            return
        if min(spend, self.balance) < 1.0:
            self.tiny += 1          # the 3% guard has squeezed risk to nothing
        self.pos[pair] = {
            "pair": pair, "entry": px, "size": min(spend, self.balance),
            "goal": px * (1.0 + st.SURE_THING_GOAL_PCT / 100.0),
            "ts": ts, "deadline": ts + HOLD_MIN * 60.0,
            "peak": px,
        }
        self.peak_open = max(self.peak_open, len(self.pos))

    def step(self, pair: str, ts: float, o: float, h: float, l: float,
             c: float):
        # NO EARLY RETURN ON halted (Nov 20): a dead or stopped book takes no
        # NEW risk (arm/enter check that), but the positions it already holds
        # are still managed — the old early return froze them until the
        # window's end, which is how one run reported a 27-day hold and a
        # -5.06% book against a -3% line.
        self.last_px[pair] = float(c)   # the book's mark for THIS pair
        p = self.pending.get(pair)
        if p:
            if h >= p["ceiling"] > 0 or h >= p["target"]:
                lvls = [x for x in (p["ceiling"], p["target"]) if x > 0 and h >= x]
                fill = max(min(lvls), o)      # the level, never a bad gap
                self.armed_min.append(round((ts - p["ts"]) / 60.0, 1))
                self.pending.pop(pair, None)
                self.confirms += 1
                self.enter(pair, ts, fill, p["spend"], None)
            elif ts > p["expires"]:
                self.pending.pop(pair, None)
                self.expired += 1
        q = self.pos.get(pair)
        if q:
            q["peak"] = max(q["peak"], h)
            # the live ratchet: 3% off the peak, 1.5% once the run is +2%
            trail = 1.5 if (q["peak"] / q["entry"] - 1.0) * 100.0 >= 2.0 else 3.0
            stop = q["peak"] * (1.0 - trail / 100.0)
            if h >= q["goal"]:
                self.close(pair, max(q["goal"], o), ts, "goal")
            elif q["peak"] > q["entry"] and l <= stop:
                self.close(pair, min(stop, o), ts, "trail")
            elif ts > q["deadline"]:
                self.close(pair, c, ts, "clock")

    def close(self, pair: str, px: float, ts: float, why: str = "end"):
        q = self.pos.pop(pair, None)
        if not q:
            return
        _g, _c, net = ca.pnl_net_usd(q["entry"], px, q["size"])
        self.balance += net
        self.net += net
        if self.balance > self.peak_bal:
            self.peak_bal, self.peak_at = self.balance, ts
        self.trades += 1
        self.wins += 1 if net > 0 else 0
        self.exits[why] = self.exits.get(why, 0) + 1
        self.log.append((q["pair"], q["entry"], px, q["size"],
                         round((ts - q["ts"]) / 60.0, 1), net, why))
        self.log_ts.append((q["ts"], ts))
        if self.balance <= self.start * (1.0 - ca.MAX_LOSS_PCT / 100.0):
            if not self.dead:
                self.dead = True
                # THE DEATH LINE ENDS THE BOOK (Nov 20): the live account is
                # out at -3%, so whatever is still open is closed HERE — not
                # carried to the window's end. EACH POSITION AT ITS OWN
                # MARK (`last_px`): closing the whole book at the price of
                # the pair that tripped the line booked one coin's tape onto
                # every other one (the bug that read +6,882 on a $21 CSPR
                # position closed at ETH's price).
                for _p in list(self.pos):
                    self.close(_p, self.last_px.get(_p, self.pos[_p]["entry"]),
                               ts, "death line")
        if self.balance >= self.need:
            self.hit_target = True


def run(pairs: list[str], candles: dict[str, list[list]]) -> dict[tuple, "Book"]:
    books = {(t, m, s): Book(t, m, s) for t in sorted(ca.TIERS)
             for m in ("gate", "pre-gate") for s in (False, True)}
    dd = {}
    for p in pairs:
        try:
            dd[p] = ca.window_drawdown_pct(p)
        except Exception:
            dd[p] = ca.DEFAULT_WINDOW_DD_PCT
    events = []
    for p in pairs:
        rows = candles.get(p) or []
        for i in range(st.SQUEEZE_LOOKBACK // 2, len(rows)):
            events.append((rows[i][0], p, i))
    events.sort()
    for ts, p, i in events:
        rows = candles[p]
        r = rows[i]
        o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        for b in books.values():
            b.step(p, ts, o, h, l, c)
        if not _coiled(rows, i):
            continue
        # exactly what the live scanner feeds analyze: the last 720 bars
        a = st.analyze(rows[max(0, i + 1 - BAR_WINDOW):i + 1])
        if not a.get("fire") or not a.get("trigger"):
            continue
        px = float(a.get("trigger_px") or c)
        for (_tier, mode, _stop), b in books.items():
            # free-cash carry: what the open book has not already committed
            spend = ca.entry_size_usd(b.balance, b.carry(), dd[p], b.start)
            if mode == "gate" and a["trigger"] == "pre-breakout":
                b.arm(p, ts, px, a.get("zone_high") or 0.0, spend, dd[p])
            else:
                b.enter(p, ts, px, spend, dd[p])
    # close everything still open at the window's end
    for b in books.values():
        for p in list(b.pos):
            rows = candles.get(p) or []
            b.close(p,
                    float(rows[-1][4]) if rows else b.pos[p]["entry"],
                    float(rows[-1][0]) if rows else b.pos[p]["ts"],
                    "window end")
    return books


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=0, help="0 = the whole universe")
    ap.add_argument("--trace", action="store_true", help="print every trade")
    ap.add_argument("--venue", choices=("kraken", "kucoin", "coinbase", "gate",
                                        "scout", "auto"),
                    default="kraken")
    ap.add_argument("--days", type=int, default=30,
                    help="kucoin/scout: how many days of 5m history")
    ap.add_argument("--archive", action="store_true",
                    help="kraken only: stamp today's window for repeat captures")
    ap.add_argument("--hold-min", type=float,
                    default=st.SURE_THING_WINDOW_MIN,
                    help="how long a position may hold before the clock ends "
                         "it (the shipped contract is "
                         f"{st.SURE_THING_WINDOW_MIN:.0f} min; 240 is the "
                         "historical floor the old runs used)")
    ap.add_argument("--min-days", type=float, default=0.0,
                    help="drop pairs whose 5m feed covers less than this, so "
                         "late-listing coins cannot carry a result")
    args = ap.parse_args()
    global HOLD_MIN
    HOLD_MIN = args.hold_min
    if HOLD_MIN < 240.0:
        raise SystemExit("refusing to run: --hold-min below the 240-min "
                         "contract history is a different strategy, not "
                         "a window")
    universe = list(json.loads(
        (ROOT / "volatility_universe.json").read_text(encoding="utf-8")))
    pairs = universe[: args.pairs] if args.pairs else universe
    candles, skipped, meta = {}, [], {}
    if args.venue == "scout":
        candles, skipped = fetch_scout(args.days, pairs)
    else:
        for p in pairs:
            try:
                if args.venue == "auto":
                    rows, name = fetch_auto(p, args.days)
                else:
                    name = args.venue if args.venue != "kraken" else "kraken"
                    rows = (fetch_kucoin(p, args.days) if args.venue == "kucoin"
                            else fetch_coinbase(p, args.days) if args.venue == "coinbase"
                            else fetch_gate(p, args.days) if args.venue == "gate"
                            else fetch(p))
                if args.archive and args.venue == "kraken" and rows:
                    archive_kraken(p, rows)
                if len(rows) > st.SQUEEZE_LOOKBACK:
                    candles[p] = rows
                    meta[p] = (name, len(rows),
                               (rows[-1][0] - rows[0][0]) / 86400.0)
                else:
                    skipped.append(f"{p} (only {len(rows)} bars)")
                print(f"  fetch {len(candles)} ok / {len(skipped)} skipped "
                      f"— {p} {len(rows)} bars [{name}]", file=sys.stderr,
                      flush=True)
            except Exception as exc:
                skipped.append(f"{p} ({str(exc)[:28]})")
                print(f"  skip {p}: {str(exc)[:40]}", file=sys.stderr, flush=True)
    dropped = sorted(((p, meta[p][2]) for p in candles
                      if meta[p][2] < args.min_days), key=lambda x: x[1])
    for p, d in dropped:
        skipped.append(f"{p} ({d:.1f}d < {args.min_days:.0f}d)")
        candles.pop(p)
        meta.pop(p)
    if not candles:
        raise SystemExit("no candles fetched")
    span = max(r[-1][0] for r in candles.values()) - \
        min(r[0][0] for r in candles.values())
    days = span / 86400.0
    books = run(list(candles), candles)

    out = []
    out.append("TIER SIMULATION — the Sure Thing coil funnel, live 5m candles")
    out.append("=" * 78)
    src = {"kucoin": "KuCoin 5m bars (venue-substituted: same granularity, "
                      "same detector, not Kraken fills)",
           "coinbase": "Coinbase 5m bars (venue-substituted: same "
                       "granularity, same detector, not Kraken fills)",
           "gate": "Gate 5m bars (venue-substituted: same granularity, "
                   "same detector, not Kraken fills)",
           "auto": "per-pair best available 5m feed — the coverage table "
                   "below names each pair's venue and span, so nothing is "
                   "blended in silence",
           "scout": "the scout's own ohlcv_cache.db store (5m bars the "
                    "scanner downloaded)",
           "kraken": "Kraken 5m bars (the live scanner's own window)"}[args.venue]
    check_5m(candles)
    out.append(f"source: {src}")
    out.append(f"pairs {len(candles)} (skipped {len(skipped)})  ·  window "
               f"{days:.2f} days of 5m bars  ·  $1 confirm target, "
               f"{st.SURE_THING_GOAL_PCT:.1f}% goal, "
               f"{HOLD_MIN:.0f}-min hold clock, "
               f"3%→1.5% trail once +2% (armed only after a green print)")
    if HOLD_MIN != st.SURE_THING_WINDOW_MIN:
        out.append(f"the only change from the shipped contract: a position may "
                   f"hold {HOLD_MIN:.0f} min instead of "
                   f"{st.SURE_THING_WINDOW_MIN:.0f}; the exit check still "
                   f"CALLS the exit at the end of that clock, and the "
                   f"{st.SURE_THING_WINDOW_MIN:.0f}-min arm/confirm expiry is "
                   "untouched")
    out.append(f"target +{ca.PROFIT_TARGET_PCT:.0f}%  ·  death line "
               f"-{ca.MAX_LOSS_PCT:.0f}%  ·  round trip {ca.COST_ROUNDTRIP_PCT:.2f}%")
    out.append("book: MANY AT ONCE — a pending row and a position PER PAIR, "
               "each sized from the free cash (balance − open notional) "
               "through the live entry_size_usd")
    if dropped:
        out.append(f"excluded for covering under {args.min_days:.0f} days of the "
                   "window (their feed only exists in its tail): "
                   + ", ".join(f"{p} {d:.1f}d" for p, d in dropped))
    out.append("")
    arms = (("gate", "SHIPPED (arm, then confirm)"),
            ("pre-gate", "PRE-GATE (fire at pre-breakout)"))
    views = ((m, lb, s, sl) for m, lb in arms
             for s, sl in ((False, "CONTINUOUS — never stops; prices the edge"),
                           (True, "CONTEST — halts at +12% or the death line")))
    for mode, label, stop, slabel in views:
        out.append(f"── {label} · {slabel} " + "─" * 12)
        out.append(f"{'tier':>6} {'arms':>5} {'fills':>5} {'exp':>4} "
                   f"{'trades':>6} {'win%':>5} {'net $':>10} {'final $':>11} "
                   f"{'%':>7}  verdict")
        for tier in sorted(ca.TIERS):
            b = books[(tier, mode, stop)]
            pct = (b.balance / b.start - 1.0) * 100.0
            if stop and b.hit_target:
                verdict = f"PASS — stopped at +12% on " \
                          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(b.peak_at))} UTC"
            elif stop and b.dead:
                verdict = f"FAIL — stopped at the -{ca.MAX_LOSS_PCT:.0f}% line"
            elif pct >= ca.PROFIT_TARGET_PCT:
                verdict = "PASS (+12% at the end)"
            elif b.hit_target:
                verdict = ("PASS (touched +12% on {}), then gave it back".format(
                    time.strftime("%Y-%m-%d %H:%M", time.gmtime(b.peak_at))))
            elif b.dead:
                verdict = "FAIL (under the death line)"
            else:
                verdict = f"open — {ca.PROFIT_TARGET_PCT - pct:.1f}% short"
            wr = (b.wins / b.trades * 100.0) if b.trades else 0.0
            out.append(f"{ca.TIERS[tier]['label']:>6} {b.arms:>5} {b.confirms:>5} "
                       f"{b.expired:>4} {b.trades:>6} {wr:>5.0f} {b.net:>+10.2f} "
                       f"{b.balance:>11,.2f} {pct:>+7.2f}  {verdict}")
            floor = b.start * (1.0 - ca.MAX_LOSS_PCT / 100.0)
            if b.trades:
                _day = pct / days if days else 0.0
                _eta = (f"{ca.PROFIT_TARGET_PCT / _day:.0f} days to +12%"
                        if _day > 0 else "this pace never reaches +12%")
                _bar = (f"  ·  the $1 bar +{sorted(b.bar_pct)[len(b.bar_pct) // 2]:.3f}%"
                        if b.bar_pct else "")
                out.append(f"       {b.net / b.trades:+.2f} per fire  ·  "
                           f"{_day:+.2f}%/day  ·  {_eta}{_bar}")
            out.append(f"       death line ${floor:,.2f} ("
                       f"-{ca.MAX_LOSS_PCT:.0f}%)  ·  cushion "
                       f"${b.balance - floor:+,.2f} "
                       f"({(b.balance - floor) / b.start * 100.0:+.2f}% of the book)")
            if b.peak_at:
                out.append("       peak ${:,.2f} ({}%) on {} UTC".format(
                    b.peak_bal, f"{b.peak_bal / b.start * 100.0 - 100.0:+.2f}",
                    time.strftime("%Y-%m-%d %H:%M", time.gmtime(b.peak_at))))
            if b.peak_open > 1:
                out.append(f"       peak {b.peak_open} positions open at once "
                           "(each sized from the free-cash carry)")
            if b.tiny:
                out.append(f"       {b.tiny} of {b.trades} fires were under $1 "
                           "notional: the 3% guard had squeezed risk to "
                           "nothing by then")
        med = [x for t in sorted(ca.TIERS)
               for x in books[(t, mode, stop)].armed_min]
        if med:
            med.sort()
            out.append(f"  armed→fill: median {med[len(med) // 2]:.0f} min "
                       f"over {len(med)} confirms")
        tiers = sorted(ca.TIERS)
        ex = books[(tiers[-1], mode, stop)].exits
        out.append("  exits: " + ("  ·  ".join(f"{k} {v}" for k, v in sorted(ex.items()))
                                  or "none"))
        out.append("")
    out.append("COVERAGE — every coin in volatility_universe.json")
    out.append("-" * 78)
    by_src: dict[str, int] = {}
    for _p, (_s, _n, _d) in meta.items():
        by_src[_s] = by_src.get(_s, 0) + 1
    out.append("  feeds: " + "  ·  ".join(f"{k} {v}" for k, v in sorted(by_src.items())))
    short = sorted(p for p, (_s, _n, d) in meta.items() if d < days - 1.0)
    if short:
        out.append(f"  · {len(short)} pair(s) cover less than the full window and "
                   "can only fire in its tail: "
                   + ", ".join(f"{p} {meta[p][2]:.1f}d" for p in short))
    for p in sorted(meta):
        s, n, d = meta[p]
        out.append(f"  {p:>13} {s:<9} {n:>5} bars  {d:>6.2f} days")
    out.append("")
    out.append("NOTES")
    out.append("-" * 78)
    out.append("  · the detector sees the last 720 bars at every step, exactly")
    out.append("    as the scanner feeds it; a longer window widens the EVIDENCE,")
    out.append("    never the detector's own 5m / 24-bar (2h) coil contract.")
    out.append("  · two views of every arm, same trades up to the point a book")
    out.append("    stops: CONTINUOUS runs the window out (what the edge is),")
    out.append("    CONTEST halts the book the moment it reaches +12% or the death")
    out.append("    line (what the rules would have done). A 'touched +12%' book")
    out.append("    is a PASS: the contest ends when the goal is reached, so what the")
    out.append("    continuous view shows afterwards is a book that declined to stop,")
    out.append("    not a pass that was taken back.")
    out.append("  · more time is not an open-ended position: the clock still")
    out.append("    calls the exit on the first bar past the hold window, so a trade")
    out.append("    that never reaches the goal is closed at the window's price.")
    out.append("  · the book holds several positions at once, one per pair, so")
    out.append("    a long hold no longer starves entries; each entry is sized")
    out.append("    against the FREE CASH still on the book, so the account can")
    out.append("    never be over-committed. The 3% guard sizes every entry and")
    out.append("    the fee is charged on both legs. Champion/mover funnels are")
    out.append("    not replayable.")
    if args.trace:
        for (tier, mode, stop), b in sorted(books.items()):
            out.append(f"")
            out.append(f"── TRADES {ca.TIERS[tier]['label']} {mode}"
                       f"{' stop' if stop else ''} " + "─" * 30)
            for p, e, x, s, mins, net, why in b.log:
                out.append(f"  {p:>11} {e:.6g} -> {x:.6g}  ${s:>8,.0f}  "
                           f"{mins:>6.0f}m  {net:>+8.2f}  {why}")
    rep = "\n".join(out)
    (ROOT / "_tiersim.txt").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
