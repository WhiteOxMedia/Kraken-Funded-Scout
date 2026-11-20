"""test_popupconfirm.py — THE POPUP CONFIRMATION GATE (user rule, Nov 20).

"The contest is a reflection of the scout and the popups... all the scout
needs to do is fire the popups when the breakout actually happens (instead
of when it wins the pop-up floor). Begin tracking when it wins the pop-up
floor but don't count it until it actually fires the pop-up. Fire the
pop-up when its profit is greater than or equal to $1 US or when the
breakout happens."

Pinned here:
  1. the $1 bar's arithmetic (profit on the recommended spend);
  2. the gate itself — $1 profit OR breakout, nothing else;
  3. arming TRACKS the floor-winner without counting it (a pending row,
     a "pending" snapshot, no fired mark/track), and re-arming keeps the
     first arm's price;
  4. a row that cannot be priced never becomes a gate (the popup fires);
  5. the wiring: the champion path arms BEFORE notify_big, and the
     deferred confirmation runs the fire's books (mark, auto-track,
     fired snapshot) only then.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs

# ── 1. the $1 bar: ONE price threshold (the gate and the console) ──────
check("the bar is $1 US", bs.POPUP_CONFIRM_PROFIT_USD == 1.0)
check("the $1 target price: entry $10.00 on a $1,000 spend -> $10.0100",
      abs(bs.popup_pending_target_price(10.0, 1000.0) - 10.01) < 1e-9,
      bs.popup_pending_target_price(10.0, 1000.0))
check("the $1 target scales with the spend ($5,100 -> $10.00196)",
      abs(bs.popup_pending_target_price(10.0, 5100.0)
          - 10.0 * (1.0 + 1.0 / 5100.0)) < 1e-12)
check("+1% on a $100 spend sits exactly at the target",
      abs(bs.popup_pending_target_price(100.0, 100.0) - 101.0) < 1e-9)
check("the target is 0 when either side is unreadable",
      bs.popup_pending_target_price(0.0, 1000.0) == 0.0
      and bs.popup_pending_target_price(10.0, 0.0) == 0.0)

# ── 3. arming TRACKS but never counts ──────────────────────────────────
_tmp = tempfile.mkdtemp()
_real_pending = bs.POPUP_PENDING_JSON
_real_snap = bs._log_signal_snapshot
_snaps = []
bs.POPUP_PENDING_JSON = os.path.join(_tmp, "popup_pending.json")
bs._log_signal_snapshot = lambda rec: _snaps.append(rec)


class _Ex:
    """A 2h ceiling at $10.50 (the highest COMPLETED 5m high)."""

    def fetch_ohlcv(self, pair, timeframe="5m", limit=30):
        return [[i * 300000, 10.0, 10.5, 9.9, 10.0, 5.0] for i in range(limit)]


try:
    armed = bs.popup_pending_arm(
        "XMR/USD", entry=10.0, spend=1000.0,
        goal_pct=1.5, win_min=60.0, title="🟢 BUY XMR",
        message="BUY XMR\nWHY: engine\nWHEN: right now — this is fresh",
        spoken="Buy XMR!", brain=91.0, quick=91.0, floor=84.0,
        rot="12-16", snapshot={"features": {"momentum_1h": 0.2}},
        exchange=_Ex())
    check("a floor-winner ARMS (the caller must not pop)", armed is True)
    doc = json.loads(Path(bs.POPUP_PENDING_JSON).read_text(encoding="utf-8"))
    row = doc.get("XMR/USD") or {}
    check("the pending row is persisted (tracked from the floor win)",
          bool(row), list(doc))
    check("the row carries the arm-time entry and the sized spend",
          row.get("entry") == 10.0 and row.get("spend") == 1000.0)
    check("the row carries the $1 price bar's denominator (spend) and the "
          "2h ceiling it must crack",
          row.get("breakout_level") == 10.5, row.get("breakout_level"))
    check("the row carries the popup it would have sent (fired later, verbatim)",
          row.get("title") == "🟢 BUY XMR" and "WHEN: right now" in row.get("message", ""))
    check("the row carries only what the fire reads (no dead fields)",
          "stop" not in row and "take_profit" not in row and "why" not in row
          and "route" not in row and "armed_at" not in row, sorted(row))
    check("the row has a clock (expiry at the window/rotation boundary)",
          float(row.get("expires_ts") or 0) > 0)
    check("the arm is journaled as PENDING — never as fired",
          bool(_snaps) and _snaps[-1].get("kind") == "pending"
          and _snaps[-1].get("pair") == "XMR/USD", _snaps[-1] if _snaps else None)
    check("the arm snapshot keeps the fire-time features (the scorer's row)",
          (_snaps[-1].get("features") or {}).get("momentum_1h") == 0.2)

    # re-arm: already tracked, the first arm's price stands
    again = bs.popup_pending_arm(
        "XMR/USD", entry=12.0, spend=1000.0,
        goal_pct=1.5, win_min=60.0, title="T2", message="M2", spoken="",
        brain=90.0, quick=90.0, floor=84.0, rot="12-16",
        snapshot={}, exchange=_Ex())
    doc = json.loads(Path(bs.POPUP_PENDING_JSON).read_text(encoding="utf-8"))
    check("re-arming an already-pending pair is a no-op (first price stands)",
          again is True and doc["XMR/USD"]["entry"] == 10.0,
          doc["XMR/USD"]["entry"])

    # unmeasurable: no size -> fires at once, nothing is tracked
    no_size = bs.popup_pending_arm(
        "SOL/USD", entry=10.0, spend=0.0,
        goal_pct=1.5, win_min=60.0, title="T", message="M", spoken="",
        brain=90.0, quick=90.0, floor=84.0, rot="12-16",
        snapshot={}, exchange=_Ex())
    doc = json.loads(Path(bs.POPUP_PENDING_JSON).read_text(encoding="utf-8"))
    check("a row with no size is NO gate (the popup fires at once)",
          no_size is False and "SOL/USD" not in doc)
finally:
    bs.POPUP_PENDING_JSON = _real_pending
    bs._log_signal_snapshot = _real_snap
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)

# ── 4. the wiring ──────────────────────────────────────────────────────
src = Path("background_scanner.py").read_text(encoding="utf-8")
check("the confirmation gate is two pieces (one target formula, one arm)",
      "def popup_pending_arm(" in src
      and "def popup_pending_target_price(" in src
      and "popup_pending_profit_usd" not in src
      and "popup_pending_confirmed" not in src)
champ = src[src.find("def send_alert"):src.find("def log_winner")]
check("the champion path arms BEFORE it pops",
      champ.find("popup_pending_arm(") > 0
      and champ.find("popup_pending_arm(") < champ.find("notify_big(title, message"))
check("an armed row returns before notify (not counted at the floor win)",
      "if _pending_armed:\n                return" in champ)
fire = src[src.find("def _fire_pending_popup"):src.find("def _check_pending_popups")]
check("the deferred fire runs the popup's books — notify, mark, track, fired row",
      "notify_big(_p_title" in fire
      and "rotation_mark_fired(asset)" in fire
      and "auto_track_popup(pair" in fire
      and '"kind": "fired"' in fire)
check("the deferred fire says WHEN it was armed and why it fired now",
      "when it won the" in fire and "CONFIRMATION:" in fire)
check("the stale WHEN line is dropped by prefix, not by exact wording",
      'if not l.startswith("WHEN: ")' in fire)
check("the pending clock fires on the target or the ceiling (not time)",
      "hit_target = target > 0 and px >= target" in src
      and "cracked = ceiling > 0 and px >= ceiling" in src)
check("an unconfirmed row expires as tracked-but-never-counted",
      '"pending-expired"' in src and "counted as a popup" in src)
check("the pending clock rides every scanner pass (no-buy cooldown excluded)",
      "_check_pending_popups(exchange)" in src
      and "if not no_buy:\n            try:\n                _check_pending_popups(exchange)" in src)
check("the Sure Thing breakout path is untouched (it already fires at the crack)",
      'auto_track_popup(pair, float(r.get("trigger_px")' in src)

# ── 4b. the pending strip in the console's POPUP HISTORY (Nov 20) ───────
ap = Path("app.py").read_text(encoding="utf-8")
_ph = ap[ap.index("def _render_popup_history"):ap.index("def _render_bots_book")]
check("the popup history reads the pending book (same place the user looks)",
      "_load_popup_pending" in _ph)
check("pending rows survive an empty fired ledger (no early return first)",
      0 < _ph.find("_load_popup_pending") < _ph.find('f = base / "popups.jsonl"')
      and _ph.find("if _pend_html:") < _ph.find("if not rows:"))
check("the strip names itself tracked-not-counted",
      "PENDING — TRACKED, NOT COUNTED" in _ph)
check("each row shows entry, spend, the $1 target, the ceiling, time left",
      all(s in _ph for s in ('f"entry ${_entry:,.4f} · spend ${_spend:,.0f}{_px_txt} · "',
                             'f"+${_bar:.2f} @ ${_target:,.4f}{_d_txt_t} · ceiling "',
                             "min left")))
check("the row is priced by the console's own ticker helper (live now + distances)",
      "_ticker_px(_pair)" in _ph
      and "_d_t = (_target / _live_px - 1.0) * 100.0" in _ph
      and "_d_c = (_ceil / _live_px - 1.0) * 100.0" in _ph)
check("a dead tick degrades to 'no tick' (never drops the row)",
      "now — (no tick)" in _ph)
check("the $1 target comes from the scanner's one helper (no second formula)",
      "popup_pending_target_price" in _ph)
check("the expander title counts LIVE rows only",
      '· ⏳ {_live_n} pending' in _ph
      and 'pending — tracked, not counted"' in _ph)
check("expired rows are split out of the live strip (never counted)",
      '_PEND_EXPIRED_GRACE_S' in _ph
      and '_live_rows, _exp_rows = [], []' in _ph
      and 'expired — not counted' in _ph)
check("the expired heading says nothing fired",
      "⌛ EXPIRED — NEVER COUNTED" in _ph
      and "nothing fired" in _ph)
check("the strip stays capped with an honest '+N more' (nothing hidden silently)",
      '+{_more_pend} more' in _ph)

# ── 4c. the strip RENDERS (exec'd against a fake st, real book) ─────────
_tmp3 = tempfile.mkdtemp()
_real_pending3 = bs.POPUP_PENDING_JSON
bs.POPUP_PENDING_JSON = os.path.join(_tmp3, "popup_pending.json")
try:
    import time as _time3
    from contextlib import contextmanager as _cm3
    bs._save_popup_pending({"XMR/USD": {
        "pair": "XMR/USD", "entry": 10.0, "spend": 1000.0,
        "breakout_level": 10.5, "floor": 84.0, "brain": 91.0,
        "quick": 91.0, "rot": "12-16", "title": "🟢 BUY XMR",
        "armed_ts": _time3.time(), "expires_ts": _time3.time() + 1500}})
    _cap3 = {"md": [], "title": []}

    class _St3:
        @staticmethod
        def markdown(h, unsafe_allow_html=False):
            _cap3["md"].append(h)

    @_cm3
    def _exp3(key, title):
        _cap3["title"].append(title)
        yield

    # the console's ticker helper, faked per pair: XMR trades under its
    # $1 target, LIVE is close to its target, JUSTEXP has no tick
    _px3 = {"XMR/USD": 9.95, "LIVE/USD": 1.99}
    _ns3 = {"__file__": str(Path("app.py").resolve()), "st": _St3,
            "stable_expander": _exp3, "logger": bs.logger, "time": _time3,
            "_ticker_px": lambda pair: _px3.get(pair, 0.0)}
    exec(compile(_ph, "app.py", "exec"), _ns3)
    _ns3["_render_popup_history"]()
    _h3 = "".join(_cap3["md"])
    check("runtime: the popup history carries the pending heading",
          "PENDING — TRACKED, NOT COUNTED" in _h3, _h3[:200])
    check("runtime: the row shows entry $10.0000 and spend $1,000",
          "entry $10.0000" in _h3 and "spend $1,000" in _h3)
    check("runtime: the row shows the $1 price target $10.0100",
          "+$1.00 @ $10.0100" in _h3, _h3[:400])
    check("runtime: the row shows the ceiling and the time left",
          "ceiling $10.5000" in _h3 and "min left" in _h3)
    check("runtime: the live price rides the row (now $9.9500)",
          "now $9.9500" in _h3, _h3[:400])
    check("runtime: the distance to the $1 target is shown (+0.60%)",
          "@ $10.0100 (+0.60%)" in _h3, _h3[:400])
    check("runtime: the distance to the ceiling is shown (+5.53%)",
          "ceiling $10.5000 (+5.53%)" in _h3, _h3[:400])
    check("runtime: the expander title announces the pending count",
          bool(_cap3["title"]) and "⏳ 1 pending" in _cap3["title"][0],
          _cap3["title"])

    # the live/expired split: a just-expired row shows marked but is NOT
    # counted; a long-dead row is not shown at all
    _now3 = _time3.time()
    bs._save_popup_pending({
        "LIVE/USD": {"pair": "LIVE/USD", "entry": 2.0, "spend": 500.0,
                     "breakout_level": 2.1, "armed_ts": _now3,
                     "expires_ts": _now3 + 1200},
        "JUSTEXP/USD": {"pair": "JUSTEXP/USD", "entry": 3.0, "spend": 300.0,
                        "breakout_level": 0.0, "armed_ts": _now3 - 600,
                        "expires_ts": _now3 - 60},
        "DEAD/USD": {"pair": "DEAD/USD", "entry": 4.0, "spend": 400.0,
                     "breakout_level": 0.0, "armed_ts": _now3 - 9000,
                     "expires_ts": _now3 - 7200}})
    _cap3["md"].clear()
    _cap3["title"].clear()
    _ns3["_render_popup_history"]()
    _h4 = "".join(_cap3["md"])
    check("runtime: the title counts ONLY the live row (1, not 3)",
          bool(_cap3["title"]) and "⏳ 1 pending" in _cap3["title"][0]
          and "3 pending" not in _cap3["title"][0], _cap3["title"])
    check("runtime: the just-expired row still shows, marked expired/not counted",
          "JUSTEXP" in _h4 and "EXPIRED" in _h4
          and "expired — not counted" in _h4, _h4[:400])
    check("runtime: the long-dead row is not shown at all",
          "DEAD" not in _h4, _h4[:400])
    check("runtime: the live row still rides as PENDING",
          "LIVE" in _h4 and "PENDING" in _h4)
    check("runtime: each row carries its own live price and distances",
          "now $1.9900" in _h4 and "@ $2.0040 (+0.70%)" in _h4
          and "ceiling $2.1000 (+5.53%)" in _h4, _h4[:600])
    check("runtime: a pair with no tick still renders, marked no tick",
          "JUSTEXP" in _h4 and "now — (no tick)" in _h4, _h4[:600])

    # no live rows at all: the title must not claim a pending popup
    bs._save_popup_pending({
        "JUSTEXP/USD": {"pair": "JUSTEXP/USD", "entry": 3.0, "spend": 300.0,
                        "breakout_level": 0.0, "armed_ts": _now3 - 600,
                        "expires_ts": _now3 - 30}})
    _cap3["md"].clear()
    _cap3["title"].clear()
    _ns3["_render_popup_history"]()
    _h5 = "".join(_cap3["md"])
    check("runtime: no live rows -> the title says no live pending, 1 expired",
          bool(_cap3["title"])
          and "no live pending" in _cap3["title"][0]
          and "1 expired (not counted)" in _cap3["title"][0], _cap3["title"])
    check("runtime: the expired-only strip never wears the PENDING heading",
          "EXPIRED — NEVER COUNTED" in _h5
          and "PENDING — TRACKED, NOT COUNTED" not in _h5, _h5[:300])
finally:
    bs.POPUP_PENDING_JSON = _real_pending3
    import shutil as _sh3
    _sh3.rmtree(_tmp3, ignore_errors=True)

# ── 5. the clock, run for real (exec'd, like the Sure Thing drive) ──────
#    The nested fire/check pair is lifted out of run_scanner and run
#    against fakes: a pending row must NOT fire under $1, MUST fire on
#    $1, MUST fire on the ceiling crack, and MUST expire not-counted.
_start = src.index("    # ── THE CONFIRMATION GATE'S FIRE (user rule, Nov 20)")
_end = src.index("    # ── Evaluate a single asset and check checklist")
_body = src[_start:_end]
# the body is already indented one level (inside run_scanner), so the
# wrapper re-hosts it verbatim at its own body level
_ns_src = ("def _scanner_ns():\n"
           + "\n".join(_body.splitlines())
           + "\n    return _check_pending_popups\n")

_tmp2 = tempfile.mkdtemp()
_real_pending2 = bs.POPUP_PENDING_JSON
bs.POPUP_PENDING_JSON = os.path.join(_tmp2, "popup_pending.json")

try:
    def _run_clock(row, tick):
        cap = {"notify": [], "track": [], "mark": [], "snaps": [], "sups": []}
        ns = {
            "notify_big": lambda t, m, spoken=None: cap["notify"].append((t, m, spoken)),
            "rotation_mark_fired": lambda asset: cap["mark"].append(asset),
            "auto_track_popup": lambda pair, px, spend, goal, window, why, source, hm_mode=None:
                cap["track"].append((pair, px, source)),
            "_popup_already_watched": lambda pair: False,
            "_log_signal_snapshot": lambda rec: cap["snaps"].append(rec),
            "_log_suppression": lambda pair, gate, detail, **kw: cap["sups"].append((pair, gate)),
            "popup_pending_target_price": bs.popup_pending_target_price,
            "_load_popup_pending": bs._load_popup_pending,
            "_save_popup_pending": bs._save_popup_pending,
            "POPUP_CONFIRM_PROFIT_USD": bs.POPUP_CONFIRM_PROFIT_USD,
            "datetime": __import__("datetime").datetime,
            "time": __import__("time"),
            "logger": bs.logger,
        }
        exec(compile(_ns_src, "background_scanner.py", "exec"), ns)
        checker = ns["_scanner_ns"]()
        bs._save_popup_pending({"XMR/USD": dict(row)})
        ex = type("E", (), {"fetch_ticker": lambda self, pair: {"last": tick}})()
        checker(ex)
        left = bs._load_popup_pending()
        return cap, left

    def _row(**kw):
        base = {"pair": "XMR/USD", "entry": 10.0, "spend": 1000.0,
                "breakout_level": 10.5, "floor": 84.0, "brain": 91.0,
                "quick": 91.0, "rot": "12-16", "title": "🟢 BUY XMR",
                "message": "BUY XMR\nWHY: engine\nWHEN: right now — this is fresh",
                "spoken": "Buy XMR!",
                "goal_pct": 1.5, "win_min": 60.0, "armed_at": "2026-11-20T10:00:00-07:00",
                "armed_ts": 1.0, "expires_ts": 9e12, "snapshot": {}}
        base.update(kw)
        return base

    cap, left = _run_clock(_row(), 10.05)       # +$5.00 on the $1,000 spend
    check("runtime: +$5.00 fires (the $1 bar clears)",
          len(cap["notify"]) == 1 and "CONFIRMED" in cap["notify"][0][0], cap["notify"])
    check("runtime: the fire marks the rotation (counted only now)",
          cap["mark"] == ["XMR"], cap["mark"])
    check("runtime: the fire auto-tracks at the CONFIRMED price",
          bool(cap["track"]) and cap["track"][0][1] == 10.05, cap["track"])
    check("runtime: the fire writes a FIRED snapshot (never a pending one)",
          bool(cap["snaps"]) and cap["snaps"][0].get("kind") == "fired"
          and cap["snaps"][0].get("route") == "champion-confirmed", cap["snaps"])
    check("runtime: the fired row is consumed (one popup, one count)",
          left == {}, left)
    _mf = cap["notify"][0][1]
    check("runtime: the confirmed message replaces the stale WHEN line",
          "armed at" in _mf and "this is fresh" not in _mf
          and "WHY: engine" in _mf and "BUY XMR" in _mf, _mf[:220])

    cap, left = _run_clock(_row(), 10.005)      # +$0.50 — under the bar, no breakout
    check("runtime: +$0.50 and no breakout -> NO popup, still pending",
          cap["notify"] == [] and "XMR/USD" in left, (cap["notify"], list(left)))

    cap, left = _run_clock(_row(), 10.5)        # the ceiling cracks, profit $50
    check("runtime: the ceiling crack fires with the breakout reason",
          len(cap["notify"]) == 1 and "breakout" in cap["notify"][0][1].lower(),
          cap["notify"])

    cap, left = _run_clock(_row(spend=100.0, breakout_level=0.0), 10.005)  # +$0.05
    check("runtime: a small spend under the bar does not fire",
          cap["notify"] == [] and "XMR/USD" in left)

    cap, left = _run_clock(_row(expires_ts=1.0), 10.05)
    check("runtime: an expired row never fires (tracked, not counted)",
          cap["notify"] == [] and left == {}
          and any(g == "pending-expired" for _, g in cap["sups"]),
          (cap["notify"], cap["sups"], left))
finally:
    bs.POPUP_PENDING_JSON = _real_pending2
    import shutil as _sh2
    _sh2.rmtree(_tmp2, ignore_errors=True)

import scratch_space
print(f"RESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_popupconfirm_check.txt'), "w",
     encoding="utf-8").write("\n".join(lines) + "\n")


def main():
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
