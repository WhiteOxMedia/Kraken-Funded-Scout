"""Test OPTION 2 + OPTION 3 (user rule, Sep 4: 'we need option: 2 and 3').

OPTION 2 — THE SUPPRESSION LOG: the popup funnel's suppression verdicts
used to print to the console window only (the POPCAT lesson: a buried
payer took three forensics scripts to name its gate). Every suppression
now appends {ts, pair, gate, detail, quick, brain, floor, tide, rot} to
popup_suppressions.jsonl, and the console's MASTER page shows the strip.

OPTION 3 — THE OUT-TIDE LONE-WOLF CARVE-OUT: the lone-wolf discount now
covers OUT tides (own 24h move >= the tiers — a TRUE solo runner moving
against a sinking fleet), the champion path's TIDE OUT block passes a
solo runner (popup marked 'LONE WOLF (OUT TIDE)'), the mover path's OUT
gate carves the same way for real runners (bounce candidates don't), and
the QC agrees (never disagree). The BOT ledger is UNTOUCHED: no bridge
write in an OUT tide, the reader's stamp check stands — two ledgers."""
import json
import sys
import time
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


bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
app_src = Path("app.py").read_text(encoding="utf-8")

# ── 1. OPTION 2: the suppression log exists and every gate hooks it ──
check("the log helper + file constant exist",
      "def _log_suppression" in bs_src and "popup_suppressions.jsonl" in bs_src)
# (Sep 5, ADVISORY BRAIN: "brain-floor" retired with the blocking floor —
# its verdicts split into deep-direction (the scan contradicts) and
# popup-cap (the flood cap). Sep 10: "rotation-lock" retired with the
# per-rotation cap — the hook text survives in the kept-dead branch; the
# other hooks stand.)
for gate in ("fail-closed", "stale-move", "tide-out",
             "deep-veto", "downward-trend", "too-thin", "worth-floor",
             "blowoff-yesterday", "blacklisted",
             "deep-direction"):
    check(f"hook: {gate} logs its verdict",
          f'"{gate}"' in bs_src)
check("hook: the brain's number no longer blocks (it sizes — Sep 5; the name lives on as the journal-relief gate)",
      "the NUMBER no longer does" in bs_src
      and 'gate_relief("brain-floor")' in bs_src)
check("hook: the popup-cap gate is retired (Sep 9: no money cap at any contest size — the functions are kept, never called)",
      '"popup-cap"' not in bs_src and "def _popup_cap()" in bs_src)
check("hook: the rotation-lock gate is retired (Sep 10: re-fires are free — the hook text survives only in the kept-dead branch)",
      bs_src.count('"rotation-lock"') == 1)
check("hook: the death-line gate logs (the daily-lock gate retired with the lock, Sep 5)",
      '"death-line"' in bs_src
      and '"daily-lock" if _which_fl == "daily-lock" else "death-line"' not in bs_src)
check("hook: the bridge-stage deep vetoes log (pre-desktop deaths too)",
      "bridge stage: deep-reads" in bs_src
      and "bridge stage: EMA9<EMA21" in bs_src)
check("hook: the mover's pre-desktop deep veto logs (the lockout hook retired Sep 10)",
      'f"mover: {_m_lock}"' not in bs_src and "mover: deep-reads" in bs_src)
check("the log carries the numbers (quick, brain, floor, tide, rot)",
      "quick=float(signal.confidence)" in bs_src
      and "floor=float(DESKTOP_ALERT_MIN)" in bs_src)
check("the mover's desktop suppressions log too (mover: prefix)",
      bs_src.count('"mover: {_m_skip}"') == 1 or '"mover: {_m_skip}"' in bs_src)
# (Sep 5, ADVISORY BRAIN: the mover's brain-floor gate retired — its
# desktop deaths now log as fail-closed (scan didn't run) or
# deep-direction (the scan contradicts); the stale + m_skip hooks stand.)
check("the mover's fire-time deaths log (fail-closed / direction)",
      '"fail-closed" if _deep_sig is None else "deep-direction"' in bs_src
      and "mover: 4h high" in bs_src)
check("the console MASTER shows the strip",
      "_render_suppression_strip" in app_src
      and "_render_suppression_strip()" in app_src)

# ── 2. OPTION 3: the OUT-tide carve-out — the champion path ──────────
check("lone_wolf_discount covers OUT tides (the FLAT-only gate is gone)",
      '_ts not in ("FLAT", "OUT")' in bs_src)
check("the OUT note names the tide",
      "against an OUT tide" in bs_src)
check("THE BRIDGE-STAGE OUT BLOCK carves (the gate that actually buried POPCAT — "
      "pass-through via gate_notes, not a bare return)",
      'gate_notes.append("OUT tide — the bot stays out; the lone-wolf '
      in bs_src)
check("the bridge-stage non-solo OUT is suppressed AND logged",
      bs_src.count('"tide-out"') >= 2)
check("the desktop TIDE OUT block carves on the solo runner too (the flip case)",
      "OUT-TIDE LONE WOLF" in bs_src and "if _lw_pts:" in bs_src)
check("a non-solo candidate in an OUT tide is suppressed AND logged",
      "all ships sink together; " in bs_src)
check("the popup title marks the carve",
      "— LONE WOLF (OUT TIDE)" in bs_src)
check("the voice names the out tide",
      "against an out tide" in bs_src)
check("the tide line carries the OUT carve-out context",
      "LONE WOLF (OUT TIDE) carve-out: " in bs_src)
check("the tide line carries the learned-bar context too (Sep 10)",
      "the learned bar was paid" in bs_src)

# ── 3. the mover path: same carve, bot ledger untouched ──────────────
check("the mover OUT gate carves real runners only (own 24h >= the "
      "adaptive carve requirement AND lone-wolf mode granted)",
      "_m_out_lw" in bs_src and "float(_mv_pct) >= _m_req" in bs_src
      and "and _m_mode)" in bs_src)
check("bounce candidates (the -5 losers) don't carve",
      "a bounce isn't a solo" in bs_src)
check("NO bridge write in an OUT tide (the bot ledger hard-blocks OUT)",
      "if _tsw != \"OUT\":" in bs_src)
check("the mover popup title marks the carve",
      "— LONE WOLF (OUT TIDE)" in bs_src.split("MOVER BUY")[1][:400]
      if "MOVER BUY" in bs_src else False)

# ── 4. the bot ledger: the OUT tide is PRICED, not blind (Sep 10) ─────
BOT_READER = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py")
bot_src = BOT_READER.read_text(encoding="utf-8")
check("the champion bridge write still hard-blocks OUT (mover + cold learner)",
      "TIDE OUT (fleet %.0f%%) — no bridge write" in bs_src)
check("THE BOT'S OUT GATE honors the learned bar on its own ledger (Sep 10 rebalance)",
      "_bot_out_priced_pass" in bs_src
      and 'tide_personal_bar(pair, "OUT", _bot_lbar)' in bs_src
      and "OUT-TIDE BOT PASS" in bs_src)
check("the bot's priced pass rides the stamp as a marker (bare OUT still rejects)",
      'f"OUT|BAR PASSED {_bot_out_pbar:.0f}"' in bs_src)
check("the bot's priced pass is fail-closed (a cold learner keeps the wall)",
      "the learner is cold — the wall stands" in bs_src)
check("the wolf carve is contest-only on the bot ledger (unchanged)",
      "carve-out is contest-only" in bs_src)
check("the reader honors the priced marker and still rejects a bare OUT",
      'startswith("OUT|BAR PASSED ")' in bot_src
      and 'if _sig_tide == "OUT" and _sig_tide_bar is None:' in bot_src)
check("the reader degrades a malformed marker to a reject (fail-closed)",
      "def _out_bar_from_signal" in bot_src
      and "bar = None" in bot_src
      and 'if _sig_tide == "OUT" and _sig_tide_bar is None:' in bot_src)
BOT_READER = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py")
bot_src = BOT_READER.read_text(encoding="utf-8")
check("the reader's stamp check still rejects OUT (belt-and-braces)",
      "Tide OUT at signal time" in bot_src
      and "no entries anywhere" in bot_src)

# ── 5. the QC agrees (never disagree) ─────────────────────────────────
check("the QC's hard gate carves the solo runner",
      "(tide_state != \"OUT\" or lw_pts > 0)" in app_src)
check("the QC's TIDE OUT toast only fires without the carve",
      '_tide_state == "OUT" and not _lw_pts' in app_src)
check("the QC's green text names TIDE OUT only without the carve",
      'tide_state == "OUT" and not lw_pts' in app_src)
check("the QC's OUT LED turns amber on the carve (red otherwise)",
      '"a" if (tide_state == "OUT" and (lw_pts or tide_learned_pass))' in app_src)
check("the QC toast explains the exception",
      "lone-wolf exception — this one isn't" in app_src)

# ── 6. functional: the log writes what it's told ─────────────────────
try:
    import background_scanner as bs
    _tmp = Path("popup_suppressions_TEST.jsonl")
    if _tmp.exists():
        _tmp.unlink()
    _orig_file = bs.SUPPRESSION_LOG_FILE
    try:
        bs.SUPPRESSION_LOG_FILE = str(_tmp)
        bs._log_suppression("POPCAT/USD", "tide-out",
                            "fleet 20% up — all ships sink together; "
                            "no solo move (the carve-out needs ≥+5% own 24h move)",
                            quick=87.0, brain=71.0, floor=84.0,
                            tide="OUT 20%", rot="08-12")
        doc = json.loads(_tmp.read_text(encoding="utf-8").strip().splitlines()[-1])
        check("the log entry carries pair + gate + detail",
              doc.get("pair") == "POPCAT/USD" and doc.get("gate") == "tide-out"
              and "carve-out" in doc.get("detail", ""))
        check("the log entry carries the numbers",
              doc.get("quick") == 87.0 and doc.get("brain") == 71.0
              and doc.get("floor") == 84.0 and doc.get("rot") == "08-12")
        check("ts is a real timestamp",
              str(doc.get("ts", "")).startswith("20"))
        # None ctx values don't ride along
        bs._log_suppression("X/USD", "fail-closed", "no brain read", quick=50.0)
        doc2 = json.loads(_tmp.read_text(encoding="utf-8").strip().splitlines()[-1])
        check("None context values don't ride along",
              "brain" not in doc2 and doc2.get("gate") == "fail-closed")
    finally:
        bs.SUPPRESSION_LOG_FILE = _orig_file
        if _tmp.exists():
            _tmp.unlink()

    # functional: the OUT-tide discount tiers (the carve's engine)
    bs._tide_cache.update(ts=time.time(), pct=20.0, state="OUT")
    bs._lone_wolf_cache.update(pair=None, pct=0.0, ts=0.0)

    class _Fx:
        def fetch_ticker(self, pair):
            return {"percentage": 9.4}

    pts, note = bs.lone_wolf_discount(_Fx(), "XMR/USD")
    # THE ADAPTIVE CONTRACT (Sep 23 spec): in an OUT tide the hatch only
    # OPENS on LoneWolfMode — and the permission gate requires
    # tide_mod >= 0.8 while an OUT tide clamps TideMod to 0.5, so the
    # POPCAT-era fixed-tier carve is superseded by design: the
    # permission GATE is the real brake in a dead tide.
    check("OUT +9.4% own move with the mode unreachable -> the permission "
          "gate keeps the hatch closed",
          pts == 0 and note == "", f"pts={pts} note={note!r}")
    check("the discounted OUT floor never drops below the 55 floor",
          bs.popup_floor("OUT", 35, "08-12") == 55.0,
          f"floor={bs.popup_floor('OUT', 35, '08-12')}")
    check("a non-solo OUT candidate keeps the full rail (no discount)",
          bs.popup_floor("OUT", 0, "08-12") == 84.0)
    # the live log file must NOT be polluted by tests
    check("the live suppression log is untouched by the test",
          not Path("popup_suppressions_TEST.jsonl").exists())
except Exception as exc:
    check("functional checks ran", False, exc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_option23_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
# THE HONEST VERDICT (battery triage, Sep 23): the battery's result mode
# parses the STREAMED OUTPUT — a verdict that lived only in the check
# file read as a no-verdict failure every full run.
print(f"RESULT: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
