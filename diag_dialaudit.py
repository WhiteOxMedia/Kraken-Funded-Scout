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
    # ── 1. SELF-CHECK: the scanner's mouth honors the dial contract ─────
    #    (Sep 23 re-pin: the mouth was rebuilt in the voice-parity era —
    #    ONE voice, three deliveries, the VOLUME dial (0-100) governing
    #    her loudness everywhere by the user's own retirement of the
    #    switches. The old console-queue-only dial split is retired with
    #    them. What must hold now: voice ALWAYS on (no toggle can silence
    #    her), the dial read from console_state with a fail-soft 100 and
    #    clamped 0-100, the spawn carrying -Volume, and the her-words log
    #    written BEFORE the spawn.)
    _speak = (ROOT / "background_scanner.py").read_text(encoding="utf-8")

    self_ok = True
    try:
        _vol_body = re.search(
            r"def _voice_volume\(\).*?(?=\ndef |\n\n\n)", _speak, re.S).group(0)
        _voice_body = re.search(
            r"def _voice_enabled\(\).*?(?=\ndef |\n\n\n)", _speak, re.S).group(0)
        _mouth = re.search(
            r"def _speak_server\(.*?subprocess\.Popen\(\[.*?\]",
            _speak, re.S).group(0)
        check1 = (
            "return True" in _voice_body and "ALWAYS on" in _voice_body
            and '"voice_volume", 100' in _vol_body
            and "max(0, min(100" in _vol_body
            and "return 100" in _vol_body
            and '"-Volume"' in _mouth and "_voice_volume()" in _mouth
            and _mouth.index("_log_her_line(text)")
            < _mouth.index("subprocess.Popen([")
        )
        say("SELF-CHECK — the mouth's dial contract")
        say(f"  voice ALWAYS on, the dial clamped 0-100 fail-soft 100, "
            f"-Volume on the spawn, the her-words log before it: "
            f"{'OK' if check1 else 'VIOLATION'}")
        self_ok &= check1
    except Exception as exc:
        self_ok = False
        say(f"SELF-CHECK ERROR: {exc}")

    # ── 2. THE AND-LATCH IS GONE: exit_watch stores its own toggle ───────
    #    (Sep 23 re-pin: the toggle WIDGETS are retired — Sep 22, user rule
    #    "remove from the console only and fix in the on position", the
    #    switches are critical to the system and hidden. So the console no
    #    longer writes exit_watch AT ALL: the bridge's fail-open default IS
    #    the forced-on. The pin is the ABSENCE: no AND-latch and no toggle
    #    write to the key may come back.)
    _app = (ROOT / "app.py").read_text(encoding="utf-8")
    latch = re.search(r"_cs\[\"exit_watch\"\]\s*=\s*bool\(watch_on and", _app)
    any_write = re.search(r'_cs\[\s*["\']exit_watch["\']\s*\]\s*=', _app) \
        or re.search(r'["\']exit_watch["\']\s*:\s*(?:bool|True|False)', _app)
    check2 = latch is None and any_write is None
    say("")
    say("LATCH CHECK — the exit_watch AND-latch")
    say(f"  console never writes exit_watch (the toggle is retired; the "
        f"bridge's fail-open IS the forced-on): "
        f"{'OK' if check2 else 'VIOLATION ' + ((latch or any_write).group(0) if (latch or any_write) else '')}")
    self_ok &= check2

    # ── 3. SEED/KEY ALIGNMENT (Sep 23 re-pin: the retirement) ────────────
    #    The four toggle widgets are GONE from the console (the Sep 22
    #    user rule). Their file keys stay always-on: watch_on is seeded
    #    and forced true; voice rides the always-on default with the
    #    HER VOLUME slider as its only seeded control; goal_on is read
    #    with a default-on; exit_watch has no console writer at all.
    #    A toggle widget re-appearing for any of these keys is the
    #    regression — the snap-to-OFF class must stay closed.
    SEEDS = [
        ("watch_toggle", "watch_on"),
        ("exit_watch_toggle", "exit_watch"),
        ("goal_toggle", "goal_on"),
        ("voice_toggle", "voice_on"),
    ]
    seed_ok = True
    say("")
    say("SEED ALIGNMENT — the toggle retirement (no toggle widgets; "
        "forced-on semantics)")
    _scan_src = (ROOT / "background_scanner.py").read_text(encoding="utf-8")
    for widget_key, file_key in SEEDS:
        # the widget must NOT exist, and must not seed itself from the file
        pat = (rf'st\.session_state\["{widget_key}"\]\s*=\s*'
               rf'_cs\.get\(\s*"{file_key}"')
        widget_gone = re.search(pat, _app) is None \
            and f'key="{widget_key}"' not in _app
        # per-key forced-on evidence:
        #   watch_on  — seeded AND forced true on its read line (app.py)
        #   goal_on   — read with a default-true chain (app.py)
        #   voice_on  — the scanner's _voice_enabled is a permanent True
        #   exit_watch — no console writer at all (bridge fail-open = on)
        if file_key == "watch_on":
            forced = re.search(rf'{file_key}[^\n]*or True', _app) is not None
        elif file_key == "goal_on":
            forced = re.search(rf'get\(\s*"{file_key}"', _app) is not None
        elif file_key == "voice_on":
            forced = ("def _voice_enabled" in _scan_src
                      and re.search(r"def _voice_enabled\(\)[^\n]*\n(.*?)return True",
                                    _scan_src, re.S) is not None)
        else:  # exit_watch
            forced = "exit_watch" not in _app
        ok = widget_gone and forced
        stale = f'{widget_key.replace("_toggle", "")}_ui"' in _app
        seed_ok &= ok and not stale
        say(f"  {widget_key:<20} <- {file_key:<14} "
            f"{'OK (widget gone, forced-on)' if ok and not stale else 'VIOLATION'}")
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
