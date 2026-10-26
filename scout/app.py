"""
scout/app.py - KRAKEN SCOUT (the standalone Scout app, monero_project/scout/).

One-file Scout host: local launcher + console + alert engine + Pro gating, plus the
eleven requested UX surfaces rendered in one place so each downloaded Scout instance
calibrates locally, generates its own alerts, and operates independently.

Sits under scout/ so install.ps1's payload\scout\app.py contract resolves. The app's own
alert/paywall/upsell tier runs entirely locally and never requires the live pool server;
remote features (NFTY push, jackpot notifications, profile sync, server watchdog) stay
quiet until Scout Pro is unlocked.

DO NOT contradict the live Scout Pro contract already committed in pool/static/:
  - Pro entitlement is scout_pro === "true" (pool/static/push_client.js unified router)
  - Informational surfaces (Miner Bot, Jackpot, contest helper) are silent until Pro
  - Only BUY_NOW / GET_OUT_NOW route; everything else is informational and no-ops
  - Miner Bot opt-in default is ON, and nothing mines when the user opts out
  - One active Scout per identity per jackpot cycle is a pool-side rule (pool/scout_endpoints.py)

Local-first contract (this file's own): a fresh clone calibrates without the server,
generates BUY_NOW / GET_OUT_NOW local alerts once Pro is unlocked, and the eleven UX
surfaces (unlock flow, onboarding pop-up, Pro-features-locked UI, calibration screen,
installer onboarding text, Pro marketing copy, in-app upsell banner, miner opt-in dialog
with firewall/antivirus warning, independent local alert engine, Pro-only remote channel
note, and full integration with the restored Scout app) all render here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# ONE BRAIN LAYOUT
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_scout_dir = _HERE.parent
_monero_root = _scout_dir.parent

STATE_DIR = Path(os.environ.get("SCOUT_STATE_DIR", str(_monero_root)))
if not STATE_DIR.is_absolute():
    STATE_DIR = _monero_root
STATE_DIR.mkdir(parents=True, exist_ok=True)

APP_STATE = STATE_DIR / "scout_app.json"

FRESH_STATE: dict = {
    "first_launch": True,
    "miner_opt_in": True,
    "miner_opt_in_at": None,
    "pro_unlocked": False,
    "pro_until": 0,
    "nfty_enabled": False,
    "profile_sync_enabled": False,
    "watchdog_routing_enabled": False,
    "calibrated": False,
    "calibration": {"steps": [], "current": None, "progress": 0},
    "last_local_alert": None,
    "launches": 0,
}

def load_state() -> dict:
    try:
        d = json.loads(APP_STATE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "first_launch" in d:
            return d
    except (OSError, ValueError):
        pass
    return dict(FRESH_STATE)

def save_state(s: dict) -> None:
    APP_STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")

state: dict = load_state()

ALERT_TYPES = ("BUY_NOW", "GET_OUT_NOW")

def normalize_alert_type(value):
    raw = str(value or "").strip().upper()
    if raw in ("BUY_NOW", "SIGNAL_BUY", "BUY"):
        return "BUY_NOW"
    if raw in ("GET_OUT_NOW", "SIGNAL_EXIT", "SELL", "EXIT", "SELL_NOW"):
        return "GET_OUT_NOW"
    return None

def scout_pro_unlocked() -> bool:
    return state.get("pro_unlocked") is True and state.get("pro_until", 0) > time.time()

def route_alert(type_value, payload=None):
    alert_type = normalize_alert_type(type_value)
    if alert_type is None:
        return {"alerted": False, "reason": "informational surface - not an alert"}
    if not scout_pro_unlocked():
        return {"alerted": False, "reason": "scout_pro not unlocked"}
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[ALERT][{ts}] {alert_type}", payload or "")
    state["last_local_alert"] = {"type": alert_type, "payload": payload, "ts": ts}
    save_state(state)
    return {"alerted": True, "type": alert_type, "ts": ts}

def local_alert_buy(payload=None):
    return route_alert("BUY_NOW", payload)

def local_alert_exit(payload=None):
    return route_alert("GET_OUT_NOW", payload)

class LocalWatchdog:
    def __init__(self) -> None:
        self.initialized = False
        self.last_check = None
        self.last_warning = None
        self.checks = 0

    def initialize(self) -> dict:
        self.initialized = True
        self.last_check = datetime.now(timezone.utc).isoformat()
        return {"initialized": True, "at": self.last_check}

    def check(self) -> dict:
        self.checks += 1
        self.last_check = datetime.now(timezone.utc).isoformat()
        warnings = []
        try:
            _ = APP_STATE.read_text(encoding="utf-8")
        except OSError:
            warnings.append("app state file unreadable")
        if not warnings:
            self.last_warning = None
            return {"ok": True, "checks": self.checks, "at": self.last_check}
        self.last_warning = warnings
        for w in warnings:
            route_alert("GET_OUT_NOW", {"watchdog": w})
        return {"ok": False, "warnings": warnings, "at": self.last_check}

    def tick(self) -> dict:
        """Lightweight per-tick watchdog probe.

        Used by UI surfaces that want a cheap live status check without
        re-running the full check() path.
        """
        self.checks += 1
        self.last_check = datetime.now(timezone.utc).isoformat()
        return {
            "ok": self.last_warning is None,
            "checks": self.checks,
            "at": self.last_check,
            "warning": self.last_warning,
        }

watchdog = LocalWatchdog()

MINER_DEFAULT_ON = True

def miner_opt_in_default() -> bool:
    return MINER_DEFAULT_ON

def miner_binary_present(target_dir=None):
    if target_dir is None:
        target_dir = installed_target_dir()
    return (target_dir / "xmrig" / "xmrig-6.26.0" / "xmrig.exe").exists()

def installed_target_dir() -> Path:
    cand = os.environ.get("SCOUT_TARGET_DIR", "")
    if cand:
        p = Path(cand)
        if p.is_dir():
            return p
    return _monero_root



# ---------------------------------------------------------------------------
# CALIBRATION SCREEN (the one missing UX piece)
# ---------------------------------------------------------------------------
CAL_STEPS = [
    "system checks",
    "watchdog initialization",
    "miner verification",
    "alert engine prep",
    "configuration sync",
]

def run_calibration(prog=None, set_status=None):
    steps = []
    errors = []
    for idx, name in enumerate(CAL_STEPS, start=1):
        if prog is not None:
            prog.set(name)
        if set_status is not None:
            set_status(f"[{idx}/{len(CAL_STEPS)}] {name}")
        if name == "system checks":
            ok = True
            detail = "python ok; filesystem ok"
            try:
                _ = sys.version
            except Exception as exc:
                ok = False
                detail = str(exc)
            try:
                _ = Path.cwd()
            except Exception as exc:
                ok = False
                detail = str(exc)
        elif name == "watchdog initialization":
            wd = watchdog.initialize()
            ok = wd["initialized"]
            detail = wd.get("at", "")
        elif name == "miner verification":
            ok = True
            have = miner_binary_present()
            detail = "xmrig present" if have else "xmrig not present (install later)"
        elif name == "alert engine prep":
            prep_ok = True
            detail_parts = []
            locked = route_alert("BUY_NOW", {"probe": "fresh clone"})
            if locked.get("alerted"):
                prep_ok = False
                detail_parts.append("Pro-locked alert fired when it should not")
            else:
                detail_parts.append("Pro-locked probe stayed silent (correct)")
            saved = state.get("pro_unlocked")
            state["pro_unlocked"] = True
            state["pro_until"] = int(time.time()) + 86400
            save_state(state)
            try:
                fired = local_alert_buy({"probe": "engine prep"})
                if not fired.get("alerted"):
                    prep_ok = False
                    detail_parts.append("Pro-unlocked alert did not fire")
                else:
                    detail_parts.append("Pro-unlocked alert routed (correct)")
            finally:
                state["pro_unlocked"] = saved
                state["pro_until"] = 0
                save_state(state)
            ok = prep_ok
            detail = "; ".join(detail_parts)
        elif name == "configuration sync":
            try:
                save_state(state)
                ok = True
                detail = "app state persisted"
            except Exception as exc:
                ok = False
                detail = str(exc)
                errors.append("configuration sync")
        else:
            ok = False
            detail = "unknown step"
        steps.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            errors.append(name)
    calibrated = len(errors) == 0
    state["calibrated"] = calibrated
    state["calibration"] = {"steps": steps, "current": None, "progress": 100}
    save_state(state)
    return {"steps": steps, "calibrated": calibrated, "errors": errors}

# ---------------------------------------------------------------------------
# ONBOARDING POP-UP (first launch) + MINER OPT-IN DIALOG WITH FIREWALL/AV WARNING
# ---------------------------------------------------------------------------
class OnboardingDialog:
    """First-launch onboarding + miner opt-in dialog.

    Default: miner enabled (matches installer_ui.py checkbox default). The user
    unchecks to opt out. A firewall/antivirus warning is shown. The choice is
    persisted and respected by start.bat and the Python launcher.
    """

    FIREWALL_WARNING_TEXT = (
        "Antivirus / firewall note: the Miner Bot uses xmrig, an open-source CPU miner. "
        "Some security tools flag CPU miners as 'riskware' because the same binary shape can "
        "be abused by malware. xmrig ships from the official xmrig release and is hash-verified "
        "by the installer before extraction. If your security tool blocks it, add the Scout install "
        "folder as an exclusion rather than disabling protection."
    )

    def __init__(self, root_obj):
        self.root_obj = root_obj
        root_obj.title("Kraken Scout - First Launch")
        root_obj.resizable(False, False)
        pad = {"padx": 16, "pady": 8}

        tk.Label(
            root_obj,
            text="Welcome to Kraken Scout",
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        tk.Label(
            root_obj,
            text=(
                "Scout runs locally on this device and generates its own alerts - "
                "watchdog warnings, local Buy Now / Get Out Now signals, and pop-ups - "
                "without requiring the server. Remote alerts, NFTY messages, jackpot "
                "notifications, profile sync, and the server watchdog require Scout Pro."
            ),
            justify="left",
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", **pad)

        var_miner = tk.BooleanVar(value=miner_opt_in_default())

        tk.Checkbutton(
            root_obj,
            variable=var_miner,
            text="Enable the Miner Bot (default on)",
        ).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        tk.Label(
            root_obj,
            text=self.FIREWALL_WARNING_TEXT,
            fg="#7a3b00",
            justify="left",
            wraplength=520,
        ).grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        tk.Label(
            root_obj,
            text="Learn about Scout Pro: xmrjackpot.com/scout-pro",
            fg="blue",
        ).grid(row=4, column=0, columnspan=2, sticky="w", **pad)

        out = tk.Text(root_obj, height=6, width=70, state="disabled", bg="#f6f6f6", relief="flat")
        out.grid(row=5, column=0, columnspan=2, sticky="we", **pad)

        def say(msg):
            out.configure(state="normal")
            out.delete("1.0", "end")
            out.insert("1.0", msg)
            out.configure(state="disabled")

        def begin():
            state["first_launch"] = False
            state["miner_opt_in"] = bool(var_miner.get())
            state["miner_opt_in_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            say("Setup recorded. Starting Scout...")
            root_obj.after(600, root_obj.destroy)

        import tkinter.ttk as ttk

        ttk.Button(root_obj, text="Begin Installation", command=begin).grid(
            row=6, column=0, sticky="w", **pad
        )
        ttk.Button(
            root_obj,
            text="Learn about Scout Pro ->",
            command=lambda: say("Scout Pro info: xmrjackpot.com/scout-pro"),
        ).grid(row=6, column=1, sticky="e", **pad)

        out.grid_configure(**pad)

        root_obj.protocol("WM_DELETE_WINDOW", lambda: begin())

    @classmethod
    def show_first_launch(cls):
        if not state.get("first_launch"):
            return True
        root_obj = tk.Tk()
        root_obj.attributes("-topmost", True)
        root_obj.geometry("560x420")
        cls(root_obj).mainloop()
        return state.get("first_launch") is False

# ---------------------------------------------------------------------------
# PRO-UNLOCK FLOW
# ---------------------------------------------------------------------------
class ProUnlockScreen:
    """Present a clear unlock screen explaining that remote alerts, NFTY messages,
    jackpot notifications, profile sync, and server watchdog require Scout Pro.
    Include an Unlock Scout Pro button and a confirmation message once activated.
    """

    BENEFITS = [
        "Remote alerts - get Scout signals on your phone, not just locally",
        "NFTY push messages - native push delivered through your device",
        "Jackpot notifications - win notices pushed to you",
        "Profile sync - preferences follow you across devices",
        "Server watchdog - cloud-enhanced monitoring routed to you",
    ]

    def __init__(self, root_obj, *, as_popup=False):
        self.root_obj = root_obj
        frame = tk.Frame(root_obj)
        frame.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(frame, text="Scout Pro", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            frame,
            text=(
                "Remote alerts, NFTY messages, jackpot notifications, profile sync, "
                "and the server watchdog require Scout Pro."
            ),
            justify="left",
            wraplength=560,
        ).pack(fill="x", anchor="w", pady=(4, 10))

        tk.Label(
            frame,
            text="With Scout Pro you get:",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        for b in self.BENEFITS:
            tk.Label(frame, text="-  " + b, justify="left", wraplength=560).pack(anchor="w")

        self.status = tk.Label(frame, text="", fg="green")
        self.status.pack(anchor="w", pady=(10, 4))

        # This button flips a LOCAL flag; it does not buy or grant an
        # entitlement. Say so on the button instead of calling it an
        # unlock (Oct 24 full-disclosure pass).
        unlock_btn = tk.Button(frame, text="Enable Pro locally (preview)",
                               command=self._unlock)
        unlock_btn.pack(anchor="w", pady=(8, 4))

        if as_popup:
            root_obj.title("Scout Pro - local preview")

    def _unlock(self):
        state["pro_unlocked"] = True
        state["pro_until"] = int(time.time()) + 30 * 86400
        save_state(state)
        self.status.configure(
            text=(
                "Local preview enabled on THIS device for 30 days - nothing "
                "was charged and no account was upgraded. Remote alerts, "
                "NFTY messages and jackpot notifications only reach you if "
                "Scout Pro is unlocked on your xmrjackpot.com account; the "
                "server checks that entitlement on every signal."
            )
        )

# ---------------------------------------------------------------------------
# PRO FEATURES LOCKED UI
# ---------------------------------------------------------------------------
class ProLockedScreen:
    """Display when a user tries to access Pro-only features without unlocking Pro.
    List locked features and provide Unlock Scout Pro and Continue buttons.
    """

    LOCKED_FEATURES = [
        "Remote alerts",
        "NFTY push messages",
        "Jackpot notifications",
        "Profile sync",
        "Server watchdog",
    ]

    def __init__(self, root_obj):
        self.root_obj = root_obj
        tk.Label(
            root_obj,
            text="Scout Pro Required",
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=(8, 6))
        tk.Label(
            root_obj,
            text=(
                "The feature you tried to use is a Scout Pro feature. "
                "Remote alerts, NFTY messages, jackpot notifications, profile sync, "
                "and the server watchdog all require Scout Pro."
            ),
            justify="left",
            wraplength=520,
        ).pack(pady=(0, 10))

        tk.Label(
            root_obj,
            text="Locked features:",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        for f in self.LOCKED_FEATURES:
            tk.Label(root_obj, text="-  " + f).pack(anchor="w")

        btn_frame = tk.Frame(root_obj)
        btn_frame.pack(pady=(12, 4), fill="x")
        tk.Button(
            btn_frame,
            text="Enable Pro locally (preview)",
            command=self._unlock,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(
            btn_frame,
            text="Continue",
            command=self.root_obj.destroy,
        ).pack(side="left", fill="x", expand=True)

    def _unlock(self):
        # LOCAL FLAG ONLY. The pool's server decides entitlement; this
        # switch exists so the console can be exercised without a purchase.
        state["pro_unlocked"] = True
        state["pro_until"] = int(time.time()) + 30 * 86400
        save_state(state)
        self.root_obj.destroy()

# ---------------------------------------------------------------------------
# IN-APP PRO UPSELL BANNER
# ---------------------------------------------------------------------------
class UpsellBanner:
    """Display inside the Scout UI for free users. Explain Scout is running locally
    and that Pro unlocks remote alerts and NFTY push messages. Include an Unlock
    Scout Pro button. Rendered as a Tk banner so the console host can show it.
    """

    def __init__(self, root_obj):
        self.root_obj = root_obj
        frame = tk.Frame(root_obj, bd=1, relief="sunken")
        frame.pack(fill="x", padx=8, pady=6)
        tk.Label(
            frame,
            text=(
                "Scout is running locally. Remote alerts and NFTY push "
                "messages are Scout Pro features, and the pool's server "
                "decides that entitlement - unlock it on your "
                "xmrjackpot.com account. The button here only enables a "
                "local preview on this device."
            ),
            justify="left",
            wraplength=760,
        ).pack(side="left", fill="x", expand=True, padx=8)
        tk.Button(
            frame,
            text="Enable Pro locally (preview)",
            command=self._unlock,
        ).pack(side="right", padx=8)
        self.frame = frame

    def _unlock(self):
        state["pro_unlocked"] = True
        state["pro_until"] = int(time.time()) + 30 * 86400
        save_state(state)
        self.frame.destroy()

# ---------------------------------------------------------------------------
# MARKETING COPY (Scout Pro marketing page copy)
# ---------------------------------------------------------------------------
# Provided here as the canonical Pro copy so the app, the installer, and any
# hosted surface stay worded the same way. Reuses the live project's Pro benefit
# vocabulary (remote alerts, NFTY messages, jackpot notifications, profile sync,
# server watchdog) without inventing new claims.
SCOUT_PRO_MARKETING = {
    "headline": (
        "Scout Pro - remote alerts, NFTY messages, jackpot notifications, "
        "profile sync, and server watchdog."
    ),
    "body": (
        "Scout runs locally and generates its own alerts. Scout Pro adds the cloud-enhanced layer "
        "on top: remote alerts that reach your device, NFTY push messages, jackpot notifications, "
        "profile sync across your devices, and a server watchdog that routes warnings to you. "
        "Everything local still works without Pro - Pro is what makes the remote features live."
    ),
    "call_to_action": "Unlock Scout Pro",
    "price_note": (
        "Scout Pro is a one-time unlock (0.01 XMR for 30 days in this build; "
        "the live checkout is at xmrjackpot.com/scout-pro)."
    ),
}

# ---------------------------------------------------------------------------
# INSTALLER ONBOARDING TEXT
# ---------------------------------------------------------------------------
# Introduce Scout installation, explain independent operation, clarify that remote
# alerts require Scout Pro. Include a Begin Installation button and a link to Scout Pro info.
INSTALLER_ONBOARDING = {
    "title": "Installing Kraken Scout",
    "intro": (
        "Kraken Scout installs and runs independently on this device - once installed, each "
        "downloaded Scout instance calibrates locally, generates its own alerts, and operates "
        "on its own. Remote alerts, NFTY messages, jackpot notifications, profile sync, and the "
        "server watchdog require Scout Pro."
    ),
    "begin_button": "Begin Installation",
    "pro_link": "Learn about Scout Pro",
    "pro_ref": "xmrjackpot.com/scout-pro",
}

# ---------------------------------------------------------------------------
# PRO-ONLY REMOTE ALERT CHANNEL NOTE
# ---------------------------------------------------------------------------
# Remote alerts, NFTY messages, jackpot notifications, and server pop-ups only
# activate after Scout Pro is unlocked. This surface makes that contract explicit
# inside the app (it is also enforced by the local router above and by the live
# push_client.js unified router).
REMOTE_CHANNEL_NOTE = (
    "Remote channel: remote alerts, NFTY messages, jackpot notifications, and the server "
    "watchdog are Pro-only. They stay quiet until Scout Pro is unlocked. Until then, Scout "
    "still generates its own local alerts and watchdog warnings without the server."
)

# ---------------------------------------------------------------------------
# MAIN CONSOLE HOST
# ---------------------------------------------------------------------------
def _console_banner(root_obj):
    tk.Label(
        root_obj,
        text="Kraken Scout - local console",
        font=("Segoe UI", 13, "bold"),
    ).pack(pady=(6, 2))
    tk.Label(
        root_obj,
        text=(
            "Scout runs locally and generates its own alerts. Remote alerts, NFTY messages, "
            "jackpot notifications, profile sync, and the server watchdog require Scout Pro."
        ),
        justify="left",
        wraplength=640,
    ).pack(pady=(0, 8))

def console_mode(*, calibrate=False, probe_alert=None, headless=False):
    """Run the Scout app as a console host.

    - First launch: show the onboarding dialog (Tk) before anything else.
    - Calibration: run the calibration screen (headless prints, or via the dialog).
    - Pro surfaces: --unlock, --locked, --upsell spawn the relevant Tk screen.
    - Alert probe: --probe <type> exercises the local alert engine and prints the verdict.
    """
    if state.get("first_launch") and not headless:
        root_obj = tk.Tk()
        root_obj.geometry("560x460")
        OnboardingDialog(root_obj)
        root_obj.mainloop()

    if not state.get("calibrated") or calibrate:
        if headless:
            rep = run_calibration()
            print("== CALIBRATION ==")
            for s in rep["steps"]:
                mark = "OK" if s["ok"] else "FAIL"
                print(f"  [{mark}] {s['name']}: {s.get('detail', '')!r}")
            print(f"calibrated: {rep['calibrated']!r}")
        else:
            root_obj = tk.Tk()
            root_obj.geometry("560x360")
            prog = tk.StringVar(value="starting...")
            status = tk.StringVar(value="calibrating...")
            tk.Label(root_obj, textvariable=prog, wraplength=520, justify="left").pack(
                pady=(6, 4)
            )
            tk.Label(root_obj, textvariable=status, fg="gray").pack()
            root_obj.after(
                50,
                lambda: (
                    run_calibration(prog=prog, set_status=lambda s: status.set(s)),
                    root_obj.after(100, root_obj.destroy),
                ),
            )
            root_obj.mainloop()

    if probe_alert is not None:
        verdict = route_alert(probe_alert, {"probe": "console"})
        print("== LOCAL ALERT PROBE ==")
        print("type   :", probe_alert)
        print("pro_unlocked:", scout_pro_unlocked())
        print("verdict:", json.dumps(verdict, ensure_ascii=False, indent=2))
        return (
            0
            if verdict.get("alerted") or normalize_alert_type(probe_alert) is None
            else 1
        )

    root_obj = tk.Tk()
    root_obj.geometry("760x420")
    _console_banner(root_obj)
    if not state.get("pro_unlocked") or state.get("pro_until", 0) <= time.time():
        UpsellBanner(root_obj)
        tk.Label(
            root_obj, text=REMOTE_CHANNEL_NOTE, fg="gray", justify="left", wraplength=720
        ).pack()
    else:
        tk.Label(root_obj, text="Scout Pro is active.", fg="green").pack()
    tk.Button(
        root_obj,
        text="Enable Pro locally (preview)",
        command=lambda: ProUnlockScreen(root_obj, as_popup=True),
    ).pack(pady=(6, 0))
    tk.Button(
        root_obj,
        text="Test Local Alert (Buy Now)",
        command=lambda: _probe_buy(root_obj),
    ).pack()
    tk.Button(
        root_obj,
        text="Test Local Alert (Get Out Now)",
        command=lambda: _probe_exit(root_obj),
    ).pack()
    tk.Button(
        root_obj,
        text="Watchdog Check",
        command=lambda: _watchdog_check(root_obj),
    ).pack()
    tk.Button(root_obj, text="Quit", command=root_obj.destroy).pack(pady=(8, 0))
    root_obj.mainloop()
    return 0


def _probe_buy(root_obj):
    v = route_alert("BUY_NOW", {"source": "console button"})
    tk.Label(root_obj, text=f"verdict: {json.dumps(v)}", justify="left", wraplength=560).pack()


def _probe_exit(root_obj):
    v = route_alert("GET_OUT_NOW", {"source": "console button"})
    tk.Label(root_obj, text=f"verdict: {json.dumps(v)}", justify="left", wraplength=560).pack()


def _watchdog_check(root_obj):
    r = watchdog.check()
    tk.Label(root_obj, text=f"watchdog: {json.dumps(r)}", justify="left", wraplength=560).pack()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Kraken Scout - standalone Scout app")
    ap.add_argument("--calibrate", action="store_true", help="run the calibration screen")
    ap.add_argument(
        "--probe",
        metavar="TYPE",
        help="probe a local alert type (BUY_NOW / GET_OUT_NOW)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="no Tk dialogs; print-only",
    )
    ap.add_argument(
        "--unlock",
        action="store_true",
        help="show the Pro unlock screen",
    )
    ap.add_argument(
        "--locked",
        action="store_true",
        help="show the Pro-features-locked screen",
    )
    ap.add_argument(
        "--upsell",
        action="store_true",
        help="show the in-app Pro upsell banner",
    )
    a = ap.parse_args()

    if a.probe is not None:
        verdict = route_alert(a.probe, {"probe": "cli"})
        print("== LOCAL ALERT PROBE ==")
        print("type   :", a.probe)
        print("pro_unlocked:", scout_pro_unlocked())
        print("verdict:", json.dumps(verdict, ensure_ascii=False, indent=2))
        return (
            0
            if verdict.get("alerted") or normalize_alert_type(a.probe) is None
            else 1
        )

    if a.headless:
        if not state.get("calibrated") or a.calibrate:
            rep = run_calibration()
            print("== CALIBRATION ==")
            for s in rep["steps"]:
                mark = "OK" if s["ok"] else "FAIL"
                print(f"  [{mark}] {s['name']}: {s.get('detail', '')!r}")
            print(f"calibrated: {rep['calibrated']!r}")
        return 0

    if a.unlock:
        root_obj = tk.Tk()
        root_obj.geometry("560x360")
        ProUnlockScreen(root_obj, as_popup=True)
        root_obj.mainloop()
        return 0
    if a.locked:
        root_obj = tk.Tk()
        root_obj.geometry("560x360")
        ProLockedScreen(root_obj)
        root_obj.mainloop()
        return 0
    if a.upsell:
        root_obj = tk.Tk()
        root_obj.geometry("760x120")
        UpsellBanner(root_obj)
        root_obj.mainloop()
        return 0

    return console_mode(headless=False)


if __name__ == "__main__":
    raise SystemExit(main())
