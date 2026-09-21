"""diag_dialaudit.py — THE DIAL AUDIT GATE (Sep 17).

WHY THIS EXISTS: the HER VOLUME dial shipped as a no-op (the amp script
wrote its gain through a read-only stream — every line played at 100%),
and the exit_watch key stored the AND of two toggles, so one AUTO-WATCH
off-cycle silently killed the fleet's GET OUT ladder (the bridge reads
that key). Shared-file dials fail exactly like that: quietly, at the
seams, months after the knob was built. This gate pins every
console→scanner dial's CONTRACT — who writes it, who reads it, what it
is allowed to shape, and what it must NEVER shape — so the next drift
fails the battery instead of the trading day.

Run: .venv\\Scripts\\python.exe -X utf8 diag_dialaudit.py   (exit 0 = green)
Writes _dialaudit.txt.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_dialaudit.txt"
lines = []


def say(msg=""):
    lines.append(msg)
    print(msg)


# ── THE CONTRACTS ────────────────────────────────────────────────────────
# (key, written by, read by, shapes, never shapes)
CONTRACTS = [
    ("voice_volume", "console (HER VOLUME slider)",
     "scanner (_her_volume_pct)",
     "console-queued speech_queue lines ONLY",
     "scanner-origin alerts (pick / goal / GET OUT / chase / tide)"),
    ("voice_on", "console (HER VOICE toggle)",
     "scanner (_voice_enabled)",
     "whether ANY line is spoken",
     "captions, logs, phone pushes, popups"),
    ("watch_on", "console (AUTO-WATCH toggle)",
     "console only (browser minute-watch)",
     "the browser watch panel",
     "the scanner, the bot, the bridge — EVER"),
    ("exit_watch", "console (EXIT WATCH toggle, its own key)",
     "scanner + bot via signal_bridge.exit_watch_enabled()",
     "the fleet's trend-exit / GET OUT ladder",
     "the mechanical ladder (stop-loss / TP / trailing); watch_on's value"),
    ("goal_on", "console (GOAL CALL toggle)",
     "console only",
     "the browser watch's goal-call toast",
     "the scanner's GOAL CALL voice (always on)"),
    ("tier", "console (contest tier picker)",
     "scanner (_tier_txt) + console plates",
     "sizing arithmetic + displayed contest size",
     "gates/bars (the fleet floors are size-aware)"),
    ("worth_floor_frac", "console (worth floor dial)",
     "scanner (_worth_floor_frac)",
     "the popup worth floor BASE (auto-tune may override)",
     "the exit trail, the sizing roster"),
    ("climb_strictness", "console (climb strictness dial)",
     "scanner (_climb_strictness)",
     "the climb-out algo's clearance strictness",
     "the popup floor itself"),
    ("popup_cap", "console (RETIRED Sep 9)",
     "nobody (kept per don't-delete rule)",
     "nothing",
     "nothing (dead by design — no fire path calls it)"),
]


def main():
    # ── 1. SELF-CHECK: the scanner's mouth honors the never-shape lines ──
    _speak = (ROOT / "background_scanner.py").read_text(encoding="utf-8")

    self_ok = True
    try:
        m = re.search(
            r"if source in CONSOLE_SOURCES:.*?subprocess\.Popen\(_cmd",
            _speak, re.S)
        _mouth = m.group(0) if m else ""
        check1 = (
            'CONSOLE_SOURCES = {"console_queue"}' in _speak
            and "def _her_volume_pct" in _speak
            and "def _voice_enabled" in _speak
            and "if source in CONSOLE_SOURCES:" in _mouth
            and "-Volume" in _mouth
            and "else:" in _mouth
            and "100%" in _mouth
            and "HER VOLUME: %s alert at 100%%" in _speak
            and "HER VOLUME: console line at %d%%" in _speak
            and "HER VOLUME: console line dialed OUT (vol 0)" in _speak
            and _mouth.index("return  # dialed to zero")
            < _mouth.index("subprocess.Popen")
        )
        say("SELF-CHECK — the mouth's dial contract")
        say(f"  dial reaches ONLY console-sourced lines, alerts always 100, "
            f"mute returns before spawn, proof lines log: "
            f"{'OK' if check1 else 'VIOLATION'}")
        self_ok &= check1
    except Exception as exc:
        self_ok = False
        say(f"SELF-CHECK ERROR: {exc}")

    # ── 2. THE AND-LATCH IS GONE: exit_watch stores its own toggle ───────
    _app = (ROOT / "app.py").read_text(encoding="utf-8")
    latch = re.search(r"_cs\[\"exit_watch\"\]\s*=\s*bool\(watch_on and", _app)
    check2 = latch is None and 'bool(exit_watch)' in _app
    say("")
    say("LATCH CHECK — the exit_watch AND-latch")
    say(f"  exit_watch key stores its own toggle's state, never the AND: "
        f"{'OK' if check2 else 'VIOLATION ' + (latch.group(0) if latch else '')}")
    self_ok &= check2

    # ── 3. SEED/KEY ALIGNMENT: every toggle seeds its widget's real key ──
    SEEDS = [
        ("watch_toggle", "watch_on"),
        ("exit_watch_toggle", "exit_watch"),
        ("goal_toggle", "goal_on"),
        ("voice_toggle", "voice_on"),
    ]
    seed_ok = True
    say("")
    say("SEED ALIGNMENT — widget keys seed from their own file key")
    for widget_key, file_key in SEEDS:
        # whitespace-tolerant: a seed may wrap across lines
        pat = (rf'st\.session_state\["{widget_key}"\]\s*=\s*'
               rf'_cs\.get\(\s*"{file_key}"')
        ok = re.search(pat, _app) is not None
        stale = f'{widget_key.replace("_toggle", "")}_ui"' in _app
        seed_ok &= ok and not stale
        say(f"  {widget_key:<20} <- {file_key:<14} "
            f"{'OK' if ok and not stale else 'VIOLATION'}")
    self_ok &= seed_ok

    # ── 4. HERMETIC GATE WORLDS: the bridge reader + fail-open ──────────
    gate_ok = True
    say("")
    say("HERMETIC WORLDS — signal_bridge.exit_watch_enabled()")
    try:
        sys.path.insert(0, str(ROOT))
        import signal_bridge as _sb
        sbx = Path(tempfile.mkdtemp()) / "console_state.json"
        _real_db = _sb.DB_PATH
        _sb.DB_PATH = sbx
        try:
            # world A: explicitly on
            sbx.write_text(json.dumps({"exit_watch": True}), encoding="utf-8")
            a = _sb.exit_watch_enabled(max_age_s=0)
            say(f"  exit_watch:true  -> gate {'ON' if a else 'OFF'}  "
                f"{'OK' if a else 'VIOLATION'}")
            gate_ok &= (a is True)
            # world B: explicitly off — the ONLY thing allowed to turn it off
            sbx.write_text(json.dumps({"exit_watch": False}), encoding="utf-8")
            b = _sb.exit_watch_enabled(max_age_s=0)
            say(f"  exit_watch:false -> gate {'ON' if b else 'OFF'}  "
                f"{'OK' if not b else 'VIOLATION'}")
            gate_ok &= (b is False)
            # world C: fail-open — torn file leaves the watch ON
            sbx.write_text("{torn", encoding="utf-8")
            c = _sb.exit_watch_enabled(max_age_s=0)
            say(f"  torn file        -> gate {'ON (fail-open)' if c else 'OFF'}  "
                f"{'OK' if c else 'VIOLATION'}")
            gate_ok &= (c is True)
            # world D: the bridge reader names ONLY its own key
            _bsrc = (ROOT / "signal_bridge.py").read_text(encoding="utf-8")
            m = re.search(r"def exit_watch_enabled.*?return val", _bsrc, re.S)
            body = m.group(0) if m else ""
            d = bool(body) and "watch_on" not in body
            say(f"  reader names only 'exit_watch' (never watch_on): "
                f"{'OK' if d else 'VIOLATION'}")
            gate_ok &= d
        finally:
            _sb.DB_PATH = _real_db
    except Exception as exc:
        gate_ok = False
        say(f"  HERMETIC ERROR: {exc}")
    self_ok &= gate_ok

    # ── THE CONTRACT TABLE ───────────────────────────────────────────────
    say("")
    say("THE DIAL CONTRACTS (console_state.json — one file, two processes)")
    say(f"  {'DIAL':<18}{'WRITTEN BY':<34}{'READ BY':<42}")
    say("  " + "-" * 94)
    for key, wby, rby, shapes, never in CONTRACTS:
        say(f"  {key:<18}{wby:<34}{rby:<42}")
        say(f"      shapes: {shapes}")
        say(f"      NEVER : {never}")
    say("")
    say("RULE OF THE FILE: a console dial may quiet its own channel; it can")
    say("never silence a trading call, and no toggle ever writes another")
    say("toggle's key. The bridge's gate answers to exit_watch alone.")

    verdict = "ALL GREEN" if self_ok else "FINDINGS PRESENT"
    say("")
    say(verdict)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if self_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
