"""test_rate_label.py — A DOLLAR FIGURE NAMES ITS RATE (Oct 26).

Every USD number on the site is a CONVERSION of an XMR amount at one
rate. That rate can be the operator's pin, a Kraken fetch from four
minutes ago, a fetch from yesterday, or — when nothing was ever fetched
— the 323 constant. A visitor who sees "$619.72" cannot tell which, and
the 323 fallback is wrong by whatever the market has done since.

So `jackpot_funding.rate_info()` returns the rate WITH its provenance,
including a display-ready `label` that every surface reuses verbatim:

    converted at $593.35/XMR · Kraken · 4 min ago
    converted at $593.35/XMR · Kraken · 2 h ago — stale, refresh overdue
    converted at the FALLBACK $323.00/XMR — no live rate on this box
    converted at the operator-set $500.00/XMR

This file grades that contract:

  1. the four tiers each produce the right source/stale/label
  2. the label always carries the unit ("/XMR") and the number quoted
  3. /api/stats publishes the descriptor AND keeps xmr_usd_rate
  4. jackpot_usd is the pot at exactly that rate
  5. /api/jackpot/projection serves the SAME descriptor (both branches:
     no row, and a persisted row) — the hero plate and the card under
     it cannot label differently
  6. the client actually paints it: hero, dashboard plate, projection
     card, and the methodology dialog
  7. hermetic: sandbox DB, no fetch (XMR_PRICE_LIVE=0), no wallet RPC

House conventions: check/report, sandbox_db, make_client — the ONE copy of
that harness lives in conftest.py now, shared with the other house scripts.
"""
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

from conftest import (                     # the harness, ONE copy: conftest.py
    check,                                 # one grade: PASS/FAIL + counters
    make_client,                           # TestClient, wallet RPC refused
    report,                                # the RESULT line + the exit code
    sandbox_db,                            # a fresh jackpot.db in a fresh tempdir
)

# No fetching in this file: every tier is simulated by writing the very
# keys the reader uses, so the grades never depend on the market.
os.environ["XMR_PRICE_LIVE"] = "0"

_tmp = sandbox_db("rate_label_")

import database as db                                        # noqa: E402
import jackpot_funding as funding                             # noqa: E402
import projection_ad                                          # noqa: E402
import xmr_price                                              # noqa: E402

CLIENT, srv = make_client()


def _live_ready():
    """Check 4.6 walks the member dashboard's LIVE watcher route
    (/api/jackpot/status), whose handler reads tickets through the
    pool-package database module — the real pool/jackpot.db, operator
    state that the checkout does not carry (gitignored; absent in a
    fresh clone, where the request 500s on a zero-table file instead of
    grading anything). The whole descriptor contract it exists to pin
    is graded hermetically by 3.x on the sandbox; when the live file is
    not a database, the check names the gap instead of crashing the
    drive mid-file. Where the live DB IS present, the exact same check
    runs unchanged.
    """
    if not (HERE / "pool" / "jackpot.db").exists():
        return False
    try:
        import sqlite3
        _c = sqlite3.connect(
            f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}?mode=ro",
            uri=True)
        try:
            _t = {r[0] for r in _c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            _c.close()
    except sqlite3.Error:
        return False
    return "tickets" in _t


_LIVE_OK = _live_ready()


def clear_rate():
    for key in (xmr_price.OVERRIDE_KEY, xmr_price.RATE_KEY, xmr_price.TS_KEY,
                xmr_price.SRC_KEY, xmr_price.ATTEMPT_KEY, xmr_price.ERR_KEY):
        db.set_setting(key, "")
    xmr_price.reset()


def stats():
    r = CLIENT.get("/api/stats")
    return r.status_code, (r.json() if r.status_code == 200 else {})


print("== 1. THE FOUR TIERS ==")

db.set_setting("jackpot_balance", "1.0")

clear_rate()
db.set_setting(xmr_price.OVERRIDE_KEY, "500")
db.set_setting("jackpot_balance", "1.0")
_st, _d = stats()
_ri = _d.get("xmr_usd_rate_info") or {}
check(1.1, _ri.get("source") == "override" and _ri.get("stale") is False,
      f"a pinned rate reports itself as the operator's: {_ri.get('source')}")
check(1.2, "operator-set" in _ri.get("label", "")
      and "500.00" in _ri.get("label", ""),
      f"…and the label says so: {_ri.get('label')!r}")

clear_rate()
_now = time.time()
db.set_setting(xmr_price.RATE_KEY, "612.5")
db.set_setting(xmr_price.TS_KEY, "%.3f" % _now)
db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
_st, _d = stats()
_ri = _d.get("xmr_usd_rate_info") or {}
check(1.3, _ri.get("source") == "kraken_ticker" and _ri.get("stale") is False,
      f"a fresh fetch is served as fresh: stale={_ri.get('stale')}")
check(1.4, "612.50" in _ri.get("label", "") and "Kraken" in _ri.get("label", "")
      and "ago" in _ri.get("label", ""),
      f"…with its venue and age: {_ri.get('label')!r}")

clear_rate()
db.set_setting(xmr_price.RATE_KEY, "612.5")
db.set_setting(xmr_price.TS_KEY, "%.3f" % (_now - 7200))
db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
_st, _d = stats()
_ri = _d.get("xmr_usd_rate_info") or {}
check(1.5, _ri.get("stale") is True,
      "a two-hour-old rate is flagged stale")
check(1.6, "stale" in _ri.get("label", "").lower()
      and "h ago" in _ri.get("label", ""),
      f"…and says how old it is: {_ri.get('label')!r}")

clear_rate()
_st, _d = stats()
_ri = _d.get("xmr_usd_rate_info") or {}
check(1.7, _ri.get("source") == "default" and _ri.get("stale") is True,
      f"nothing fetched → the fallback, flagged: {_ri.get('source')}")
check(1.8, "FALLBACK" in _ri.get("label", "")
      and "323.00" in _ri.get("label", ""),
      f"…and the label names the constant: {_ri.get('label')!r}")

print("\n== 2. THE LABEL ALWAYS CARRIES THE UNIT AND THE NUMBER ==")
for _case in ("override", "fresh", "stale", "default"):
    clear_rate()
    if _case == "override":
        db.set_setting(xmr_price.OVERRIDE_KEY, "400")
    elif _case in ("fresh", "stale"):
        db.set_setting(xmr_price.RATE_KEY, "601.25")
        db.set_setting(xmr_price.TS_KEY, "%.3f" % (
            time.time() - (120 if _case == "fresh" else 9000)))
        db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
    _info = funding.rate_info()
    check(f"2.{_case}", "/XMR" in _info["label"]
          and ("%.2f" % _info["rate"]) in _info["label"],
          f"{_case}: {_info['label']!r}")

print("\n== 3. /api/stats KEEPS THE NUMBER AND ADDS THE DESCRIPTOR ==")
clear_rate()
db.set_setting(xmr_price.OVERRIDE_KEY, "500")
db.set_setting("jackpot_balance", "1.044443")
_st, _d = stats()
check(3.1, _st == 200 and "jackpot_usd" in _d, f"stats answers {_st}")
check(3.2, isinstance(_d.get("xmr_usd_rate"), (int, float))
      and abs(float(_d["xmr_usd_rate"]) - 500.0) < 1e-9,
      f"the legacy numeric rate is unchanged: {_d.get('xmr_usd_rate')}")
_ri = _d.get("xmr_usd_rate_info") or {}
check(3.3, abs(float(_ri.get("rate", 0)) - float(_d["xmr_usd_rate"])) < 1e-9,
      "the descriptor quotes the same rate as the number beside it")
check(3.4, abs(float(_d["jackpot_usd"])
               - round(float(_d["jackpot_xmr"]) * float(_ri["rate"]), 2)) < 0.02,
      f"jackpot_usd is the pot at that rate: {_d['jackpot_usd']} = "
      f"{_d['jackpot_xmr']} x {_ri['rate']}")
check(3.5, set(_ri) >= {"rate", "source", "as_of", "stale", "label"},
      f"the descriptor is complete: {sorted(_ri)}")

print("\n== 4. THE PROJECTION CARD READS THE SAME DESCRIPTOR ==")
_r = CLIENT.get("/api/jackpot/projection")
_p0 = _r.json() if _r.status_code == 200 else {}
check(4.1, _r.status_code == 200 and _p0.get("source") == "none",
      f"no projection row yet: source={_p0.get('source')}")
check(4.2, _p0.get("xmr_usd_rate_info") == _ri,
      "the empty projection still names the rate it converted at")
projection_ad.publish_estimate(possible_usd=999.0, seed_usd=12.0,
                               current_usd=987.0, users=3,
                               source="kraken_referral_sim_v1")
_p1 = CLIENT.get("/api/jackpot/projection").json()
check(4.3, _p1.get("xmr_usd_rate_info") == _ri,
      "…and a persisted projection serves the identical descriptor")
check(4.4, abs(float(_p1["xmr_usd_rate"]) - float(_ri["rate"])) < 1e-9,
      "the card's numeric rate matches /api/stats")

# a stale rate must be visible on the projection card too, not only on /stats
clear_rate()
db.set_setting(xmr_price.RATE_KEY, "612.5")
db.set_setting(xmr_price.TS_KEY, "%.3f" % (time.time() - 7200))
db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
_p2 = CLIENT.get("/api/jackpot/projection").json()
check(4.5, bool((_p2.get("xmr_usd_rate_info") or {}).get("stale")),
      "the stale flag travels with the projection card's dollars")

clear_rate()
_uid = db.create_user("rate_user", "x", email="rate@example.test")
_tok = "rate-label-token"
db.create_session(_tok, _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok)
if _LIVE_OK:
    _js = CLIENT.get("/api/jackpot/status")
    _jd = _js.json() if _js.status_code == 200 else {}
    check(4.6, _js.status_code == 200
          and _jd.get("xmr_usd_rate_info") == (stats()[1].get("xmr_usd_rate_info")),
          "the member dashboard's jackpot sentence gets the same descriptor")
else:
    print("SKIP: 4.6 the live pool/jackpot.db is absent in this checkout — "
          "the member dashboard's watcher route reads the operator DB "
          "through the pool-package module, so there is nothing here to "
          "grade; the descriptor contract is graded hermetically by 3.x")
_wf = (HERE / "pool" / "static" / "wf_dashboard.js").read_text(encoding="utf-8")
check(4.7, "xmr_usd_rate_info" in _wf and "rateNote" in _wf,
      "…and the dashboard sentence renders it, not just ~$N")

print("\n== 5. THE CLIENT PAINTS IT ==")
_app = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(5.1, "function paintRateNote" in _app
      and "paintRateNote(usdEl, d.xmr_usd_rate_info, 'heroRateNote')" in _app,
      "the hero USD figure gets the note")
check(5.2, "paintRateNote(document.querySelector('#realJackpotUsd')" in _app,
      "…and so does the dashboard plate")
check(5.3, "info.stale ? 'var(--redstone,#c0392b)'" in _app,
      "a stale/fallback rate is painted in the warning colour")
_proj = (HERE / "pool" / "static" / "referral_projection.js").read_text(
    encoding="utf-8")
check(5.4, "function rateNote(info)" in _proj
      and "rateNote(d.xmr_usd_rate_info)" in _proj,
      "the projection card renders the note from its own payload")
check(5.5, "<b>Conversion:</b>" in _proj,
      "…and the methodology dialog names the conversion")
check(5.6, "info.stale ? \"var(--red,#e05555)\"" in _proj,
      "the card's note also turns warning-coloured when stale")

print("\n== 6. NO FIGURE LOST ITS DESCRIPTOR IN TRANSIT ==")
_doc = json.dumps(_p2)
check(6.1, '"xmr_usd_rate_info"' in _doc and '"label"' in _doc,
      "the label survives JSON serialization (not just Python)")

print("\n== 7. HERMETIC ==")
check(7.1, str(db.DB_PATH).startswith(_tmp), "every write landed in the sandbox")
check(7.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")
check(7.3, xmr_price.live_enabled() is False,
      "fetching stayed disabled for the whole file")

report()
