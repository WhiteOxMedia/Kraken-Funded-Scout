"""installer_ui.py — THE CHECKBOX (Sep 24).

One window, one choice, exactly as the protocol orders it:

  [x] Install the Miner Bot (recommended)   <- DEFAULT CHECKED
      wallet address (OPTIONAL): __________
      worker name (optional):    __________

  [ Install Scout ]  [ Remove Miner Bot ]

Checkbox ON + wallet -> installer.opt_in: configure, auto-register with the
                 jackpot, grant the first raffle ticket, bind the wallet.
Checkbox ON, no wallet -> INSTALL FIRST (Oct 26): installer.install_app
                 writes the neutral config, records the install and
                 hands off to Scout, which calibrates on start. Only the
                 pool binding is deferred; the empty field is no longer
                 an error and never blocks the app from running.
Checkbox OFF -> nothing is installed or registered; the finish text
                 points at xmrjackpot.com for manual registration.

Every successful path starts Scout (`--calibrate`), fail-soft: a spawn
failure is a sentence in the output pane, never an undo of the install.

The UI displays no secrets (the wallet is typed by the user, never
generated or stored beyond the opt-in record) and changes nothing until
Install is pressed. A "Remove Miner Bot" button calls opt_out() for the
change-of-heart path. Falls back to printed instructions when tkinter
is unavailable (headless installs use installer.py's CLI flags).

Run:  .venv/Scripts/python.exe installer/installer_ui.py
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer


def _run_tk() -> int:
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("Kraken Scout Setup")
    root.resizable(False, False)
    pad = {"padx": 14, "pady": 6}

    tk.Label(root, text="Kraken Scout + Community Jackpot",
             font=("Segoe UI", 13, "bold")).grid(row=0, column=0,
                                                 columnspan=2,
                                                 sticky="w", **pad)
    tk.Label(root, text="The Miner Bot mines Monero in the background and "
                        "sends the work to the public pool. Accepted shares "
                        "earn raffle entries, which appear in your "
                        "xmrjackpot.com dashboard."
             ).grid(row=1, column=0, columnspan=2, sticky="w", **pad)

    var_optin = tk.BooleanVar(value=True)          # CHECKBOX ON (default)
    ttk.Checkbutton(
        root, variable=var_optin,
        text="Install the Miner Bot and register this wallet with the pool "
             "(recommended)",
    ).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

    tk.Label(root, text="Your Monero wallet address (optional — add it "
                        "later to start earning)").grid(
        row=3, column=0, sticky="w", **pad)
    ent_wallet = ttk.Entry(root, width=64)
    ent_wallet.grid(row=4, column=0, columnspan=2, sticky="we", **pad)

    tk.Label(root, text="Worker name (optional)").grid(
        row=5, column=0, sticky="w", **pad)
    ent_worker = ttk.Entry(root, width=32)
    ent_worker.grid(row=6, column=0, columnspan=2, sticky="we", **pad)

    out = tk.Text(root, height=6, width=74, state="disabled",
                  bg="#f6f6f6", relief="flat")

    def _say(msg):
        out.configure(state="normal")
        out.delete("1.0", "end")
        out.insert("1.0", msg)
        out.configure(state="disabled")

    def _install():
        if not var_optin.get():
            installer.opt_out()
            _say("Miner Bot not installed — you are NOT registered.\n\n"
                 + installer.GETTING_STARTED)
            return
        wallet = ent_wallet.get().strip()
        if not wallet:
            # INSTALL FIRST (Oct 26): a blank field is a choice, not an
            # error. Scout, the config and the calibration are local; only
            # earning needs an address, and that stays pending.
            r = _install_app_via_api(ent_worker.get().strip())
            if not r.get("ok"):
                _say(r.get("error", "install failed"))
                return
            _say("Scout installed — calibration is starting.\n"
                 "  Miner Bot: NOT started (no wallet bound yet)\n"
                 "  xmrig config: "
                 + ("ready" if r.get("xmrig_config") else "could not be written") + "\n"
                 + r.get("next_step", "") + "\n\n"
                 + ("" if _launch_scout() else
                    "Open Kraken Scout yourself to run the calibration.\n\n")
                 + r.get("phrase", ""))
            return
        r = _install_via_api(wallet, ent_worker.get().strip())
        if r.get("ok"):
            _say("Miner Bot installed — this wallet is registered with the "
                 "pool.\n"
                 f"  miner_id: {r.get('miner_id')}\n"
                 f"  stratum: {r.get('stratum')}\n"
                 f"  xmrig config written: {r.get('xmrig_patched')}\n\n"
                 + r.get("entry_note", "") + "\n\n"
                 + r["phrase"])
            _launch_scout()          # calibration begins now, not later
        elif r.get("friendly"):
            # a down server is guidance, not a crash: the line lands in
            # the output pane (no scary popup — nothing the user did
            # was wrong)
            _say(r.get("error", "install failed"))
        else:
            messagebox.showerror("Install failed", r.get("error", "?"))

    def _remove():
        installer.opt_out()
        _say("Miner Bot disabled (opt-out recorded).\n"
             "The miner stops mining for the jackpot when it next exits.\n\n"
             + installer.GETTING_STARTED)

    ttk.Button(root, text="Install Scout", command=_install).grid(
        row=7, column=0, sticky="w", **pad)
    ttk.Button(root, text="Remove Miner Bot", command=_remove).grid(
        row=7, column=1, sticky="w", **pad)
    out.grid(row=8, column=0, columnspan=2, sticky="we", **pad)

    root.mainloop()
    return 0


def _install_via_api(wallet: str, worker: str) -> dict:
    """THE GUARD (Sep 26, closes the KNOWN GAP): _install's opt_in call
    wrapped so a DOWN SERVER answers as guidance instead of an
    exception crashing the tk callback. Returns the result dict with
    friendly=True (the caller renders the message in the output pane —
    guidance, not an error popup: nothing the user did was wrong).
    Module-level and importable so the drive can pin it headlessly.
    Real validation errors keep their popup: those ARE the user's
    doing. Validation errors are not transport errors."""
    try:
        return installer.opt_in(wallet, worker=worker)
    except OSError:
        return {
            "ok": False, "friendly": True,
            "error": "Could not reach xmrjackpot.com — the pool server "
                     "may be down. Your miner is NOT registered yet; "
                     "press Install again in a minute.",
        }


def _install_app_via_api(worker: str = "") -> dict:
    """The wallet-free install, guarded like _install_via_api.

    A down server is guidance here too — and for this path the server is
    not even needed: install_app's beacon is fail-soft, so an offline box
    still installs and calibrates. The wrapper exists so the tk callback
    can never die on a transport error.
    """
    try:
        return installer.install_app(worker=worker)
    except OSError:
        return {
            "ok": False, "friendly": True,
            "error": "Could not finish the install on this machine — press "
                     "Install Scout again in a minute.",
        }


def _launch_scout() -> bool:
    """Start Scout so calibration begins immediately (Oct 26).

    Fail-soft by contract: an install that already landed must never be
    undone by a missing interpreter, a locked file or a policy block. The
    caller turns False into one sentence in the output pane. Detached on
    Windows so closing the setup window does not kill the calibration.
    """
    app = installer.PROJECT_DIR / "scout" / "app.py"
    if not app.is_file():
        return False
    try:
        kwargs = {"cwd": str(app.parent)}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008   # DETACHED_PROCESS
        subprocess.Popen([sys.executable, str(app), "--calibrate"], **kwargs)
        return True
    except Exception:                 # noqa: BLE001 — never fail the install
        return False


def main() -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("tkinter unavailable (headless install) — use the CLI:\n"
              "  installer.py --opt-in                  # install, wallet later\n"
              "  installer.py --opt-in --wallet <addr>  # install + register\n"
              "  installer.py --opt-out\n"
              "  installer.py --status")
        return 1
    return _run_tk()


if __name__ == "__main__":
    sys.exit(main())
