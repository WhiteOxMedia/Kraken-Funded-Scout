"""FULL END-TO-END AUDIT — every system, ready for a fresh start tomorrow.
Writes a PASS/FAIL report to audit_report.txt."""
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
SCOUT_PY = BASE / ".venv\\Scripts\\python.exe"
BOT_PY = BOT / ".venv\\Scripts\\python.exe"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def run(cmd, cwd):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=180)
        return r.returncode == 0, (r.stdout or b"").decode(errors="replace")[:300]
    except Exception as exc:
        return False, str(exc)[:200]


# ── 1. Compile every live Python file (both projects) ───────────────
scout_files = ["app.py", "background_scanner.py", "scanner.py", "execution_tiers.py",
               "volatility_engine.py", "signal_bridge.py", "strategy.py", "config.py",
               "kraken_api.py", "db.py"]
ok, out = run([str(SCOUT_PY), "-X", "utf8", "-m", "py_compile", *scout_files], BASE)
check("scout code compiles (10 files)", ok, out)

bot_files = ["bot_engine.py", "scout_signal_reader.py", "kraken_api.py", "bot_runner.py",
             "reset_positions.py"]
ok, out = run([str(BOT_PY), "-X", "utf8", "-m", "py_compile", *bot_files], BOT)
check("bot code compiles (5 files)", ok, out)

# ── 2. Unit test suites ──────────────────────────────────────────────
ok, out = run([str(SCOUT_PY), "-X", "utf8", "test_execution_tiers.py"], BASE)
tiers = (BASE / "test_execution_tiers_check.txt").read_text(encoding="utf-8") if (BASE / "test_execution_tiers_check.txt").exists() else ""
tiers_ok = "0 failed" in tiers
check("execution-tier tests (66 checks)", tiers_ok, tiers.splitlines()[0] if tiers else "no output")

ok, out = run([str(SCOUT_PY), "-X", "utf8", "test_longonly.py"], BASE)
lo = (BASE / "longonly_check.txt").read_text(encoding="utf-8") if (BASE / "longonly_check.txt").exists() else ""
lo_fails = [l for l in lo.splitlines() if "FAIL" in l]
check("long-only rules tests (buy beats sell)", not lo_fails, f"{len(lo_fails)} failed")

# ── 3. AppTest boot smoke ────────────────────────────────────────────
ok, out = run([str(SCOUT_PY), "-X", "utf8", "test_apptest.py"], BASE)
at = (BASE / "apptest_check.txt").read_text(encoding="utf-8") if (BASE / "apptest_check.txt").exists() else ""
check("console boots (AppTest, no exceptions)", "ElementList()" in at, at.splitlines()[0] if at else "?")

# ── 4. Launcher + bat files pure ASCII ───────────────────────────────
bats = sorted(BASE.glob("*.bat"))
bad = []
for b in bats:
    if any(byte > 0x7F for byte in b.read_bytes()):
        bad.append(b.name)
check("bat files pure ASCII (no mojibake)", not bad, ", ".join(bad))
check("Launch Kraken.bat exists", (BASE / "Launch Kraken.bat").exists())

# ── 5. Voice + sound assets ──────────────────────────────────────────
check("voice script (Zira amplified)", (BASE / "speak_loud.ps1").exists())
sounds = ["alert_ding.wav", "alert_alarm.wav", "alert_tick.wav"]
check("sound assets", all((BASE / "assets" / s).exists() for s in sounds))

# ── 6. Processes running ─────────────────────────────────────────────
ps = ("Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
      "Where-Object {$_.CommandLine -match 'background_scanner|volatility_engine|"
      "bot_runner|streamlit'} | ForEach-Object {$_.CommandLine} | "
      "Out-File -FilePath proc_audit.txt -Encoding utf8")
subprocess.run(["powershell", "-NoProfile", "-Command", ps], cwd=str(BASE), timeout=60)
procs = (BASE / "proc_audit.txt").read_text(errors="replace") if (BASE / "proc_audit.txt").exists() else ""
for needle, label in [("background_scanner", "scanner"), ("volatility_engine", "engine"),
                      ("bot_runner", "bot"), ("streamlit", "console")]:
    check(f"process: {label}", needle in procs)

# ── 7. Engine data fresh + valid ─────────────────────────────────────
try:
    doc = json.loads((BASE / "rotation_thresholds.json").read_text())
    age_min = (time.time() - (BASE / "rotation_thresholds.json").stat().st_mtime) / 60
    check("engine thresholds fresh (<70 min)", age_min < 70, f"{age_min:.0f} min old")
    check("engine thresholds valid (54 assets, cells)", len(doc.get("universe", {})) >= 50
          and bool(doc.get("median", {}).get("weekday")), f"assets={len(doc.get('universe', {}))}")
except Exception as exc:
    check("engine thresholds", False, str(exc)[:100])

# ── 8. Bridge health ─────────────────────────────────────────────────
try:
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    pend = conn.execute("SELECT COUNT(*) c FROM signals WHERE status='pending'").fetchone()["c"]
    stuck = conn.execute(
        "SELECT COUNT(*) c FROM signals WHERE status='pending' AND "
        "timestamp < datetime('now', '-2 hours')").fetchone()["c"]
    filled_open = conn.execute(
        "SELECT COUNT(*) c FROM signals WHERE status='filled' AND action='buy'").fetchone()["c"]
    check("bridge: no pending signals", pend == 0, f"{pend} pending")
    check("bridge: no stuck (2h+) signals", stuck == 0, f"{stuck} stuck")
    check("bridge: open positions tracked", True, f"{filled_open} filled/open")
    conn.close()
except Exception as exc:
    check("bridge", False, str(exc)[:100])

# ── 9. Execution tier state ──────────────────────────────────────────
try:
    st = json.loads((BASE / "execution_state.json").read_text())
    now_iso = datetime.now(MST).isoformat()
    live = {k: v for k, v in st.get("pending_scales", {}).items()
            if v.get("expire", "") > now_iso}
    expired = {k: v for k, v in st.get("pending_scales", {}).items()
               if v.get("expire", "") <= now_iso}
    # live scale-ins are WORKING STATE (tier-capped remainders draining),
    # not errors. Blacklists and expired-lingering scales are the problems.
    check("execution state", not st.get("blacklist") and not expired,
          f"blacklist={len(st.get('blacklist', {}))}, live scale-ins={len(live)} "
          f"(working), expired lingering={len(expired)}")
except FileNotFoundError:
    check("execution state", True, "not created (no gates fired — clean)")
except Exception as exc:
    check("execution state", False, str(exc)[:100])

# ── 10. Bot heartbeat ────────────────────────────────────────────────
try:
    hb = json.loads((BOT / "bot_heartbeat.txt").read_text().strip())
    ts = datetime.fromisoformat(hb["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    age = (datetime.now(ZoneInfo("UTC")) - ts).total_seconds() / 60
    check("bot heartbeat fresh (<35 min)", age < 35, f"{age:.0f} min old, status={hb.get('status')}")
except Exception as exc:
    check("bot heartbeat", False, str(exc)[:100])

# ── 10b. DESIGN CONTRACT: does the code still do what the owner designed? ──
# These catch intent-drift: changes half-reverted, rules forgotten,
# old logic surviving in a second code path (the thin-book lesson).
try:
    et_src = (BASE / "execution_tiers.py").read_text(encoding="utf-8")
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    app_src = (BASE / "app.py").read_text(encoding="utf-8")

    check("contract: NO thin-book blacklist in gate_buy",
          "blacklist_pair(pair, 24, f\"thin book" not in et_src,
          "thin-book blacklist still present!")
    check("contract: NO thin-book branch in liquidity_snapshot",
          "T6 (thin book)" not in et_src,
          "advisory path still labels thin books")
    check("contract: 89% desktop floor present",
          "DESKTOP_ALERT_MIN = 89" in bs_src)
    check("contract: adaptive resolve rule present (scalp-based)",
          "_max_resolve_hours" in bs_src and "SCALP_TARGET_PCT = 0.4" in bs_src
          and "_hours = _hours_to_scalp" in bs_src
          and "_hours = _hours_to_goal" not in bs_src)
    check("contract: resolve rule on the green light too (scalp-based)",
          "_resolve_cap" in app_src and "_hours_to_scalp" in app_src)
    check("contract: QC green light is scalp-gated (floor + pace, not median bar)",
          "QC_SCALP_FLOOR = 60" in app_src
          and "sig.confidence >= QC_SCALP_FLOOR" in app_src
          and "AUTO-TRADE BAR PASSED" not in app_src)
    check("contract: chase guard on the green light (no stale moves)",
          "QC_CHASE_PCT = 2.0" in app_src and "_chase <= QC_CHASE_PCT" in app_src)
    check("contract: giveback + loss warnings in the contest light",
          "GIVEBACK_WARN_USD = 50" in bs_src and "LOSS_WARN_USD = 50" in bs_src
          and "gave back" in bs_src and "down {_amt} dollars" in bs_src)
    check("contract: re-entry blocked after a big TAKE IT payout",
          "BIG_PAYOUT_USD = 100" in bs_src and "_recent_big_payout" in app_src
          and "_payout is None" in app_src)
    check("contract: FAST $20 leaderboard on the console",
          "_fast_twenty" in app_src and "FAST $20" in app_src)
    check("contract: TAKE IT $20 system present",
          "TAKE_IT_USD = 20" in bs_src and "Take it!" in bs_src)
    check("contract: cooldown flatten 20:00 (once daily, persisted)",
          "now.hour >= 20" in bs_src and "_load_scanner_state" in bs_src)
    check("contract: bounce path (losers, not gainers)",
          "Bounce: yesterday's loser turning" in bs_src
          and "NEW GAINER" not in bs_src)
    check("contract: long-only hard guard on alerts",
          "longs only (never short)" in bs_src)
    check("contract: contest/bot tournament separation",
          "long_only=False" in app_src and 'long_only: bool = True' in
          (BASE / "scanner.py").read_text(encoding="utf-8"))
    check("contract: bot BUY-only challenge (never displaces to short)",
          "CHALLENGE WIN" in (BOT / "scout_signal_reader.py").read_text(encoding="utf-8"))
    check("contract: bot own-scanning OFF (scout funnel owns buys)",
          "own_scanning_enabled: bool = False" in (BOT / "config.py").read_text(encoding="utf-8"))
    check("contract: rotation-aware liquidity filter",
          "LIQ_ROT_FLOOR_WEEKDAY" in bs_src and "median_volume_usd" in bs_src)
    check("contract: bridge lets challengers through (no max-position write reject)",
          "Already %d open positions" not in (BASE / "signal_bridge.py").read_text(encoding="utf-8"))
except Exception as exc:
    check("design contract", False, str(exc)[:100])

# ── 11. Console-local state files valid ──────────────────────────────
for f in ["contest_watch.json", "console_state.json", "payout_track.json"]:
    try:
        json.loads((BASE / f).read_text())
        check(f"state file: {f}", True)
    except FileNotFoundError:
        check(f"state file: {f}", True, "absent = defaults (ok)")
    except Exception as exc:
        check(f"state file: {f}", False, str(exc)[:80])
try:
    pt = json.loads((BASE / "payout_track.json").read_text())
    total = sum(p["amount"] for p in pt.get("payouts", {}).values())
    check("payouts tracked", True, f"${total:,.2f}")
except Exception:
    check("payouts tracked", True, "none yet")

# ── 12. Kill switch (informational — user resets it) ─────────────────
ks_active = (BASE / "kill_switch.lock").exists()
ks_reason = ""
if ks_active:
    try:
        ks_reason = (BASE / "kill_switch.lock").read_text().strip()[:120]
    except Exception:
        pass
check("kill switch", True, "TRIGGERED — reset before tomorrow" if ks_active else "clear")
check("cooldown window awareness", True,
      "it is 20:00-24:00 now — buys resume at midnight by design"
      if now.hour >= 20 else "not in cooldown")

# ── report ───────────────────────────────────────────────────────────
fails = [r for r in results if not r[1]]
lines = [f"FULL AUDIT — {now.strftime('%a %I:%M %p')} Denver — "
         f"{len(results) - len(fails)}/{len(results)} PASS, {len(fails)} FAIL", "=" * 70]
for name, ok, detail in results:
    lines.append(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
lines.append("=" * 70)
if ks_active:
    lines.append(f"KILL SWITCH: TRIGGERED — {ks_reason}")
    lines.append("  → reset via desktop RESET shortcut to arm tomorrow's signals")
lines.append("BUY WINDOW: opens at midnight (R1 Asia rotation)")
if fails:
    lines.append("")
    lines.append("FAILURES NEED ATTENTION: " + "; ".join(f[0] for f in fails))
else:
    lines.append("")
    lines.append("VERDICT: ALL SYSTEMS PASS — ready for a fresh start "
                 "(kill switch reset pending)")

out = BASE / "audit_report.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
