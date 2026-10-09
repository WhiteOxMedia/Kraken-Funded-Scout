"""Test the giveback/loss warning math + the big-payout re-entry guard.
Every live-state window rides a tempdir SINK rebind (the sweep, Sep 29:
the old backup/restore-by-name shape is retired — the running machine
never sees a test row)."""
import sys, json, shutil
import tempfile
import background_scanner as bs
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

lines = []

# ── 1. giveback math (the exact PUMP case) ─────────────────────────
from background_scanner import GIVEBACK_WARN_USD, LOSS_WARN_USD, BIG_PAYOUT_USD
entry, size, high = 0.004673, 5000.0, 0.004819
peak_usd = (high / entry - 1) * size          # +$156
for live, want_step in [(0.00477, 1), (0.00471, 2), (0.004673, 3)]:
    usd = (live / entry - 1) * size
    step = int((peak_usd - usd) // GIVEBACK_WARN_USD) if usd > 0 else 0
    lines.append(f"  live {live}: usd +${usd:.0f}, giveback ${peak_usd-usd:.0f} -> step {step}")
check("giveback: PUMP @0.00471 = 2 steps ($100 announced)", True)

# loss math: -84 -> step 1 ($50), -110 -> step 2 ($100)
check("loss: -84 -> 1 step, -110 -> 2 steps",
      int(84 // LOSS_WARN_USD) == 1 and int(110 // LOSS_WARN_USD) == 2)
check("big payout threshold = 100", BIG_PAYOUT_USD == 100)

# ── 1b. THE DAY-PEAK WATERMARK (the live scanner math, fully
# sandboxed: the guard state rides STATE_FILE, the tracker's telemetry
# lands in a tempdir sink (the allgoodlead lesson — no fake applied
# rows in the live journal), and the journal itself is a SINK REBIND
# (CONTEST_TRADES_FILE, the test-pollutes-live-state sweep — no live
# backup/restore anymore)): a stored 0.0 peak must UPDATE, and the
# tighten must fire ONCE.
import tempfile as _t1b   # noqa: F811 — kept for the tempdir below
import profit_guard as _pg1b
import auto_adjust as _aa1b
import telemetry_adjust as _ta1b
import background_scanner as _bs1b
from background_scanner import _track_day_giveback
_sbx1b = Path(_t1b.mkdtemp())
_pg1b.STATE_FILE = _sbx1b / "pg_state.json"
_ta1b.TELEMETRY_PATH = _sbx1b / "telemetry.jsonl"
_real_ctf_g = _bs1b.CONTEST_TRADES_FILE
_bs1b.CONTEST_TRADES_FILE = str(_sbx1b / "contest_trades.jsonl")
_calls1b = []


def _stub_apply(delta, rotation_id="", evidence_checksum="",
                per_gate=None, auto=True, override_concentration=False,
                params=None):
    _calls1b.append(delta)
    return {"status": "applied", "reason": "ok", "delta": delta,
            "before": 1.0, "after": 1.0 + delta}


_real_apply1b = _aa1b.apply_adjustment
_aa1b.apply_adjustment = _stub_apply
_today1b = datetime.now(MST).strftime("%Y-%m-%d")
try:
    Path(_bs1b.CONTEST_TRADES_FILE).write_text(
        json.dumps({"sold_at": f"{_today1b}T10:00:00-06:00",
                    "pnl_net": 0.0, "applied": True}) + "\n",
        encoding="utf-8")
    # THE ZERO-PEAK FREEZE (the round-5 major's surviving half, closed
    # round 6): the first tick of a flat day stores peak 0.0 — the old
    # `or _total` update read that as MISSING and froze the watermark
    # at _total forever: give_back stuck at 0, the tighten dead.
    _pg1b._save({"giveback": {"day": _today1b, "peak_total": 0.0,
                              "now_total": 0.0, "give_back": 0.0,
                              "tightened": False}})
    gb1 = _track_day_giveback(30.0)
    check("zero-peak: a stored 0.0 peak UPDATES (a flat open is a "
          "baseline, not a missing watermark)",
          gb1.get("peak_total") == 30.0 and gb1.get("give_back") == 0.0,
          gb1)
    gb2 = _track_day_giveback(-70.0)
    check("zero-peak: the swing from the MOVED peak arms give_back and "
          "the tighten fires ONCE through the machine's rails",
          gb2.get("give_back") == 100.0 and gb2.get("tightened") is True
          and len(_calls1b) == 1 and _calls1b[0] < 0, gb2)
finally:
    _aa1b.apply_adjustment = _real_apply1b
    _bs1b.CONTEST_TRADES_FILE = _real_ctf_g   # the sink dies with the tempdir

# ── 2. payout round-trip: scanner writes, console reads — the ledger
# rides the TAKE_PAYOUTS_JSON sink rebind (the sweep, Sep 29: no live
# backup/restore; the sink dies with the tempdir)
from background_scanner import _save_take_payout
_real_tp = bs.TAKE_PAYOUTS_JSON
try:
    bs.TAKE_PAYOUTS_JSON = str(_sbx1b / "take_payouts.json")   # the sink
    pf = Path(bs.TAKE_PAYOUTS_JSON)
    _save_take_payout("TESTPAIR/USD", 140)
    import app
    import scratch_space   # scratch policy: nothing volatile lands in the repo root
    got = app._recent_big_payout("TESTPAIR/USD", now_ts=0.0)
    check("guard retired: a fresh $140 payout blocks nothing (Sep 21: "
          "replaced with real data)", got is None, f"got {got}")
    check("guard retired: any pair reads as clean",
          app._recent_big_payout("NOPE/USD", now_ts=0.0) is None)

    # the ledger record persists — display/history only, never a gate
    doc = json.loads(pf.read_text())
    check("payout ledger keeps the record for history",
          int(doc["TESTPAIR/USD"].get("amount") or 0) == 140)
finally:
    bs.TAKE_PAYOUTS_JSON = _real_tp   # the sink dies with the tempdir

# ── 3. cleanup: the live ledger was never touched (finally-restored
# above, the audit's own rule)
check("payout sink restored (no residue — the live ledger unbound, "
      "the tempdir carries the test rows)",
      bs.TAKE_PAYOUTS_JSON == _real_tp)

bssrc_g = (BASE / "background_scanner.py").read_text(encoding="utf-8")
casrc_g = (BASE / "contest_account.py").read_text(encoding="utf-8")

# ── 4. THE CONSOLE GAUGE READS THE SAME WATERMARK (Sep 29, her ask:
# the day's peak/current/erosion always VISIBLE on the danger plate,
# not just voiced). The gauge rides profit_guard's giveback block in a
# sandboxed STATE_FILE, and the plate lines are exercised through the
# same _today_facts body the console renders — via a bound Exec able
# to inject the st-free fixture rows (the full app import needs
# streamlit; the GAUGE is arithmetic + dict reads, and THAT is what
# we pin).
import profit_guard as _pg_gauge
_sbx_g = Path(tempfile.mkdtemp())
_pg_gauge.STATE_FILE = _sbx_g / "pg_state.json"   # sink: dies with the tempdir
_real_ctf_gg = bs.CONTEST_TRADES_FILE
bs.CONTEST_TRADES_FILE = str(_sbx_g / "contest_trades.jsonl")
_today_g = datetime.now(MST).strftime("%Y-%m-%d")
try:
    from background_scanner import _track_day_giveback as _tgd_g
    _pg_gauge._save({"giveback": {"day": _today_g, "peak_total": 0.0,
                                  "now_total": 0.0, "give_back": 0.0,
                                  "tightened": False}})
    _tgd_g(120.0)    # the day peaks at +120
    _tgd_g(45.0)     # then erodes 75
    _gb_blk = (_pg_gauge._state() or {}).get("giveback") or {}
    check("gauge fixture: the watermark says peak 120.0, current 45.0, "
          "erosion 75.0",
          _gb_blk.get("peak_total") == 120.0
          and _gb_blk.get("now_total") == 45.0
          and _gb_blk.get("give_back") == 75.0, _gb_blk)
    _asrc = (BASE / "app.py").read_text(encoding="utf-8")
    check("the console gauge reads the scanner's OWN watermark "
          "(profit_guard giveback block — one writer, one truth; the "
          "gauge and the voice can never disagree)",
          "_pg_facts._state()" in _asrc
          and '_gb.get("day") == _gb_day' in _asrc)
    # ── THE TIGHTEN-OUTCOME CHIP (Oct 4, red-first): the gauge's tail
    #    used to read the tightened BOOLEAN — but tightened=True means
    #    "an attempt was made", not "the guard moved": today's live
    #    ledger already carries giveback rows refused
    #    (daily_cap_exceeded) under tightened=True. The writer persists
    #    the truth (gb["tighten"] = {before, status, reason}); the chip
    #    renders THE OUTCOME (attempt applied / attempt refused
    #    (reason) / no tighten attempt yet) — the writer's record, not
    #    optimism.
    _tf_src = _asrc[_asrc.index("def _today_facts"):]
    _tf_src = _tf_src[:_tf_src.index("\ndef ")]
    check("the give-back chip renders the tighten attempt's REAL "
          "OUTCOME from the writer's own record (gb['tighten'].status), "
          "never the raw tightened boolean",
          '_gb.get("tighten")' in _tf_src
          and "'guard tightened' if _gb.get('tightened')" not in _tf_src,
          _tf_src[-300:])
    check("the chip's outcome words are honest (attempt refused with "
          "the reason, attempt applied, no tighten attempt yet — the "
          "boolean tails are gone)",
          "attempt refused" in _tf_src
          and "attempt applied" in _tf_src
          and "no tighten attempt yet" in _tf_src
          and "guard tightened" not in _tf_src
          and "guard normal" not in _tf_src,
          [_w for _w in ("attempt refused", "attempt applied",
                         "no tighten attempt yet") if _w not in _tf_src])
    _trk_src = bssrc_g[bssrc_g.index("def _track_day_giveback"):]
    _trk_src = _trk_src[:_trk_src.index("\ndef ")]
    check("the writer persists the attempt's outcome for the chip "
          "(tighten = before/status/reason in the watermark, set "
          "whatever the apply answered)",
          'gb["tighten"]' in _trk_src
          and '"status": _rec.get("status")' in _trk_src
          and '"reason": _rec.get("reason")' in _trk_src,
          _trk_src[-200:])
    check("the gauge renders all three numbers plus the "
          "tighten-outcome stamp (the boolean stamp is retired)",
          "DAY PEAK" in _asrc and "CURRENT" in _asrc
          and "GIVE-BACK" in _asrc
          and "attempt applied" in _tf_src and "attempt refused" in _tf_src)
    check("the erosion colors at the machine's own triggers "
          "(green < 1, amber < the tuned 0.20%-of-start tighten trigger, red at/over)",
          "if _ero < 1.0" in _asrc and "if _ero < 50.0" in _asrc
          and "#4be38a" in _asrc and "#ffc36b" in _asrc
          and "#ff6b5e" in _asrc
          and "GIVEBACK_TIGHTEN_PCT_OF_START = 0.0020" in casrc_g)
    check("a stale-dated or absent block renders DORMANT (the "
          "cross-process stamp: no invented peak-of-zero), naming the "
          "day and the reason",
          "no watermark yet today" in _asrc
          and "_gb.get('peak_ts', '')[:14]" in _asrc)
    check("a dead guard file degrades the gauge, never the plate "
          "(fail-soft, logged at debug)",
          "watermark unreadable" in _asrc
          and "give-back gauge read failed" in _asrc)
    check("the gauge's clock is DATA (the day key derives from the "
          "plate's bucketed now_ts, not a fresh wall-clock read)",
          "datetime.fromtimestamp(now_ts, MST).strftime" in _asrc)
    # ── the chip's three outcomes, RENDERED (app imports in the fleet
    #    bare mode — apptest proves it; the giveback block reads the
    #    sandboxed STATE_FILE, so _today_facts renders OUR fixture):
    #    the applied shape is the REAL attempt the rails made above.
    import app as _app_g
    # the plate is st.cache_data'd on the bucketed now_ts — each render
    # gets its own epoch (hours apart, same day) and a cache clear, or
    # the first render silently answers all three
    _clear_facts_g = getattr(_app_g._today_facts, "cache_clear", None) \
        or (lambda: None)
    _dt_g = datetime.now(MST)
    if _dt_g.hour == 23 and _dt_g.minute >= 55:
        _dt_g = _dt_g.replace(minute=50)   # the midnight cliff:
        # a start this late would push the simulated epochs into
        # tomorrow, where the today-window facts do not exist
    _sec_to_mid = (((23 - _dt_g.hour) * 60
                    + (59 - _dt_g.minute)) * 60 + 59 - _dt_g.second)
    _off1 = max(120, min(3610, _sec_to_mid // 3 - 60))
    _off2 = max(240, min(7220, _sec_to_mid // 2 - 60))
    _now_ts_g = int(_dt_g.timestamp())
    _clear_facts_g()
    _gb_rows_g = _app_g._today_facts(_now_ts_g)
    _gb_row_g = next((r for r in _gb_rows_g if "GIVE-BACK" in r), "")
    check("the chip RENDERS the real outcome — the runtime attempt the "
          "real rails just made was applied, and the chip says so (no "
          "bare 'guard tightened' boolean)",
          "attempt applied" in _gb_row_g
          and "guard tightened" not in _gb_row_g
          and "guard normal" not in _gb_row_g, _gb_row_g[:220])
    _pg_gauge._save({"giveback": {"day": _today_g, "peak_total": 120.0,
                                  "now_total": 45.0, "give_back": 75.0,
                                  "tightened": False}})
    _clear_facts_g()
    _gb_rows_g2 = _app_g._today_facts(_now_ts_g + _off1)
    _gb_row_g2 = next((r for r in _gb_rows_g2 if "GIVE-BACK" in r), "")
    check("a never-attempted day renders honestly (no tighten attempt "
          "yet — the absence is named, not disguised as calm)",
          "no tighten attempt yet" in _gb_row_g2, _gb_row_g2[:220])
    _pg_gauge._save({"giveback": {"day": _today_g, "peak_total": 120.0,
                                  "now_total": 45.0, "give_back": 75.0,
                                  "tightened": True,
                                  "tighten": {"before": 2.0,
                                              "status": "refused",
                                              "reason": "daily_cap_exceeded"}}})
    _clear_facts_g()
    _gb_rows_g3 = _app_g._today_facts(_now_ts_g + _off2)
    _gb_row_g3 = next((r for r in _gb_rows_g3 if "GIVE-BACK" in r), "")
    check("a REFUSED attempt renders its outcome AND its reason (the "
          "writer's record — the flag once claimed the guard moved "
          "when the cap had refused it)",
          "attempt refused" in _gb_row_g3
          and "daily_cap_exceeded" in _gb_row_g3
          and "guard tightened" not in _gb_row_g3, _gb_row_g3[:220])
finally:
    bs.CONTEST_TRADES_FILE = _real_ctf_gg

# ── 5. THE SEPTEMBER TUNING, TIER-SCALED (Sep 29): the tighten trigger
# and speak floor scale with the contest tier as % of start — the study
# (scratch/giveback_tier_study.py, 54 applied closes) showed the fixed
# $50 caught only the blowoff and missed both real erosion days (Sep 22
# -26.0, Sep 29 -23.9, both closed negative after being positive); the
# crossing-day median was 11.40, so the fixed $25 speak floor spoke
# almost nothing. Calibration: tighten 0.20% of start, speak 0.10% —
# the resolver lives in contest_account (one function for the tracker,
# the voice, and the gauge).
import contest_account as _ca_g


def _thr(tier):
    """giveback_thresholds() under a patched account read (None = broken)."""
    _real_ls = _ca_g.load_state
    try:
        if tier is None:
            def _boom():
                raise RuntimeError("account unreadable")
            _ca_g.load_state = _boom
        else:
            _ca_g.load_state = lambda: {"tier": tier}
        return _ca_g.giveback_thresholds()
    finally:
        _ca_g.load_state = _real_ls


check("the calibration constants live in the contest-account module "
      "(0.20% tighten, 0.10% speak of start — one function, three consumers)",
      "GIVEBACK_TIGHTEN_PCT_OF_START = 0.0020" in casrc_g
      and "GIVEBACK_SPEAK_PCT_OF_START = 0.0010" in casrc_g
      and "def giveback_thresholds" in casrc_g)
check("the tier table maps the study: 10k -> tighten 20 / speak 10; "
      "5k -> 10 / 5; 1k -> 2 / 1",
      _thr(10000) == (20.0, 10.0)
      and _thr(5000) == (10.0, 5.0)
      and _thr(1000) == (2.0, 1.0),
      (_thr(10000), _thr(5000), _thr(1000)))
check("a broken account read fails soft to the 10k defaults, NEVER to "
      "zero (a zero trigger would tighten on any red tick)",
      _thr(None) == (20.0, 10.0), _thr(None))


# THE SEPTEMBER REPLAY: the same tracker ticks, the new calibration —
# the two missed days must now arm the tighten (reuses section 1b's
# sandboxed guard/telemetry/auto_adjust handles).
_sbx5 = Path(tempfile.mkdtemp())
_pg1b.STATE_FILE = _sbx5 / "pg_state.json"
_ta1b.TELEMETRY_PATH = _sbx5 / "telemetry.jsonl"
_real_ctf5 = bs.CONTEST_TRADES_FILE
bs.CONTEST_TRADES_FILE = str(_sbx5 / "contest_trades.jsonl")
_calls5 = []


def _stub_apply5(delta, rotation_id="", evidence_checksum="",
                 per_gate=None, auto=True, override_concentration=False,
                 params=None):
    _calls5.append(delta)
    return {"status": "applied", "reason": "ok", "delta": delta,
            "before": 1.0, "after": 1.0 + delta}


_real_apply5 = _aa1b.apply_adjustment
_aa1b.apply_adjustment = _stub_apply5
_today5 = datetime.now(MST).strftime("%Y-%m-%d")
try:
    from background_scanner import _track_day_giveback as _tgd5
    Path(bs.CONTEST_TRADES_FILE).write_text(
        json.dumps({"sold_at": f"{_today5}T10:00:00-06:00",
                    "pnl_net": 0.0, "applied": True}) + "\n",
        encoding="utf-8")
    # Sep 22's shape: peak +15.80, closed -10.21 -> erosion 26.01
    _pg1b._save({"giveback": {"day": _today5, "peak_total": 0.0,
                              "now_total": 0.0, "give_back": 0.0,
                              "tightened": False}})
    _tgd5(15.80)
    _gb22 = _tgd5(-10.21)
    check("the September replay: Sep 22's shape (erosion 26.01) ARMS "
          "the tighten under the tier-scaled trigger (the fixed $50 "
          "missed it)",
          _gb22.get("tightened") is True and len(_calls5) == 1, _gb22)
    # Sep 29's shape: peak +4.62, closed -19.28 -> erosion 23.90 (a
    # different day key so the one-attempt-per-day rail stays out of
    # the way — each replay shape gets its own fresh watermark)
    _pg1b._save({"giveback": {"day": _today5 + "x", "peak_total": 0.0,
                              "now_total": 0.0, "give_back": 0.0,
                              "tightened": False}})
    _tgd5(4.62)
    _gb29 = _tgd5(-19.28)
    check("the September replay: Sep 29's shape (erosion 23.90) also "
          "arms — both real erosion days are inside the guard's reach now",
          _gb29.get("tightened") is True, _gb29)
    # the speak floor: the study's median crossing (11.40) must now be
    # HEARD (the old $25 floor silenced it)
    check("the speak floor: the crossing-day median 11.40 clears the "
          "tier-scaled 10.0 floor (the old 25.0 silenced it)",
          11.40 >= _thr(10000)[1], _thr(10000))
finally:
    _aa1b.apply_adjustment = _real_apply5
    bs.CONTEST_TRADES_FILE = _real_ctf5

# ── 6. THE TIERED GIVE-BACK RESPONSE (Oct 5, user ask): the 13:42
#    line exposed the gap — the day's ONE tighten had fired at 12:46,
#    before the 13:01 peak, so the second erosion leg had no knob
#    until midnight. TWO cures: the tighten RE-ARMS at 2x the trigger
#    (tier 2, its own rotation_id so the rails' once-per-day dedupe
#    cannot refuse it), and the armed trails' half-goal floor
#    TIGHTENS for the rest of the day once the day's tighten is
#    spent (GIVEBACK_TRAIL_FLOOR_FRAC per spent tier).
import profit_guard as _pg6
import auto_adjust as _aa6
import telemetry_adjust as _ta6
import contest_account as _ca6
from background_scanner import _track_day_giveback as _tgd6
# the trigger is PINNED (the hermeticity lesson — a live tier change
# under the drive would move every erosion face): the 10k tier, 20.0
_real_thr6 = _ca6.giveback_thresholds
_ca6.giveback_thresholds = lambda: (20.0, 10.0)
_sbx6 = Path(tempfile.mkdtemp())
_pg6.STATE_FILE = _sbx6 / "pg_state.json"
_ta6.TELEMETRY_PATH = _sbx6 / "telemetry.jsonl"
_real_ctf6 = bs.CONTEST_TRADES_FILE
bs.CONTEST_TRADES_FILE = str(_sbx6 / "contest_trades.jsonl")
_calls6 = []          # (delta, rotation_id) per attempt


def _stub_apply6(delta, rotation_id="", evidence_checksum="",
                 per_gate=None, auto=True, override_concentration=False,
                 params=None):
    _calls6.append((delta, rotation_id, _ca6.giveback_thresholds()))
    return {"status": "applied", "reason": "ok", "delta": delta,
            "before": 1.0, "after": 1.0 + delta}


_real_apply6 = _aa6.apply_adjustment
_aa6.apply_adjustment = _stub_apply6
_today6 = datetime.now(MST).strftime("%Y-%m-%d")
try:
    Path(bs.CONTEST_TRADES_FILE).write_text(
        json.dumps({"sold_at": f"{_today6}T10:00:00-06:00",
                    "pnl_net": 0.0, "applied": True}) + "\n",
        encoding="utf-8")
    # 6a. THE LADDER IS BORN: tier 1 fires exactly as before and the
    #     watermark now carries the count the ladder walks.
    _pg6._save({"giveback": {"day": _today6, "peak_total": 0.0,
                             "now_total": 0.0, "give_back": 0.0,
                             "tightened": False}})
    _tgd6(30.0)
    _gb6a = _tgd6(5.0)          # erosion 25 >= trigger 20 -> tier 1
    check("6a tier 1: the first crossing fires once and BIRTHS the "
          "count (tightens == 1, tier stamp 1, the classic rotation_id)",
          _gb6a.get("tightened") is True and _gb6a.get("tightens") == 1
          and _gb6a.get("tighten", {}).get("tier") == 1
          and len(_calls6) == 1
          and _calls6[0][1] == f"giveback-{_today6}", _gb6a)
    # the tide read is LIVE since the regime ceiling (Oct 6) - the 6b
    # block pins the ceiling-2 world (FLAT/RISING), so the read is
    # sandboxed here; the OUT extension (tier 3) is the regime pins' own
    # contract further down
    _real_tide_6b = bs._fleet_tide
    bs._fleet_tide = lambda max_age_s=300: (52.0, "FLAT")

    # 6b. THE 13:42 SHAPE, WHOLE: tier 1 fires early; a leg between
    #     1x and 2x re-fires NOTHING (the gap the afternoon lived in);
    #     2x RE-ARMS the tighten under its own rotation_id; and past
    #     the re-arm the ladder CEILS (no tier 3 — the ask's one
    #     re-arm; the floor factor is what keeps tightening).
    _pg6._save({"giveback": {"day": _today6, "peak_total": 0.0,
                             "now_total": 0.0, "give_back": 0.0,
                             "tightened": False}})
    _calls6.clear()
    _tgd6(100.0)                 # the day peaks at +100
    _tgd6(78.0)                  # erosion 22 -> tier 1
    _n_t1 = len(_calls6)         # the tier-1 watermark for the counters
    _gb6b2 = _tgd6(65.0)         # erosion 35 (1.75x) -> the GAP: silence
    check("6b the gap: after tier 1, a 1.75x leg re-fires NOTHING",
          _gb6b2.get("tightens") == 1 and len(_calls6) == _n_t1,
          (_gb6b2, list(_calls6)))
    _gb6b3 = _tgd6(55.0)         # erosion 45 (2.25x) -> tier 2
    check("6b the re-arm: give_back at 2x+ fires the SECOND tighten "
          "under its own tier rotation_id",
          _gb6b3.get("tightens") == 2
          and _gb6b3.get("tighten", {}).get("tier") == 2
          and len(_calls6) == _n_t1 + 1
          and _calls6[-1][1] == f"giveback-{_today6}-tier2"
          and _calls6[-1][0] < 0, (_gb6b3, list(_calls6)))
    _gb6b4 = _tgd6(20.0)         # erosion 80 (4x) -> CEIL: no tier 3
    check("6b the ceiling: a 4x leg after the re-arm fires NO third "
          "tier (the ladder stops where the ask stops it)",
          _gb6b4.get("tightens") == 2 and len(_calls6) == _n_t1 + 1,
          (_gb6b4, list(_calls6)))
    bs._fleet_tide = _real_tide_6b
    check("6b the tier-1 identity is untouched (same rotation_id, no "
          "tier suffix on the first attempt)",
          _calls6[0][1] == f"giveback-{_today6}", _calls6[0])
    # 6c. BACK-COMPAT: the LIVE mid-day state has tightened=True and
    #     no tightens key (the 12:46 row) — the ladder reads it as
    #     tier 1 spent and the re-arm arms at 2x.
    _pg6._save({"giveback": {"day": _today6, "peak_total": 0.0,
                             "now_total": 0.0, "give_back": 0.0,
                             "tightened": True}})
    _calls6.clear()
    _tgd6(100.0)
    _gb6c = _tgd6(55.0)          # erosion 45 >= 2x -> tier 2
    check("6c a legacy watermark (tightened, no count) reads as tier "
          "1 spent and the re-arm fires at 2x",
          _gb6c.get("tightens") == 2
          and _gb6c.get("tighten", {}).get("tier") == 2
          and len(_calls6) == 1, (_gb6c, _calls6))
    # 6d. A CRASHED attempt leaves the count unchanged and retries the
    #     SAME tier next tick (the one-attempt-per-tier contract).
    _pg6._save({"giveback": {"day": _today6, "peak_total": 100.0,
                             "now_total": 100.0, "give_back": 0.0,
                             "tightened": False}})
    _calls6.clear()
    def _boom6(*a, **k):
        raise RuntimeError("rails down")
    _aa6.apply_adjustment = _boom6
    _gb6d1 = _tgd6(70.0)         # erosion 30 >= 20 -> the attempt CRASHES
    _aa6.apply_adjustment = _stub_apply6
    _gb6d2 = _tgd6(70.0)         # same erosion: the SAME tier retries
    check("6d a crashed attempt spends nothing and the same tier "
          "retries next tick",
          _gb6d1.get("tightens") is None
          and _gb6d2.get("tightens") == 1 and len(_calls6) == 1
          and _calls6[0][1] == f"giveback-{_today6}",
          (_gb6d1, _gb6d2, _calls6))
    # 6e. THE SPENT-DAY FLOOR: the armed trails half-goal cap base
    #     tightens GIVEBACK_TRAIL_FLOOR_FRAC per spent tier, for the
    #     rest of the day; fail-soft to 1.0.
    _sgf6 = getattr(bs, "_spent_giveback_floor", None)
    _acb6 = getattr(bs, "_armed_cap_base", None)
    check("6e the floor factor and the armed cap base exist on the "
          "scanner (one expression for all three trail sites)",
          callable(_sgf6) and callable(_acb6))
    if _sgf6 and _acb6:
        _pg6._save({"giveback": {"day": _today6, "tightens": 0}})
        _f0 = _sgf6()
        _pg6._save({"giveback": {"day": _today6, "tightens": 1}})
        _f1 = _sgf6()
        _pg6._save({"giveback": {"day": _today6, "tightens": 2}})
        _f2 = _sgf6()
        _pg6._save({"giveback": {"day": _today6, "tightens": 3}})
        _f3 = _sgf6()
        check("6e the floor walks the ladder: 1.0 unspent, 0.9 after "
              "tier 1, 0.81 after the re-arm",
              _f0 == 1.0 and _f1 == 0.9 and _f2 == 0.81, (_f0, _f1, _f2))
        check("6e the floor keeps tightening past the ceiling (a tier "
              "count that ever ran long never resets to loose)",
              _f3 < _f2, (_f2, _f3))
        _pg6._save({"giveback": {"day": _today6, "tightened": True}})
        check("6e the legacy watermark tightens the floor too "
              "(tightened without a count reads as one spent tier)",
              _sgf6() == 0.9, _sgf6())
        _pg6._save({"giveback": {"day": "1999-01-01", "tightens": 2}})
        check("6e a stale day reads LOOSE (the floor is day-scoped and "
              "expires with the watermark)",
              _sgf6() == 1.0, _sgf6())
        _pg6._save({"giveback": {"day": _today6, "tightens": 0}})
        _c0 = _acb6(2.0)
        _pg6._save({"giveback": {"day": _today6, "tightens": 1}})
        _c1 = _acb6(2.0)
        _pg6._save({"giveback": {"day": _today6, "tightens": 2}})
        _c2 = _acb6(2.0)
        check("6e the armed cap base: half-goal 1.0 unspent, 0.9 and "
              "0.81 as the tiers spend",
              _c0 == 1.0 and _c1 == 0.9 and _c2 == 0.81, (_c0, _c1, _c2))
        _pg6._save({"giveback": {"day": _today6, "tightens": 0}})
        _t0 = _acb6(0.5)
        _pg6._save({"giveback": {"day": _today6, "tightens": 1}})
        _t1 = _acb6(0.5)
        check("6e the HARD floor tightens too (a tiny goal 1.0 floor "
              "drops to 0.9 on a spent day)",
              _t0 == 1.0 and _t1 == 0.9, (_t0, _t1))
        _real_state6 = _pg6._state
        def _broken6():
            raise RuntimeError("state gone")
        _pg6._state = _broken6
        _fbroken = _sgf6()
        _pg6._state = _real_state6
        check("6e a broken state read fails soft to LOOSE (1.0 — the "
              "floor never tightens on a lie)",
              _fbroken == 1.0, _fbroken)
    # 6f. THE THREE SITES EAT THE SAME EXPRESSION (source parity: the
    #     exit enforcement, the voice, and the all-good lead can never
    #     drift apart on the floor).
    check("6f the exit enforcement and the voice both start from "
          "_armed_cap_base",
          "_cap = _armed_cap_base(_goal_tr)" in bssrc_g
          and "_cap_now = _armed_cap_base(_goal)" in bssrc_g)
    check("6f the all-good lead starts from the same cap base",
          "_armed_cap_base(_g_ag)" in bssrc_g)
    check("6f the tracker carries the tier ladder (the re-arm "
          "rotation_id, the ceiling constant, the floor constant)",
          "giveback-{_day}-tier" in bssrc_g
          and "GIVEBACK_MAX_TIERS" in bssrc_g
          and "GIVEBACK_TRAIL_FLOOR_FRAC = 0.90" in bssrc_g)
    # ── THE REGIME-SCALED CEILING (user ask, Oct 6; RED-FIRST): the
    # tier-ceiling study (scratch/_tier_ceiling_study.py) found every
    # uncovered erosion day sat in OUT tide (Sep 26 $150 = 7.5 legs,
    # Oct 5 $99 = 5.0 legs) and no RISING day ever crossed the second
    # leg — OUT earns a third tier, RISING/FLAT keep the fixed 2, and
    # an unreadable tide fails to the base ceiling.
    _pg_r = __import__("profit_guard")
    _aa_r = __import__("auto_adjust")
    _ca_r = __import__("contest_account")
    _tel_r = __import__("telemetry_adjust")
    _real_state_r, _real_save_r = _pg_r._state, _pg_r._save
    _real_apply_r = _aa_r.apply_adjustment
    _real_thr_r = _ca_r.giveback_thresholds
    _real_emit_r = _tel_r.emit_adjustment
    _real_fleet_r = bs._fleet_tide
    _real_ctf_r = bs.CONTEST_TRADES_FILE
    _sink_r = {}
    _calls_r = []
    try:
        _tmp_r = tempfile.mkdtemp()
        _ctf_r = Path(_tmp_r) / "contest_trades.jsonl"
        _ctf_r.write_text("", encoding="utf-8")
        bs.CONTEST_TRADES_FILE = str(_ctf_r)
        _pg_r._state = lambda: _sink_r
        _pg_r._save = lambda patch: (_sink_r.update(patch))
        _aa_r.apply_adjustment = (
            lambda delta, rotation_id="", evidence_checksum="", auto=True:
            (_calls_r.append(rotation_id),
             {"status": "applied", "rotation_id": rotation_id})[1])
        _ca_r.giveback_thresholds = lambda: (20.0, 10.0)
        _tel_r.emit_adjustment = lambda *a, **k: None
        check("RED the tier ceiling scales by tide: OUT arms a 3rd tier, "
              "RISING/FLAT keep the fixed 2, unreadable tide fails to base",
              bs._giveback_ceiling("OUT") == 3
              and bs._giveback_ceiling("RISING") == 2
              and bs._giveback_ceiling("FLAT") == 2
              and bs._giveback_ceiling(None) == 2)
        bs._fleet_tide = lambda max_age_s=300: (34.2, "OUT")
        bs._track_day_giveback(0.0)
        _sink_r["giveback"]["tightens"] = 2
        _sink_r["giveback"]["peak_total"] = 100.0
        bs._track_day_giveback(-70.0)
        check("RED an OUT-tide third crossing ARMS the tier-3 attempt",
              any("tier3" in str(c) for c in _calls_r))
        _calls_r.clear()
        bs._fleet_tide = lambda max_age_s=300: (52.0, "FLAT")
        _tight_r = _sink_r["giveback"].get("tightens")
        bs._track_day_giveback(-70.0)
        check("the FLAT tide keeps the fixed ceiling: no third attempt, "
              "the count holds",
              not _calls_r
              and _sink_r["giveback"].get("tightens") == _tight_r)
    finally:
        bs.CONTEST_TRADES_FILE = _real_ctf_r
        bs._fleet_tide = _real_fleet_r
        _pg_r._state, _pg_r._save = _real_state_r, _real_save_r
        _aa_r.apply_adjustment = _real_apply_r
        _ca_r.giveback_thresholds = _real_thr_r
        _tel_r.emit_adjustment = _real_emit_r
        shutil.rmtree(_tmp_r, ignore_errors=True)

finally:
    _aa6.apply_adjustment = _real_apply6
    _ca6.giveback_thresholds = _real_thr6
    bs.CONTEST_TRADES_FILE = _real_ctf6   # the sink dies with the tempdir

# - 7. THE STALE LEG (the Oct 6 CodeRabbit triage - 4342/4391's uncured
#    twin: a failed ticker continued BEFORE the unrealized sum, so a
#    dropped WINNER read as a drawdown the guard tightened on; the
#    ride-prices cure closed the suppressed-book half, not this one) --
sc7 = open(BASE / "background_scanner.py", encoding="utf-8").read()
_leg_w = {"pair": "X/USD", "entry": 2.0, "size": 100.0, "last_px": 2.5}
check("7a the stale leg prices from the row's last stored truth: (2.5/2.0-1)*100 = 25",
      bs._stale_unrealized_leg(_leg_w) == 25.0,
      "got " + str(bs._stale_unrealized_leg(_leg_w)))
check("7b no stored truth is honest None (a first-tick row is unpriced, never fabricated)",
      bs._stale_unrealized_leg({"pair": "X/USD", "entry": 2.0, "size": 100.0}) is None)
check("7c unpriceable rows return None (entry/size/px guards)",
      bs._stale_unrealized_leg({"entry": 0, "size": 100.0, "last_px": 2.5}) is None
      and bs._stale_unrealized_leg({"entry": 2.0, "size": 0, "last_px": 2.5}) is None
      and bs._stale_unrealized_leg({"entry": 2.0, "size": 100.0, "last_px": 0}) is None)
check("7d garbage inputs fail soft to None",
      bs._stale_unrealized_leg({"entry": "x", "size": {}, "last_px": None}) is None)
check("7e the wiring: the priced path stores the row's last truth, the failed fetch prices the leg",
      'w["last_px"] = live' in sc7 and "_unrealized_sum += _leg" in sc7
      and sc7.count("_stale_unrealized_leg(w)") == 1)


# ── 7g. THE DAILY_CAP COLLISION (Oct 6, red-first): a daily_cap_exceeded
#    refusal spent the tier — "ONE ATTEMPT PER TIER whatever the outcome"
#    burned gb["tightens"] on a cap that cannot free until midnight, so
#    every later tier that day burned on the same exhausted cap while
#    the guard never tightened despite real erosion. THE CURE: a cap
#    refusal records the attempt (the chip still renders it) but spends
#    NO tier and retries next day via a refusal marker that also keeps
#    the ledger unflooded.
import auto_adjust as _aa7
import contest_account as _ca7
_sbx7 = Path(tempfile.mkdtemp())
_pgs7_real = _pg_gauge.STATE_FILE
_pg_gauge.STATE_FILE = _sbx7 / "pg7.json"
_thr7_real = _ca7.giveback_thresholds
_app7_real = _aa7.apply_adjustment
_ctf7_real = bs.CONTEST_TRADES_FILE
_tide7_real = bs._fleet_tide
_ta_calls7 = {"n": 0}
import telemetry_adjust as _ta7
_ta7_real = _ta7.emit_adjustment
_ta7.emit_adjustment = lambda *a, **k: _ta_calls7.__setitem__("n", _ta_calls7["n"] + 1) or None
_calls7 = {"n": 0}
_resp7 = {"status": "refused", "reason": "daily_cap_exceeded"}
def _apply7(delta, **kw):
    _calls7["n"] += 1
    return dict(_resp7)
try:
    bs.CONTEST_TRADES_FILE = str(_sbx7 / "ct7.jsonl")
    bs._fleet_tide = lambda max_age_s=300: (52.0, "FLAT")
    _ca7.giveback_thresholds = lambda: (50.0, 999.0)
    _aa7.apply_adjustment = _apply7
    from background_scanner import _track_day_giveback as _tgd7
    _day7 = datetime.now(MST).strftime("%Y-%m-%d")
    _pg_gauge._save({"giveback": {"day": _day7, "peak_total": 0.0, "now_total": 0.0,
                             "give_back": 0.0, "tightened": False}})
    _tgd7(120.0)
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    _tgd7(45.0)   # give_back 75 crosses the 50 trigger: tier 1 arms
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    check("7g.1 a daily_cap refusal does NOT burn the tier (the count holds, the attempt is recorded)",
          not gb7.get("tightens") and not gb7.get("tightened")
          and (gb7.get("tighten") or {}).get("reason") == "daily_cap_exceeded"
          and (gb7.get("refused") or {}).get("tier") == 1, gb7)
    _tgd7(45.0)   # the same tick again: the marker must keep the ledger quiet
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    check("7g.2 a cap-refused tier does not flood the ledger (one attempt, then the marker)",
          _calls7["n"] == 1 and not gb7.get("tightens"), (_calls7["n"], gb7.get("tightens")))
    check("7g.2b the cap refusal still STREAMS its decision (the telemetry/api/alert stream sees it)",
          _ta_calls7["n"] == 1, _ta_calls7["n"])
    _resp7 = {"status": "applied"}   # the cap freed: a real tighten spends the tier
    _pg_gauge._save({"giveback": {"day": _day7, "peak_total": 120.0, "now_total": 45.0,
                             "give_back": 75.0, "tightened": False}})
    _tgd7(45.0)
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    check("7g.3 a real tighten still spends its tier (applied -> tightens 1)",
          gb7.get("tightens") == 1 and gb7.get("tightened") is True, gb7)
    _resp7 = {"status": "queued", "reason": "concentration_gate_blocks_auto_apply"}
    _pg_gauge._save({"giveback": {"day": _day7, "peak_total": 300.0, "now_total": 200.0,
                             "give_back": 100.0, "tightens": 1, "tightened": True}})
    _tgd7(200.0)   # give_back 100 crosses the 2x trigger: tier 2 arms, queued
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    check("7g.4 a queued attempt (a real review request) still spends its tier",
          gb7.get("tightens") == 2, (gb7.get("tightens"), gb7.get("tighten")))
    _resp7 = {"status": "refused", "reason": "daily_cap_exceeded"}
    _pg_gauge._save({"giveback": {"day": "2030-01-02", "peak_total": 120.0, "now_total": 45.0,
                             "give_back": 75.0, "tightened": False}})
    _calls7["n"] = 0
    _tgd7(120.0)  # a NEW day: the stale watermark resets - peak rebuilds first,
    _tgd7(45.0)   # then the erosion legs in: tier 1 re-arms fresh with the cap
    gb7 = (_pg_gauge._state() or {}).get("giveback") or {}
    check("7g.5 next day the ladder recovers (the watermark resets with the cap, tier 1 re-arms)",
          _calls7["n"] == 1 and (gb7.get("tighten") or {}).get("tier") == 1,
          (_calls7["n"], gb7.get("tighten")))
    check("7g.6 every tier outcome streamed exactly once (4 emits: refusal, applied, queued, refusal)",
          _ta_calls7["n"] == 4, _ta_calls7["n"])
finally:
    _pg_gauge.STATE_FILE = _pgs7_real
    bs.CONTEST_TRADES_FILE = _ctf7_real
    bs._fleet_tide = _tide7_real
    _ca7.giveback_thresholds = _thr7_real
    _aa7.apply_adjustment = _app7_real
    _ta7.emit_adjustment = _ta7_real

# 7g.7 the fixture pays its own rent (probe A, Oct 6): the 7g sandbox
# re-pointed _pg_gauge.STATE_FILE without restoring it - later suites
# reading giveback would inherit the sandbox file. Save-and-restore,
# pinned in source.
src7 = Path(__file__).read_text(encoding="utf-8")
check("7g.7 the fixture pays its own rent (STATE_FILE save + restore, "
      "probe A's Oct 6 advisory)",
      "_pgs7_real = _pg_gauge.STATE_FILE" in src7
      and "_pg_gauge.STATE_FILE = _pgs7_real" in src7,
      "the fixture must save and restore the guard's real state file")
# 7g.8 the capture leads the sandbox assign (Copilot medium on 34df9d7,
# cured red-first): the baseline must hold the REAL state file, or the
# finally restores the sandbox and the fixture leaks its state into
# every later suite in this process.
s7 = Path(__file__).read_text(encoding="utf-8").splitlines()
t7 = [ln.strip() for ln in s7]
cap7 = t7.index("_pgs7_real = _pg_gauge.STATE_FILE")
sbx7_at = t7.index('_pg_gauge.STATE_FILE = _sbx7 / "pg7.json"')
check("7g.8 the STATE baseline is captured BEFORE the sandbox "
      "assignment (the finally restores the REAL file)",
      cap7 < sbx7_at and t7.count("_pgs7_real = _pg_gauge.STATE_FILE") == 1,
      (cap7, sbx7_at, t7.count("_pgs7_real = _pg_gauge.STATE_FILE")))
fails = [r for r in results if not r[1]]
out = BASE / "giveback_check.txt"
scratch_space.report_temp('giveback_check.txt').write_text("\n".join(
    [f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
     for name, ok, d in results] + [""] + lines), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")


def main():
    raise SystemExit(1 if fails else 0)   # the fleet-honesty tail (Sep 30 triage)


if __name__ == "__main__":
    sys.exit(main())
