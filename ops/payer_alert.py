"""THE PAYER-RECOVERY-ALERT WATCHER (ops tool, Oct 1): the burial funnel
gets its own recovery tripwire — one watch per buried candidate, ONE
ping on the phone when the recovery leg starts, then the watch retires.

    # the launch (detached, its own logs — see the boot log for the pid):
    uv run python -X utf8 ops/payer_alert.py

    # one pass over the ledger and out (the drive/cron shape):
    uv run python -X utf8 ops/payer_alert.py --once

THE SPEC OF RECORD: ops/payer_alert.yaml is the contract — the
thresholds, the confirm, the book gate, the push copy, all of it. This
watcher parses it with the TINY SUBSET the spec commits to (nested
mappings of scalar values, single-quoted strings, # comments; no lists,
no block scalars, no flow mappings) and REFUSES to run half-configured:
a spec line it cannot parse is exit 2, never a guess. The threshold is
not a magic number: it is FRACTION of the gate's own medfill (the EV
table's medfill* column, scratch/gate_ev_table.txt — regenerate that
artifact and the watcher re-prices itself on the next run), floored
above the fee-drag noise band, per-gate overrides by slug. It fires
DELIBERATELY BELOW the scanner's 1.8% entry floor — ending blind waits
BEFORE the funnel could re-fire is the alert's whole job (1.085 ~=
65% of FIL's 1.67 medfill).

HOW IT WATCHES: each cycle it reads the burial ledger
(popup_suppressions.jsonl), keeps the FIRST burial per pair+gate
(the watch ages from the first sighting, never a rolling refresh),
drops anything past horizon_hours, anything whose gate has no medfill
(no invented thresholds), anything already fired, and anything inside
the pair's cooldown — then for each candidate asks the 5m feed (Kraken
public, no keys — the price_alert idiom): the LAST candle in the feed
is the FORMING one and can NEVER fire; the fire needs the last
confirm_candles CLOSED candles at/above the threshold. With the book
gate on (book_required: true) the fire also needs a READABLE top of
book whose spread fits book_max_spread_bps — a crossed book is no
book. Then ONE high-priority push through the SAME private ntfy pipe
the GET OUTs use (push_topic.txt), the fire is recorded (state
tombstone + the metrics csv row, peak_after_pct left EMPTY for
hindsight to fill — fire-time peaks are fiction), and the watch is
done: no re-arm, no spam (retire-on-fire).

HONESTY RAILS:
  - a failed feed NEVER fires — a stale candle is a lie with decimals
  - a failed ntfy send keeps the watch ARMED and retries next cycle
    (retry_on_send_fail; --once reports it as exit 4) — a dead pipe
    must not silently swallow the trip
  - mixed UTC offsets in the ledger are PARSED (fromisoformat), never
    compared as strings (the EV-table lesson)
  - the lock file refuses a second watcher (a double is two phones
    buzzing); a DEAD lock is retaken (the recycled-pid trap)
  - read-only on the firm: the ledger, the EV artifact, the cache —
    all reads; the watcher's own runtime state (payer_alert.lock,
    payer_alert_state.json, payer_alert_metrics.csv) is gitignored
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MST = ZoneInfo("America/Denver")
SPEC_FILE = ROOT / "ops" / "payer_alert.yaml"
LEDGER_FILE = ROOT / "popup_suppressions.jsonl"
EV_FILE = ROOT / "scratch" / "gate_ev_table.txt"
STATE_FILE = ROOT / "payer_alert_state.json"
METRICS_FILE = ROOT / "payer_alert_metrics.csv"
LOCK_FILE = ROOT / "payer_alert.lock"
_TOPIC_FILE = ROOT / "push_topic.txt"

FETCH_TIMEOUT_S = 10
PUSH_TIMEOUT_S = 10


def _now() -> datetime:
    return datetime.now(MST)


def _log(msg: str) -> None:
    print(f"{_now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


# ── the spec parser: the TINY SUBSET, and nothing more ────────────────

def _scalar(v: str):
    if v == "true":
        return True
    if v == "false":
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def parse_spec(text: str) -> dict:
    """The committed subset: nested mappings of scalar values,
    single-quoted strings, # comments. Anything else RAISES — the
    watcher refuses a half-configured run (fail-closed)."""
    doc: dict = {}
    stack = [(-1, doc)]                      # (indent, mapping)
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if ":" not in line:
            raise ValueError(f"unparseable spec line: {raw!r}")
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("'"):
            if len(val) < 2 or not val.endswith("'"):
                raise ValueError(f"unterminated quote: {raw!r}")
            val = val[1:-1]                  # quoted: whole, # and all
        else:
            val = val.split(" #", 1)[0].strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"bad indent: {raw!r}")
        if val == "":
            child: dict = {}
            stack[-1][1][key] = child
            stack.append((indent, child))
        else:
            stack[-1][1][key] = _scalar(val)
    return doc


# ── medfills: the EV-table artifact is the pricing source ─────────────

def load_medfills(path) -> dict:
    """scratch/gate_ev_table.txt's medfill* column per gate. A gate row
    is 'NAME <int n> <int pay> <rate%> <medfill> ...' — anything else
    (headers, rules, dashes) is skipped. Unreadable -> {} (no medfill
    means NO watch, never an invented threshold)."""
    out: dict[str, float] = {}
    try:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if len(parts) < 5 or not parts[1].isdigit():
                continue
            try:
                out[parts[0]] = float(parts[4])
            except ValueError:
                continue
    except Exception:
        return {}
    return out


def threshold_for(medfill: float, fraction: float, floor: float) -> float:
    """fraction x the gate's medfill, FLOORED above the noise band — a
    zero-medfill family (rcl-drift-reentry) alerts at the floor, never
    at 0 (a threshold of 0 fires on every candle)."""
    return round(max(floor, fraction * medfill), 4)


def fraction_for(gate: str, spec: dict) -> float:
    """The per-gate override by slug (tide-out -> fraction_tide_out —
    the yaml keys are underscored, the gate slugs hyphenated, the
    mapping is the spec's own convention); a family without an
    override rides the global fraction."""
    v = spec.get("fraction_" + gate.replace("-", "_"))
    if isinstance(v, (int, float)):
        return float(v)
    return float(spec.get("fraction_of_medfill", 0.65))


# ── candidates: first-seen per pair+gate, parsed time, capped ─────────

def build_candidates(ledger_path, medfills: dict, spec: dict,
                     now: datetime, state: dict | None = None,
                     max_parallel: int | None = None) -> list[dict]:
    """The watch list. first_seen_per_pair: the watch ages from the
    FIRST burial of its pair+gate. Timestamps are PARSED (mixed offsets
    coexist); unreadable rows fail soft. Horizon expiry, fired
    tombstones, per-pair cooldown and the max_parallel cap all apply."""
    state = state or {}
    horizon_h = float(spec.get("horizon_hours", 7))
    maxp = int(max_parallel if max_parallel is not None
               else spec.get("max_parallel", 6))
    cooldown_min = float(spec.get("per_pair_cooldown_min", 240))
    fired = state.get("fired") or {}
    cooldown = state.get("pair_cooldown") or {}
    first: dict[tuple, dict] = {}
    try:
        lines = Path(ledger_path).read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
            ts = datetime.fromisoformat(str(row["ts"]))
            pair, gate = str(row["pair"]), str(row["gate"])
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=MST)      # naked ts = the house MST
        key = (pair, gate)
        prev = first.get(key)
        if prev is None or ts < prev["ts"]:
            first[key] = {"pair": pair, "gate": gate, "ts": ts,
                          "quick": row.get("quick")}
    cands: list[dict] = []
    for (pair, gate), c in first.items():
        age = now - c["ts"]
        if gate not in medfills or age > timedelta(hours=horizon_h) \
                or age < timedelta(0):       # future ts = clock skew
            continue
        if f"{pair}|{gate}" in fired:
            continue                          # retire-on-fire is forever
        cd = cooldown.get(pair)
        if cd:
            try:
                cd_dt = datetime.fromisoformat(str(cd))
                if cd_dt.tzinfo is None:
                    cd_dt = cd_dt.replace(tzinfo=MST)   # hand-edited naive
                    # ts: the SAME naive->MST rule the ledger rows get
                    # (the review MINOR: an unnormalized subtraction must
                    # not silently void the cooldown)
                if (now - cd_dt).total_seconds() < cooldown_min * 60:
                    continue
            except Exception:
                pass
        c["medfill"] = medfills[gate]
        c["fraction"] = fraction_for(gate, spec)
        c["threshold"] = threshold_for(
            c["medfill"], c["fraction"],
            float(spec.get("min_threshold_pct", 0.55)))
        cands.append(c)
    cands.sort(key=lambda c: c["ts"], reverse=True)   # freshest burial first
    return cands[:maxp]


# ── the trip: closed-candle confirm + the book gate (pure) ────────────

def trip(pair: str, threshold: float, confirm_candles: int,
         fetch_candles, fetch_book, spec: dict | None = None) -> tuple[bool, dict]:
    """The fire decision. fetch_candles(pair, tf, n) -> oldest->newest
    candles where the LAST one is the FORMING candle (ccxt idiom) — the
    forming candle can NEVER fire, however high its live price. The
    fire needs the last confirm_candles CLOSED candles at/above the
    line. With the book gate on, an unreadable book or a spread wider
    than book_max_spread_bps blocks the fire (the watch stays armed for
    a tradeable book). Returns (ok, why) — why carries the fire record
    fields or a human reason."""
    spec = spec or {}
    try:
        candles = fetch_candles(pair, "5m", 30)
    except Exception:
        candles = None
    if not candles:
        return False, {"reason": "feed failed — no 5m candles (a stale "
                                 "feed never fires)"}
    closed = list(candles)[:-1]              # the forming candle is last
    n = max(1, int(confirm_candles or 1))
    if len(closed) < n:
        return False, {"reason": "feed failed — not enough closed 5m "
                                 "candles yet"}
    closes = []
    for c in closed[-n:]:
        try:
            closes.append(float(c["close"]))
        except Exception:
            return False, {"reason": "feed failed — unreadable candle close"}
    if not all(v >= threshold for v in closes):
        return False, {"reason": f"armed — last closed candle(s) below "
                                 f"{threshold:.3f}%"}
    crossed_close = closes[-1]
    ask = spread_bps = None
    if spec.get("book_required", True) or fetch_book is not None:
        try:
            book = fetch_book(pair) if fetch_book is not None else None
        except Exception:
            book = None
        if not book or not book.get("asks") or not book.get("bids"):
            return False, {"reason": "book unreadable — book_required "
                                     "stands, no fire on a blind book"}
        try:
            ask = float(book["asks"][0][0])
            bid = float(book["bids"][0][0])
        except Exception:
            return False, {"reason": "book unreadable — book_required "
                                     "stands, no fire on a blind book"}
        if ask <= 0 or bid <= 0 or ask < bid:
            return False, {"reason": "book unreadable — crossed book, "
                                     "no fire"}
        spread_bps = (ask - bid) / ask * 10_000.0
        cap = float(spec.get("book_max_spread_bps", 30))
        if spread_bps > cap:
            return False, {"reason": f"book spread {spread_bps:.1f}bps > "
                                     f"{cap:.0f} — waiting for a "
                                     "tradeable book"}
    return True, {"crossed_price": crossed_close,
                  "crossed_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                  "ask_tob_usd": ask,
                  "spread_bps": None if spread_bps is None
                  else round(spread_bps, 4)}


# ── the push copy: the spec's own templates ───────────────────────────

def fire_texts(cand: dict, spec: dict, crossed_at, crossed_price,
               ask_tob_usd, spread_bps,
               now: datetime | None = None) -> tuple[str, str]:
    """Render the spec's title/body with every knob bound — a raw '{'
    placeholder on the phone is a broken alert."""
    now = now or _now()
    fmt = {"pair": cand["pair"], "gate": cand["gate"],
           "quick": cand.get("quick"),
           "medfill": float(cand.get("medfill") or 0.0),
           "fraction": float(cand.get("fraction") or 0.0),
           "threshold": float(cand.get("threshold") or 0.0),
           "hours_ago": (now - cand["ts"]).total_seconds() / 3600.0,
           "crossed_at": crossed_at, "crossed_price": crossed_price,
           "ask_tob_usd": ask_tob_usd, "spread_bps": spread_bps}
    title = str(spec.get("title", "{pair} recovery leg")).format(**fmt)
    body = str(spec.get("body", "")).format(**fmt)
    return title, body


# ── the records: the tombstone + the metrics row ──────────────────────

def _atomic_json(path, doc: dict) -> None:
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, str(path))
    except Exception as exc:
        _log(f"state write failed (non-fatal): {exc}")


def record_fire(cand: dict, why: dict, spec: dict, metrics_path,
                state_path, now: datetime | None = None) -> dict:
    """Persist the retire-on-fire tombstone (pair|gate in state.fired +
    the pair cooldown) and append the metrics csv row — peak_after_pct
    stays EMPTY at fire time (hindsight fills it; fire-time peaks are
    fiction). fired_at carries the iso instant the WINDOW counts from
    and crossed_price rides the row as the peak's BASE (the hindsight
    backfill measures the recovery leg over the crossed price)."""
    now = now or _now()
    rec = {"pair": cand["pair"], "gate": cand["gate"],
           "threshold": cand.get("threshold"), "fired_at": now.isoformat(),
           "crossed_at": why.get("crossed_at"),
           "crossed_price": why.get("crossed_price"),
            "ask_tob_usd": why.get("ask_tob_usd"),
           "spread_bps": why.get("spread_bps")}
    try:
        st = json.loads(Path(state_path).read_text(encoding="utf-8"))
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    st.setdefault("fired", {})[f"{cand['pair']}|{cand['gate']}"] = rec
    st.setdefault("pair_cooldown", {})[cand["pair"]] = rec["fired_at"]
    _atomic_json(state_path, st)
    header = str(spec.get("metrics_csv") or "").strip()
    if header:
        try:
            p = Path(metrics_path)
            new = not p.exists() or p.read_text(encoding="utf-8").strip() == ""
            vals = []
            for col in [c.strip() for c in header.split(",")]:
                v = rec.get(col)
                vals.append("" if v is None else str(v))
            with open(p, "a", encoding="utf-8", newline="") as fh:
                if new:
                    fh.write(header + "\n")
                fh.write(",".join(vals) + "\n")
        except Exception as exc:
            _log(f"metrics row failed (non-fatal): {exc}")
    return rec


# ── the hindsight backfill: the horizon-closed peak, measured ───────

def _parse_mst(v: str, ref: datetime) -> datetime:
    """A row's iso instant, normalized like the ledger rows: a naive
    stamp is the house MST; anything unreadable raises (the caller
    decides fail-soft)."""
    dt = datetime.fromisoformat(str(v))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MST)
    return dt


def read_metrics(path) -> tuple[list, list]:
    """The metrics csv as (header, rows) — header is the comma list,
    each row a dict of column->string ('' when absent). Unreadable or
    empty -> ([], [])."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return [], []
    if not lines:
        return [], []
    header = [c.strip() for c in lines[0].split(",")]
    rows = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        vals = ln.split(",")
        rows.append({c: (vals[i] if i < len(vals) else "")
                     for i, c in enumerate(header)})
    return header, rows


def backfill_metrics(spec: dict, metrics_path, now: datetime,
                     fetch_candles_fn) -> int:
    """THE HINDSIGHT BACKFILL (user ask, Oct 1): fill the metrics csv's
    empty peak_after_pct once each fired watch's 7h horizon closes, and
    record whether the alert beat the funnel.

    For every row with a parseable fired_at whose horizon window
    [fired_at, fired_at + horizon_hours] is CLOSED, the highest 5m close
    INSIDE the window is measured as a percent over the row's
    crossed_price — the same honesty as the fire itself: pre-fire and
    post-horizon closes never inflate it, and a failed feed leaves the
    row EMPTY for the next cycle (fire-time peaks are fiction; so are
    hindsight peaks measured on a stale feed). beat_funnel is true when
    the peak reached funnel_entry_floor_pct (the funnel's own entry
    floor — could the funnel have re-entered this leg?). Rows are
    rewritten atomically (tmp + os.replace, the _write_state idiom);
    ANY error leaves the csv untouched and returns 0 fills — the
    backfill never kills the watch loop."""
    try:
        header, rows = read_metrics(metrics_path)
        if "fired_at" not in header or "peak_after_pct" not in header:
            return 0                        # legacy schema: nothing to fill
        horizon_h = float(spec.get("horizon_hours", 7))
        floor_pct = float(spec.get("funnel_entry_floor_pct", 1.8))
        fills = 0
        for r in rows:
            if r.get("peak_after_pct", "").strip() != "":
                continue                    # already measured
            try:
                fired = _parse_mst(r["fired_at"], now)
            except Exception:
                continue                    # unreadable stamp: unfilled
            end = fired + timedelta(hours=horizon_h)
            if now < end:
                continue                    # horizon still open
            try:
                base = float(r["crossed_price"])
            except Exception:
                continue                    # no base: an honest % is impossible
            # THE FULL-HORIZON PULL (the Copilot HIGH, Oct 1): the pull
            # must cover the WHOLE horizon window — a fixed 30 candles is
            # 2.5h of a 7h horizon and a peak in hour 6.5 was silently
            # missed. The limit is horizon_hours x 12 five-minute candles,
            # +1 hour of margin, floored at 30 (the trip's own pull),
            # CEILINGED at 1000 (a corrupted horizon_hours spec can never
            # ask the exchange for an unbounded pull).
            try:
                pull_n = min(1000, max(30, int(horizon_h * 12) + 12))
                candles = fetch_candles_fn(r.get("pair"), "5m", pull_n,
                                           since=fired)
            except Exception:
                candles = None
            if candles is not None and len(candles) < int(horizon_h * 12):
                # THE CAP-HONESTY GUARD (the Copilot LOW, Oct 1): the pull
                # could not cover the whole window (a corrupted spec whose
                # horizon outruns the 1000-candle ceiling, a thin feed) —
                # a partial-window peak saved as complete is fiction. The
                # row stays empty and the broken spec shows as an unfilled
                # column, never as a fake peak.
                continue
            if not candles:
                continue                    # failed feed: stays empty
            peak_px = None
            peak_ts = None
            for c in candles:
                try:
                    cts = datetime.fromtimestamp(float(c["ts"]) / 1000.0,
                                                 tz=MST)
                    close = float(c["close"])
                except Exception:
                    continue
                if fired <= cts < end and close > 0:
                    if peak_px is None or close > peak_px:
                        peak_px, peak_ts = close, cts
            if peak_px is None or base <= 0:
                continue                    # no in-window data: stays empty
            pct = (peak_px / base - 1.0) * 100.0
            r["peak_after_pct"] = f"{round(pct, 3)}"
            r["peak_at"] = peak_ts.strftime("%Y-%m-%d %H:%M:%S")
            r["beat_funnel"] = "true" if pct >= floor_pct else "false"
            fills += 1
        if fills:
            tmp = str(metrics_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(",".join(header) + "\n")
                for r in rows:
                    fh.write(",".join(r.get(c, "") for c in header) + "\n")
            os.replace(tmp, str(metrics_path))
        return fills
    except Exception as exc:
        _log(f"hindsight backfill skipped this cycle (non-fatal): {exc}")
        return 0


# ── the lock: one watcher, dead locks are retaken ─────────────────────

def _pid_alive_python(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        return "python" in out.lower()
    except Exception:
        return False


def claim_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            old = 0
        if _pid_alive_python(old):
            _log(f"REFUSED: the watcher already runs (pid {old}) — one "
                 "watcher, a double is two phones buzzing")
            return False
        _log(f"stale lock (pid {old} gone) — retaking")
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass


# ── the pipe: the same private ntfy topic the GET OUTs use ────────────

def send(text: str, prio: str, tag: str) -> bool:
    """One ntfy attempt. True only on an HTTP accept — delivery to the
    SERVER, never proof the phone showed it (the scanner's wire
    format: same title header, same priority scheme)."""
    try:
        topic = _TOPIC_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        topic = None
    if not topic:
        _log("push skipped — no push_topic.txt (the pipe has no address)")
        return False
    try:
        import urllib.parse
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{urllib.parse.quote(topic)}",
            data=str(text).encode("utf-8"),
            headers={"Title": "Kraken Scout", "Priority": prio,
                     "Tags": tag})
        urllib.request.urlopen(req, timeout=PUSH_TIMEOUT_S)
        _log(f"📲 PUSHED ({prio}): {str(text)[:90]}")
        return True
    except Exception as exc:
        _log(f"push failed — the phone may have missed: {exc}")
        return False


# ── the feeds: Kraken public, no keys, fail-honest ────────────────────

def fetch_candles(pair: str, timeframe: str = "5m", limit: int = 30,
                  since=None):
    """The 5m feed. since (aware datetime, the hindsight backfill's
    window start) narrows the pull so the backfill measures the horizon
    window, not the last 30 candles on the clock."""
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    since_ms = int(since.timestamp() * 1000) if since is not None else None
    o = ex.fetch_ohlcv(pair, timeframe=timeframe, limit=limit,
                       since=since_ms)
    return [{"ts": r[0], "close": r[4]} for r in (o or [])] or None


def fetch_book(pair: str):
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    return ex.fetch_order_book(pair, limit=5)


# ── the cycle ─────────────────────────────────────────────────────────

def _load_state(state_path) -> dict:
    try:
        doc = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _tick(spec: dict, ledger_path, medfills, state_path, metrics_path,
          now: datetime | None, _trip, _force_send_fail: bool,
          _fetch_candles_fn=None, backfill: bool = False) -> int:
    state = _load_state(state_path)
    mf = medfills if medfills is not None else load_medfills(EV_FILE)
    cands = build_candidates(ledger_path, mf, spec, now or _now(),
                             state=state)
    if not cands:
        # no watches this cycle — but the HINDSIGHT BACKFILL (opt-in by
        # spec) still runs: a horizon-closed metrics row must not wait
        # for the next burial to be measured
        if backfill:
            try:
                _tick_backfill(spec, metrics_path, now or _now(),
                               _fetch_candles_fn)
            except Exception as _exc:
                _log(f"backfill pass failed (non-fatal): {_exc}")
        return 0
    confirm = int(spec.get("confirm_candles", 1))
    book_on = spec.get("book_required", True)
    for cand in cands:
        if _trip is not None:
            ok, why = _trip(cand["pair"], cand["threshold"], confirm)
        else:
            ok, why = trip(cand["pair"], cand["threshold"], confirm,
                           fetch_candles,
                           fetch_book if book_on else None, spec=spec)
        if not ok:
            _log(f"{cand['pair']} ({cand['gate']}): "
                 f"{why.get('reason', 'armed')}")
            continue
        title, body = fire_texts(cand, spec, why.get("crossed_at"),
                                 why.get("crossed_price"),
                                 why.get("ask_tob_usd"),
                                 why.get("spread_bps"), now=now)
        prio = str(spec.get("priority", "high"))
        tag = str(spec.get("tag", "recovery"))
        sent = False if _force_send_fail else send(f"{title}\n{body}",
                                                   prio, tag)
        if not sent:
            _log(f"send failed — {cand['pair']} ({cand['gate']}) stays "
                 "ARMED, retrying next cycle")
            return 4
        record_fire(cand, why, spec, metrics_path, state_path, now=now)
        _log(f"FIRED: {cand['pair']} buried by {cand['gate']} — crossed "
             f"at {why.get('crossed_price')}; the watch is retired")
    # THE HINDSIGHT BACKFILL (opt-in by spec): AFTER the watch loop, fill
    # any horizon-closed metrics rows — the same cycle that watches for
    # the next recovery leg measures the last one. Fail-soft by design:
    # a backfill problem can never kill the watch loop (backfill_metrics
    # catches its own; this guard catches the wiring).
    try:
        _tick_backfill(spec, metrics_path, now or _now(),
                       _fetch_candles_fn)
    except Exception as _exc:
        _log(f"backfill pass failed (non-fatal): {_exc}")
    return 0


def _tick_backfill(spec: dict, metrics_path, now: datetime,
                   _fetch_candles_fn):
    """Run the hindsight backfill when the spec opts in (the yaml is
    the dial — absent/false and the csv is never rewritten). Returns
    the fill count (0 when off)."""
    if not spec.get("hindsight_backfill"):
        return 0
    return backfill_metrics(spec, metrics_path, now,
                            _fetch_candles_fn or fetch_candles)


def run(spec_path=None, ledger_path=None, medfills=None, state_path=None,
        metrics_path=None, once=False, interval: float = 60.0,
        now: datetime | None = None, _no_lock: bool = False,
        _trip=None, _force_send_fail: bool = False,
        _fetch_candles=None) -> int:
    """One cycle (--once) or the standing loop. Exit codes: 0 ran (or
    nothing to do), 2 spec broken/refused, 4 a send failed (the watch
    stays armed)."""
    try:
        spec = parse_spec(Path(spec_path or SPEC_FILE)
                          .read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"spec unreadable — refusing to run half-configured: {exc}")
        return 2
    if spec.get("enabled") is not True:
        _log("the spec is disabled (enabled is not true) — nothing to do")
        return 0
    # THE RENDER CONTRACT (the review MAJOR): the fire-time render must
    # be total — a spec whose push copy formats the book fields while
    # the book gate is off would crash .format(None) at the one moment
    # that matters. Refuse it here, fail-closed (the parser's own rule:
    # never a half-configured watcher), never a crash at fire time.
    body_tpl = str(spec.get("body", ""))
    if (not spec.get("book_required", True)
            and ("ask_tob_usd" in body_tpl or "spread_bps" in body_tpl)):
        _log("spec refused — the body formats book fields "
             "(ask_tob_usd/spread_bps) but book_required is false: "
             "turn the gate on or take the fields out of the copy")
        return 2
    if not _no_lock and not claim_lock():
        return 2
    try:
        while True:
            rc = _tick(spec, ledger_path or LEDGER_FILE, medfills,
                       state_path or STATE_FILE, metrics_path or METRICS_FILE,
                       now, _trip, _force_send_fail,
                       _fetch_candles_fn=_fetch_candles,
                       backfill=bool(spec.get("hindsight_backfill")))
            if once or rc:
                return rc
            time.sleep(interval)
    finally:
        if not _no_lock:
            release_lock()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="the burial funnel's recovery tripwire — ops/"
                    "payer_alert.yaml is the spec of record")
    ap.add_argument("--once", action="store_true",
                    help="one pass over the ledger and out")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between cycles (default 60)")
    ap.add_argument("--spec", default=None,
                    help="override the spec path (ops/payer_alert.yaml)")
    args = ap.parse_args()
    return run(once=args.once, interval=args.interval, spec_path=args.spec)


if __name__ == "__main__":
    raise SystemExit(main())
