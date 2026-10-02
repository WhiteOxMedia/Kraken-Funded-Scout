"""THE PAYER-RECOVERY-ALERT DRIVE (the ops/payer_alert.yaml watcher):
hermetic — NO subprocesses, NO network, NO repo runtime state written.
The spec of record (ops/payer_alert.yaml) is the contract; the drive
pins the watcher's reading of it, the arithmetic, and the at-most-once
fire — every section RED before the watcher existed (the drive was
written first and failed on ImportError).

Sections:
  1. the spec parser (the TINY SUBSET: nested scalar mappings,
     single-quoted strings, # comments — and the COMMITTED yaml itself
     must parse: the watcher can never drift from its own contract)
  2. medfills from the EV-table artifact + the threshold arithmetic
     (fraction of the gate's OWN medfill, floored above the noise band,
     per-gate overrides by slug)
  3. candidates from the burial ledger (first_seen_per_pair, PARSED
     timestamps — mixed offsets never compared lexically, horizon
     expiry, max_parallel, per-pair cooldown, retire-on-fire)
  4. the trip (5m CLOSED-candle confirm — the forming candle can never
     fire, feed failure fails honest, the book gate, the templates)
  5. the watcher's rails (disabled/broken spec, at-most-once, lock)
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def _find(rel: str) -> Path:
    """The pinned-asset search: the drive's own dir first (the plain
    repo-root run), then the gate's PYTHONPATH layers in order (the
    STAGED export, the HEAD archive — the committed contract itself —
    then the working repo). The gate exports drives WITHOUT their
    unstaged neighbors, so a pin on a committed file must look where
    the layers actually are; the first hit wins, the honest miss
    returns the BASE path so the pin FAILS with a real path shown."""
    cands = [BASE / rel]
    for layer in os.environ.get("PYTHONPATH", "").split(";"):
        if layer.strip():
            cands.append(Path(layer.strip()) / rel)
    for c in cands:
        if c.exists():
            return c
    return BASE / rel


import ops.payer_alert as pa

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


TMP = Path(tempfile.mkdtemp(prefix="_payalrt_"))
MST = timezone(timedelta(hours=-6))


def _mst(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 1, h, m, tzinfo=MST)


# ════════ 1. THE SPEC PARSER ════════
print("== 1. the spec parser: the TINY SUBSET ==")

spec_doc = pa.parse_spec(_find("ops/payer_alert.yaml").read_text(encoding="utf-8"))
check(1.1, spec_doc.get("name") == "payer-recovery-alert"
      and spec_doc.get("enabled") is True
      and spec_doc.get("fraction_of_medfill") == 0.65
      and spec_doc.get("confirm_candles") == 1
      and spec_doc.get("book_required") is True
      and spec_doc.get("horizon_hours") == 7
      and spec_doc.get("max_parallel") == 6
      and spec_doc.get("per_pair_cooldown_min") == 240
      and spec_doc.get("min_threshold_pct") == 0.55,
      "the COMMITTED spec parses: typed scalars (bool/int/float), not strings")
check(1.2, spec_doc.get("title")
      == "{pair} recovery leg — {threshold:.3f}% crossed ({gate})",
      "the quoted title survives WHOLE (braces, parens, the em dash)")
check(1.3, "book: tob {ask_tob_usd:.0f} ask" in str(spec_doc.get("body"))
      and str(spec_doc.get("body", "")).startswith("buried {hours_ago:.1f}h"),
      "the quoted body survives WHOLE — inner ' - ' and 'book: ' colon "
      "inside the quotes never split the value")
check(1.4, spec_doc.get("fraction_sure_thing_deep_veto") == 0.70
      and spec_doc.get("fraction_rcl_drift_reentry") == 0.60
      and spec_doc.get("fraction_tide_out") == 0.70
      and spec_doc.get("fraction_stale_move") == 0.60
      and spec_doc.get("metrics_csv")
      == "pair,gate,threshold,crossed_at,crossed_price,fired_at,"
         "peak_after_pct,peak_at,beat_funnel,ask_tob_usd,spread_bps"
      and spec_doc.get("hindsight_backfill") is True
      and spec_doc.get("funnel_entry_floor_pct") == 1.8,
      "the per-gate override keys and the csv header parse — the header "
      "carries the hindsight columns (fired_at, peak_after_pct, peak_at, "
      "beat_funnel) and the backfill is ON by spec")
nested = pa.parse_spec(
    "top: 1\n"
    "# a comment line\n"
    "section:\n"
    "  inner: 'kept # whole'\n"
    "  deep: 2.5\n"
    "after: plain value\n")
check(1.5, nested.get("top") == 1
      and nested.get("section") == {"inner": "kept # whole", "deep": 2.5}
      and nested.get("after") == "plain value",
      "the subset's NESTED scalar mappings parse (2-space indent) and a "
      "trailing # inside quotes is never a comment")
try:
    pa.parse_spec("this line has no colon\n")
    _bad = False
except ValueError:
    _bad = True
check(1.6, _bad,
      "an unparseable line RAISES — a broken spec fails CLOSED (the "
      "watcher refuses to run half-configured), never guesses")

# ════════ 2. MEDFILLS + THRESHOLDS ════════
print("== 2. medfills (the EV-table artifact) + thresholds ==")

mf = pa.load_medfills(_find("scratch/gate_ev_table.txt"))
check(2.1, all(g in mf for g in
               ("sure-thing-deep-veto", "rcl-drift-reentry", "tide-out",
                "stale-move", "deep-veto"))
      and all(isinstance(v, float) and 0.0 <= v <= 10.0 for v in mf.values()),
      "the COMMITTED artifact parses to float medfills for the spec's "
      "override families (structure pinned — the artifact regenerates)")
_tsv = ("gate,medfill\n"
        "GATE                 n  pay   rate medfill* dud-med    cap    EV%\n"
        "------------------------------------------------------------------------------\n"
        "tide-out                11    6    55%    1.77   0.06    74%   0.99\n"
        "rcl-drift-reentry        2    0     0%    0.00   0.38     0%   0.38\n")
_tsf = TMP / "ev_synth.txt"
_tsf.write_text(_tsv, encoding="utf-8")
check(2.2, pa.load_medfills(_tsf) == {"tide-out": 1.77, "rcl-drift-reentry": 0.0},
      "a synthetic artifact parses: dashes/headers skipped, the medfill* "
      "column (not EV, not dud-med) is the value taken")
check(2.3, pa.threshold_for(0.0, 0.60, 0.55) == 0.55
      and pa.threshold_for(1.67, 0.60, 0.55) == 1.002
      and pa.threshold_for(2.29, 0.70, 0.55) == 1.603,
      "threshold = fraction x medfill, FLOORED at the noise band — the "
      "rcl-drift family (medfill 0.00) alerts at the floor, never at 0")
spec2 = dict(spec_doc)
check(2.4, pa.fraction_for("sure-thing-deep-veto", spec2) == 0.70
      and pa.fraction_for("deep-veto", spec2) == 0.65
      and pa.fraction_for("totally-unknown-gate", spec2) == 0.65,
      "per-gate override by slug (sure-thing-deep-veto -> "
      "fraction_sure_thing_deep_veto); missing family -> the global fraction")

# ════════ 3. CANDIDATES FROM THE LEDGER ════════
print("== 3. candidates: first-seen, parsed time, horizon, caps ==")


def _row(ts_iso, pair, gate, quick=60.0):
    return {"ts": ts_iso, "pair": pair, "gate": gate, "quick": quick,
            "detail": "x", "rot": "00-04"}


NOW = _mst(12, 0)
mf2 = {"tide-out": 1.77, "stale-move": 1.67, "deep-veto": 1.83}
LEDGER = [
    _row("2026-10-01T11:30:00-06:00", "FIL/USD", "tide-out"),            # 0.5h old
    _row("2026-10-01T10:00:00-06:00", "FIL/USD", "tide-out"),            # FIRST seen wins
    _row("2026-10-01T03:00:00-06:00", "OLD/USD", "stale-move"),          # 9h — past horizon
    _row("2026-10-01T11:45:00-06:00", "QNT/USD", "mystery-gate"),        # no medfill
    _row("2026-10-01T11:50:00+00:00", "UTC/USD", "deep-veto"),           # mixed offset
    _row("garbage-ts", "BAD/USD", "tide-out"),                           # unreadable ts
]
_ledf = TMP / "led.jsonl"
_ledf.write_text("\r\n".join(json.dumps(r) for r in LEDGER) + "\r\n\r\n",
                 encoding="utf-8")
cands = pa.build_candidates(_ledf, mf2, spec2, NOW, state={})
_by = {(c["pair"], c["gate"]): c for c in cands}
check(3.1, ("FIL/USD", "tide-out") in _by
      and _by[("FIL/USD", "tide-out")]["ts"] == datetime.fromisoformat("2026-10-01T10:00:00-06:00"),
      "first_seen_per_pair: the watch ages from the FIRST burial, not the last")
check(3.2, ("UTC/USD", "deep-veto") in _by
      and _by[("UTC/USD", "deep-veto")]["ts"].utcoffset() == timedelta(0),
      "timestamps are PARSED (fromisoformat) — a +00:00 row coexists with "
      "-06:00 rows, never lexically compared (the EV-table lesson)")
check(3.3, ("OLD/USD", "stale-move") not in _by
      and ("QNT/USD", "mystery-gate") not in _by
      and ("BAD/USD", "tide-out") not in _by,
      "past-horizon buries expire, medfill-less gates are never watched "
      "(no invented threshold), unreadable ts rows fail soft")
check(3.4, _by[("FIL/USD", "tide-out")]["threshold"] == 1.239
      and _by[("UTC/USD", "deep-veto")]["threshold"] == round(1.83 * 0.65, 4),
      "each candidate carries its OWN threshold (per-gate fraction x its "
      "medfill, floored)")
cands_full = pa.build_candidates(_ledf, mf2, spec2, NOW, state={},
                                 max_parallel=1)
check(3.5, len(cands_full) == 1 and cands_full[0]["pair"] == "FIL/USD",
      "max_parallel caps the watches (most recent PARSED instant first "
      "— the +00:00 row's MST instant is 05:50, older than FIL's 10:00)")
STATE = {"fired": {"FIL/USD|tide-out": {"fired_at": "x"}},
         "pair_cooldown": {"UTC/USD": (NOW - timedelta(minutes=60)).isoformat()}}
check(3.6, pa.build_candidates(_ledf, mf2, spec2, NOW, state=STATE) == [],
      "retire-on-fire: a fired pair|gate NEVER returns; per-pair cooldown "
      "(60min < 240) blocks the pair's other gates too")
STATE2 = {"fired": {}, "pair_cooldown":
          {"UTC/USD": (NOW - timedelta(minutes=300)).isoformat()}}
check(3.7, ("UTC/USD", "deep-veto") in
      {(c["pair"], c["gate"]) for c in pa.build_candidates(
          _ledf, mf2, spec2, NOW, state=STATE2)},
      "a cooldown past its 240min window reopens the pair")

# ════════ 4. THE TRIP ════════
print("== 4. the trip: closed-candle confirm + the book gate ==")
THR = 1.085


def _candles(closes):
    """A 5m feed shape: last candle is the FORMING one (ccxt idiom)."""
    now_ms = 1790881200000
    return [{"ts": now_ms - 300_000 * (len(closes) - i), "close": c}
            for i, c in enumerate(closes)]


_NOBOOK = {"book_required": False}
ok, why = pa.trip("FIL/USD", THR, 1,
                  lambda p, tf, n: _candles([1.0, 1.0, 1.09, 99.0]),
                  None, spec=_NOBOOK)
check(4.1, ok and str(why.get("crossed_price")) == "1.09",
      "the 5m CLOSED candle's close crosses -> confirm — the FORMING "
      "candle (99.0, ten times higher) is EXCLUDED")
ok, why = pa.trip("FIL/USD", THR, 1,
                  lambda p, tf, n: _candles([1.0, 1.0, 1.05, 99.0]),
                  None, spec=_NOBOOK)
check(4.2, not ok, "the closed candle below the line -> still armed")
try:
    pa.trip("FIL/USD", THR, 1, lambda p, tf, n: None, None, spec=_NOBOOK)
    _feed_ok = False
except Exception:
    _feed_ok = False
ok, why = pa.trip("FIL/USD", THR, 1, lambda p, tf, n: None, None,
                  spec=_NOBOOK)
check(4.3, not ok and "feed" in why.get("reason", "").lower(),
      "a failed feed NEVER fires — a stale candle is a lie with decimals")
BOOK_OK = {"bids": [[1.08, 5]], "asks": [[1.081, 5]]}          # ~9bps
BOOK_WIDE = {"bids": [[1.05, 5]], "asks": [[1.13, 5]]}         # ~750bps
ok, why = pa.trip("FIL/USD", THR, 1,
                  lambda p, tf, n: _candles([1.0, 1.09, 1.10]),
                  lambda p: BOOK_OK)
check(4.4, ok and abs(why["spread_bps"] - 9.259) < 0.01 and why["ask_tob_usd"] == 1.081,
      "book_required satisfied: tob ask + spread in bps ride the fire record")
ok, why = pa.trip("FIL/USD", THR, 1,
                  lambda p, tf, n: _candles([1.0, 1.09, 1.10]),
                  lambda p: BOOK_WIDE)
check(4.5, not ok and "spread" in why.get("reason", "").lower(),
      "a spread wider than book_max_spread_bps blocks the fire — the "
      "watch stays armed for a tradeable book")
ok, why = pa.trip("FIL/USD", THR, 1,
                  lambda p, tf, n: _candles([1.0, 1.09, 1.10]),
                  lambda p: None)
check(4.6, not ok and "book" in why.get("reason", "").lower(),
      "an unreadable book blocks the fire (book_required: true)")
t, b = pa.fire_texts(_by[("FIL/USD", "tide-out")], spec2,
                     crossed_at="2026-10-01 11:31:00", crossed_price=1.09,
                     ask_tob_usd=1.081, spread_bps=9.3, now=NOW)
check(4.7, t == "FIL/USD recovery leg — 1.239% crossed (tide-out)"
      and "buried 2.0h ago by tide-out" in b and "medfill 1.77%" in b
      and "70% of medfill" in b and "tob 1 ask / 9.3 bps" in b
      and "(quick 60.0)" in b,
      "the spec's own templates render EVERY knob (formats included) — "
      "a placeholder left raw '{' is a push the phone can't read")

_mcsv = TMP / "metrics.csv"
rec = pa.record_fire({"pair": "FIL/USD", "gate": "tide-out",
                      "threshold": 1.239, "quick": 60.0},
                     {"crossed_at": "2026-10-01 11:31:00",
                      "crossed_price": 1.09, "ask_tob_usd": 1.081,
                      "spread_bps": 9.3},
                     spec2, _mcsv, TMP / "st.json", NOW)
check(4.8, rec["fired_at"]
      and json.loads((TMP / "st.json").read_text(encoding="utf-8"))
      ["fired"]["FIL/USD|tide-out"]["threshold"] == 1.239
      and (TMP / "st.json").exists()
      and _mcsv.read_text(encoding="utf-8").splitlines()[0]
      == "pair,gate,threshold,crossed_at,crossed_price,fired_at,"
         "peak_after_pct,peak_at,beat_funnel,ask_tob_usd,spread_bps"
      and _mcsv.read_text(encoding="utf-8").splitlines()[1]
      == f"FIL/USD,tide-out,1.239,2026-10-01 11:31:00,1.09,"
         f"{NOW.isoformat()},,,,1.081,9.3",
      "the fire RECORDS: state gets the pair|gate tombstone (retire-on-"
      "fire is PERSISTED), the csv header lands once + one row — "
      "peak_after_pct empty at fire time (hindsight fills it; fire-time "
      "peaks are fiction)")

# ════════ 5. THE RAILS ════════
print("== 5. the watcher's rails ==")

_specf = TMP / "spec.yaml"
_specf.write_text("name: payer-recovery-alert\nenabled: false\n",
                  encoding="utf-8")
rc = pa.run(spec_path=_specf, state_path=TMP / "s2.json",
            metrics_path=TMP / "m2.csv", once=True, _no_lock=True)
check(5.1, rc == 0 and not (TMP / "s2.json").exists(),
      "a disabled spec exits 0 without touching ledger, feed or state")
_specf.write_text("this line has no colon\n", encoding="utf-8")
rc = pa.run(spec_path=_specf, state_path=TMP / "s3.json",
            metrics_path=TMP / "m3.csv", once=True, _no_lock=True)
check(5.2, rc == 2,
      "a broken spec fails CLOSED (exit 2) — never a half-configured watcher")

# at-most-once THROUGH the real cycle path (stubbed feed, one tick):
_specf.write_text(
    "enabled: true\nhorizon_hours: 7\nmax_parallel: 6\n"
    "per_pair_cooldown_min: 240\nfraction_of_medfill: 0.65\n"
    "min_threshold_pct: 0.55\nconfirm_candles: 1\nbook_required: true\n"
    "book_max_spread_bps: 30\npriority: high\ntag: recovery\n"
    "title: '{pair} crossed'\nbody: 'b'\nmetrics_csv: 'pair,gate'\n"
    "topic_file: push_topic.txt\n", encoding="utf-8")
_led2 = TMP / "led2.jsonl"
_led2.write_text(json.dumps(_row(
    (NOW - timedelta(minutes=30)).isoformat(), "FIL/USD", "tide-out")),
    encoding="utf-8")
# led3: an UNFIRED pair (XMR) for the send-fail rail — the tombstones
# are pair|gate, so sharing state with 5.3 leaves XMR armed
(TMP / "led3.jsonl").write_text(json.dumps(_row(
    (NOW - timedelta(minutes=30)).isoformat(), "XMR/USD", "tide-out")),
    encoding="utf-8")
mf3 = {"tide-out": 1.77}
_sent = []
pa.send = lambda text, prio, tag: (_sent.append(text) or True)
_st2 = TMP / "st2.json"
rc1 = pa.run(spec_path=_specf, ledger_path=_led2, medfills=mf3,
             state_path=_st2, metrics_path=TMP / "m4.csv", once=True,
             now=NOW, _no_lock=True,
             _trip=lambda pair, thr, cc: (True, {
                 "crossed_price": "1.09", "crossed_at": "t",
                 "ask_tob_usd": 1.081, "spread_bps": 9.3}))
rc2 = pa.run(spec_path=_specf, ledger_path=_led2, medfills=mf3,
             state_path=_st2, metrics_path=TMP / "m4.csv", once=True,
             now=NOW, _no_lock=True,
             _trip=lambda pair, thr, cc: (True, {
                 "crossed_price": "1.10", "crossed_at": "t",
                 "ask_tob_usd": 1.081, "spread_bps": 9.3}))
check(5.3, rc1 == 0 and rc2 == 0 and len(_sent) == 1,
      "AT-MOST-ONCE through the real cycle: two runs over the same "
      "burial send exactly ONE push (the second run sees the tombstone)")
rc3 = pa.run(spec_path=_specf, ledger_path=TMP / "led3.jsonl",
             medfills=mf3, state_path=_st2, metrics_path=TMP / "m4.csv",
             once=True, now=NOW, _no_lock=True,
             _trip=lambda pair, thr, cc: (True, {
                 "crossed_price": "1.10", "crossed_at": "t",
                 "ask_tob_usd": 1.081, "spread_bps": 9.3}),
             _force_send_fail=True)
check(5.4, rc3 == 4 and len(_sent) == 1,
      "a failed send keeps the watch ARMED and reports it (exit 4) — "
      "a dead pipe must not silently swallow the trip "
      "(an UNFIRED pair's ledger row; the sent count stays at one)")
# 5.5 (CodeRabbit MAJOR): the fire-time render must be TOTAL — a spec
# whose push copy formats the book fields while the book gate is off
# would crash .format(None) at the one moment that matters. The spec
# is REFUSED up front (fail-closed, the parser's own rule).
_specf.write_text(
    "enabled: true\nbook_required: false\n"
    "title: '{pair} crossed'\n"
    "body: 'tob {ask_tob_usd:.0f} / {spread_bps:.1f} bps'\n",
    encoding="utf-8")
rc5 = pa.run(spec_path=_specf, ledger_path=_led2, medfills=mf3,
             state_path=TMP / "st5.json", metrics_path=TMP / "m5.csv",
             once=True, now=NOW, _no_lock=True)
check(5.5, rc5 == 2 and not (TMP / "st5.json").exists(),
      "a spec whose copy formats book fields WITHOUT book_required is "
      "refused (exit 2) — never a crash at fire time (state untouched)")
# 5.6 (CodeRabbit MINOR): a hand-edited NAIVE cooldown timestamp is
# normalized to MST like the ledger rows — the cooldown is HONORED,
# never silently skipped by a TypeError.
STATE4 = {"fired": {"FIL/USD|tide-out": {"fired_at": "x"}},
          "pair_cooldown":
          {"UTC/USD": (NOW - timedelta(minutes=60)).replace(tzinfo=None)
           .isoformat()}}
check(5.6, pa.build_candidates(_ledf, mf2, spec2, NOW, state=STATE4) == [],
      "a naive cooldown ts still honors the 240min window (naive -> MST, "
      "same rule as the ledger rows)")

# ════════ 6. THE HINDSIGHT BACKFILL ════════
# (user ask, Oct 1: fill the metrics csv's empty peak_after_pct once
# each fired watch's 7h horizon closes, and record whether the alert
# beat the funnel — the recovery leg's realized peak vs the funnel's
# own 1.8% entry floor.)
print("== 6. the hindsight backfill: horizon-closed peaks + the "
      "beat-the-funnel verdict ==")

HORIZON_H = 7
OPEN_AT = datetime(2026, 9, 30, 20, 0, tzinfo=MST)       # horizon still OPEN
FIRED_AT = datetime(2026, 9, 30, 10, 0, tzinfo=MST)      # 26h before NOW
LATER = datetime(2026, 10, 1, 5, 0, tzinfo=MST)          # horizon CLOSED
MUCH_LATER = datetime(2026, 10, 9, 0, 0, tzinfo=MST)     # a 200h horizon CLOSED
_HDR = ("pair,gate,threshold,crossed_at,crossed_price,fired_at,"
        "peak_after_pct,peak_at,beat_funnel,ask_tob_usd,spread_bps")


def _csv_with(rows: str) -> Path:
    p = TMP / f"m6_{abs(hash(rows)) % 99999}.csv"
    p.write_text(_HDR + "\n" + rows, encoding="utf-8")
    return p


def _candle(ts: datetime, close: float) -> dict:
    return {"ts": int(ts.timestamp() * 1000), "close": close}


# a feed that knows FIL: closes inside the 7h window post-fire; the
# 2.2 close = +2.5% over the 1.09 crossed price; a pre-fire 1.0 close
# and a post-horizon 3.0 close must NEVER inflate the peak
_C6 = ([_candle(FIRED_AT - timedelta(minutes=5), 1.0)]
       + [_candle(FIRED_AT + timedelta(minutes=5 * i),
                  1.10 if i == 6 else
                  1.1173 if i == 24 else
                  3.0 if i == 96 else 1.0)
           for i in range(97)])   # a WINDOW'S worth of closes: the +30m
                                  # 1.10, the +2h 1.1173 peak, the +8h 3.0
                                  # OUTSIDE the horizon (also beyond the
                                  # pull's limit cut), baseline 1.0 between


def _feed6(pairs):
    def f(pair, tf, limit, since=None):
        got = list(pairs.get(pair) or [])
        return got[:limit] if limit else got   # respects the pull size
    return f


# 6.1 the empty fill: horizon still OPEN -> the row is left EMPTY
# (fired 2h ago — the feed even carries an in-window close, so the
# horizon gate itself is what keeps the row unfilled)
_m61 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{(NOW - timedelta(hours=2)).isoformat()},,,,,\n")
n = pa.backfill_metrics(spec2, _m61, NOW, _feed6({"FIL/USD": _C6}))
_rows61 = pa.read_metrics(_m61)[1]
check(6.1, n == 0 and len(_rows61) == 1
      and _rows61[0]["peak_after_pct"] == ""
      and _rows61[0]["beat_funnel"] == "",
      "a fired watch whose 7h horizon is still OPEN stays empty — "
      "fire-time peaks are fiction, hindsight waits for the close")
# 6.2 the fill: horizon CLOSED -> peak measured in the window only
_m62 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(spec2, _m62, LATER, _feed6({"FIL/USD": _C6}))
_rows62 = pa.read_metrics(_m62)[1]
_r = _rows62[0]
check(6.2, n == 1
      and abs(float(_r["peak_after_pct"]) - 2.5) < 0.06
      and _r["beat_funnel"] == "true"
      and _r["peak_at"].startswith("2026-09-30"),
      "horizon CLOSED: peak = max window close vs the crossed price "
      "+2.5% (the pre-fire 1.0 and the post-horizon 3.0 never inflate "
      "it), beat_funnel true (2.5 >= the 1.8 funnel floor), peak_at "
      "carries the peak candle's instant")
# 6.3 the miss: peak below the funnel floor
_C6b = [_candle(FIRED_AT + timedelta(minutes=5 * i),
                1.10 if i == 12 else 1.09)       # the whole leg: +0.917%
        for i in range(96)]
_m63 = _csv_with(
    "QNT/USD,stale-move,0.9,2026-09-30 20:05:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
pa.backfill_metrics(spec2, _m63, LATER, _feed6({"QNT/USD": _C6b}))
_r3 = pa.read_metrics(_m63)[1][0]
check(6.3, _r3["beat_funnel"] == "false"
      and abs(float(_r3["peak_after_pct"]) - 0.917) < 0.01,
      "a leg that never reaches the funnel's entry floor records "
      "beat_funnel false — the honest miss is the measurement")
# 6.4 the failed feed NEVER invents a peak
_m64 = _csv_with(
    "XMR/USD,tide-out,1.15,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(spec2, _m64, LATER, _feed6({}))
_r4 = pa.read_metrics(_m64)[1][0]
check(6.4, n == 0 and _r4["peak_after_pct"] == ""
      and _r4["beat_funnel"] == "",
      "a failed feed leaves the row EMPTY for the next cycle — a stale "
      "candle is a lie with decimals, the same honesty as the trip")
# 6.5 a legacy csv without the hindsight columns degrades to nothing
_m65 = TMP / "m6_legacy.csv"
_m65.write_text("pair,gate,threshold,crossed_at,peak_after_pct,"
                "ask_tob_usd,spread_bps\n"
                "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,,1.081,9.3\n",
                encoding="utf-8")
n = pa.backfill_metrics(spec2, _m65, LATER, _feed6({"FIL/USD": _C6}))
check(6.5, n == 0
      and "fired_at" not in pa.read_metrics(_m65)[0],
      "a legacy csv (no fired_at column) is skipped fail-soft — the "
      "backfill never rewrites a schema it cannot fill honestly")
# 6.6 the integration: run() backfills a closed row through the REAL
# cycle path (stubbed feed) and the watch loop still stands
_m66 = TMP / "m6_run.csv"
_st6 = TMP / "st6.json"
_st6.write_text(json.dumps(
    {"fired": {"FIL/USD|tide-out": {"fired_at": FIRED_AT.isoformat()}},
     "pair_cooldown": {}}), encoding="utf-8")
_m66.write_text(_HDR + "\n"
                "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
                f"{FIRED_AT.isoformat()},,,,,\n", encoding="utf-8")
_spec6 = TMP / "spec6.yaml"
_spec6.write_text(
    "enabled: true\nhindsight_backfill: true\nhorizon_hours: 7\n"
    "funnel_entry_floor_pct: 1.8\nmax_parallel: 6\n"
    "per_pair_cooldown_min: 240\nfraction_of_medfill: 0.65\n"
    "min_threshold_pct: 0.55\nconfirm_candles: 1\nbook_required: true\n"
    "book_max_spread_bps: 30\npriority: high\ntag: recovery\n"
    "title: '{pair} crossed'\nbody: 'b'\n"
    "metrics_csv: 'pair,gate,threshold,crossed_at,crossed_price,"
    "fired_at,peak_after_pct,peak_at,beat_funnel,ask_tob_usd,"
    "spread_bps'\n"
    "topic_file: push_topic.txt\n", encoding="utf-8")
pa.run(spec_path=_spec6, ledger_path=TMP / "led6.jsonl",
       medfills={"tide-out": 1.77}, state_path=_st6, metrics_path=_m66,
       once=True, now=LATER, _no_lock=True,
       _trip=lambda pair, thr, cc: (False, {"reason": "armed"}),
       _fetch_candles=_feed6({"FIL/USD": _C6}))
_r6 = pa.read_metrics(_m66)[1][0]
check(6.6, abs(float(_r6["peak_after_pct"]) - 2.5) < 0.06
      and _r6["beat_funnel"] == "true",
      "run() backfills the horizon-closed row through the REAL cycle "
      "(the tripwire itself ran and stayed armed on the stubbed trip — "
      "backfill and watch share one tick)")
# 6.7 the off switch: hindsight_backfill absent -> run() never touches
# the csv (the backfill is opt-in by spec)
_m67 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,"
    f"{FIRED_AT.isoformat()},,,,,\n")
_spec67 = TMP / "spec67.yaml"
_spec67.write_text(
    "enabled: true\nhorizon_hours: 7\nmax_parallel: 6\n"
    "per_pair_cooldown_min: 240\nfraction_of_medfill: 0.65\n"
    "min_threshold_pct: 0.55\nconfirm_candles: 1\nbook_required: true\n"
    "book_max_spread_bps: 30\npriority: high\ntag: recovery\n"
    "title: '{pair} crossed'\nbody: 'b'\n"
    "metrics_csv: 'pair,gate,threshold,crossed_at,crossed_price,"
    "fired_at,peak_after_pct,peak_at,beat_funnel,ask_tob_usd,"
    "spread_bps'\n"
    "topic_file: push_topic.txt\n", encoding="utf-8")
pa.run(spec_path=_spec67, ledger_path=TMP / "led6.jsonl",
       medfills={"tide-out": 1.77}, state_path=TMP / "st7.json",
       metrics_path=_m67, once=True, now=LATER, _no_lock=True,
       _trip=lambda pair, thr, cc: (False, {"reason": "armed"}),
       _fetch_candles=_feed6({"FIL/USD": _C6}))
check(6.7, pa.read_metrics(_m67)[1][0]["peak_after_pct"] == "",
      "the off switch: no hindsight_backfill in the spec -> the csv is "
      "never rewritten (opt-in by spec, the yaml is the dial)")
# 6.8 THE FULL-HORIZON PULL (the Copilot HIGH, Oct 1): 30 candles is
# 2.5h of a 7h window — a peak in hour 6.5 must still be measured, and
# the base's own sub-base closes must never masquerade as it.
_C68 = [_candle(FIRED_AT + timedelta(minutes=5 * i),
                1.15 if i == 78 else 1.0)     # the only peak: hour 6.5
        for i in range(90)]
_seen_limits = []


def _feed68(pairs):
    def f(pair, tf, limit, since=None):
        _seen_limits.append(limit)
        got = list(pairs.get(pair) or [])
        return got[:limit] if limit else got   # respects the pull size
    return f


_m68 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(spec2, _m68, LATER, _feed68({"FIL/USD": _C68}))
_r8 = pa.read_metrics(_m68)[1][0]
check(6.8, n == 1
      and abs(float(_r8["peak_after_pct"]) - 5.505) < 0.01
      and _r8["beat_funnel"] == "true",
      "a peak in hour 6.5 of the 7h window IS measured (the pull must "
      "cover the WHOLE horizon — 30 candles = 2.5h and the late peak "
      "was silently missed; the first 2.5h's 1.0 closes never "
      "masquerade as the peak)")
check(6.9, _seen_limits and max(_seen_limits) >= HORIZON_H * 12,
      "the pull's candle limit covers the horizon (7h x 12 five-minute "
      "candles = 84, plus margin) — pinned on the captured request, "
      "not the happy feed")
# 6.10 THE CAP-HONESTY (the Copilot LOW, Oct 1): a corrupted spec whose
# horizon needs more candles than the pull's 1000 ceiling CANNOT be
# measured completely — a partial-window peak saved as complete is
# fiction, so the row stays empty (the same honesty as the failed feed).
_spec_big = dict(spec2)
_spec_big["horizon_hours"] = 200
_m610 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(_spec_big, _m610, MUCH_LATER,
                        _feed6({"FIL/USD": _C6}))
_r10 = pa.read_metrics(_m610)[1][0]
check(6.11, n == 0 and _r10["peak_after_pct"] == ""
      and _r10["beat_funnel"] == "",
      "a horizon the pull cannot fully cover (200h >> the 1000-candle "
      "ceiling) is NEVER measured partially — the row stays empty and "
      "the broken spec shows as an unfilled column, not as a fake peak")
# 6.12 THE COVERAGE IS IN-WINDOW (the Copilot medium, Oct 1): a thin or
# odd-spaced feed can return enough TOTAL candles to pass a total-count
# guard while carrying fewer than the horizon's worth INSIDE the window
# — the guard must count candles in [fired_at, horizon end), not the
# returned total. A 6-minute-spaced feed: 96 candles total, only 70
# inside the 7h window, a tempting 1.15 peak in there.
_C612 = [_candle(FIRED_AT + timedelta(minutes=6 * i),
                 1.15 if i == 30 else 1.0)
         for i in range(96)]
_m612 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(spec2, _m612, LATER, _feed6({"FIL/USD": _C612}))
_r12 = pa.read_metrics(_m612)[1][0]
check(6.12, n == 0 and _r12["peak_after_pct"] == "",
      "70 in-window candles < the 84 the horizon needs — the total "
      "count (96) must not masquerade as coverage; the partial-window "
      "peak stays unfilled")
# 6.13 THE REQUIRED COUNT CEILS (the Copilot low, Oct 1): int() floors —
# a 7.99h horizon needs ceil(95.88) = 96 in-window candles, but a floor
# asked only 95 and let a 7.92h partial window pass as complete. A
# 5-minute-spaced feed with exactly 95 in-window candles (a 1.15 peak
# among them) must stay UNFILLED under a 7.99h horizon.
_spec_799 = dict(spec2)
_spec_799["horizon_hours"] = 7.99
_C613 = [_candle(FIRED_AT + timedelta(minutes=5 * i),
                 1.15 if i == 80 else 1.0)
         for i in range(95)]                  # 95 in-window, no more
_m613 = _csv_with(
    "FIL/USD,tide-out,1.239,2026-09-30 20:00:00,1.09,"
    f"{FIRED_AT.isoformat()},,,,,\n")
n = pa.backfill_metrics(_spec_799, _m613, LATER, _feed6({"FIL/USD": _C613}))
_r13 = pa.read_metrics(_m613)[1][0]
check(6.13, n == 0 and _r13["peak_after_pct"] == ""
      and _r13["beat_funnel"] == "",
      "95 in-window candles < ceil(7.99h x 12) = 96 — the floored "
      "requirement let a 7.92h window pass as complete; the required "
      "count CEILS")

print()
fails = [r for r in results if not r[1]]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
