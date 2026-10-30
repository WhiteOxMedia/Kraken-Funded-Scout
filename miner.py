"""
Monero CPU Miner Manager
========================
Manages xmrig mining process, monitors hashrate/earnings,
and integrates with Kraken for deposit tracking.

Usage:
    python miner.py              # Start mining + monitoring
    python miner.py --status     # Check current mining status
    python miner.py --kraken     # Show Kraken deposit info
    python miner.py --benchmark  # Run xmrig benchmark
"""
import os
import sys
import time
import json
import signal
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "pool"))

import xmrig_api
import kraken_client

# THE XMRIG PIN, one source for every Python chain: pool/operator_config
# (the PowerShell installer keeps the same literals under installer/
# install.ps1, bound by test_operator_config_contract.py).
import operator_config as _oc
_XMRIG_PIN = _oc.xmrig_pin()
XMRIG_VERSION = _XMRIG_PIN["version"]


# ─── Configuration ────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
XMRIG_DIR = PROJECT_DIR / "xmrig" / XMRIG_VERSION
XMRIG_EXE = XMRIG_DIR / "xmrig.exe"
XMRIG_CONFIG = XMRIG_DIR / "config.json"
# THE MINING ADDRESS COMES FROM THE DEPLOYMENT (Oct 25): it is an
# operator identifier, not a source constant. Unset means the miner has
# nothing to mine for and says so. THE ONE READER of that env key is
# pool/operator_config.miner_wallet(); this file reads nothing directly.
WALLET_ADDRESS = _oc.miner_wallet()
MINING_POOLS = [
    ("SupportXMR", "pool.supportxmr.com:3333"),
    ("MoneroOcean", "gulf.moneroocean.stream:10128"),
    ("HashVault", "pool.hashvault.pro:3333"),
]
STATS_INTERVAL = 15  # seconds between stats prints
KRAKEN_CHECK_INTERVAL = 600  # seconds between Kraken checks (10 min)

# ANSI colors
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"


def print_banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════════╗
║          MONERO CPU MINER — XMR → Kraken Pipeline           ║
╠══════════════════════════════════════════════════════════════╣{C.RESET}
""", end="")


def print_config():
    print(f"{C.CYAN}║  Miner:      xmrig {XMRIG_VERSION} (RandomX)                       ║{C.RESET}")
    print(f"{C.CYAN}║  Coin:       Monero (XMR)                                    ║{C.RESET}")
    print(f"{C.CYAN}║  Wallet:     {WALLET_ADDRESS[:16]}...{WALLET_ADDRESS[-8:]}  ║{C.RESET}")
    print(f"{C.CYAN}║  Pools:      {', '.join(p[0] for p in MINING_POOLS):<43s}║{C.RESET}")
    print(f"{C.CYAN}║  API:        http://127.0.0.1:18000                         ║{C.RESET}")
    print(f"{C.CYAN}╚══════════════════════════════════════════════════════════════╝{C.RESET}")
    print()


def start_xmrig(background=True):
    """Start the xmrig mining process."""
    if not XMRIG_EXE.exists():
        print(f"{C.RED}[ERROR] xmrig.exe not found at {XMRIG_EXE}{C.RESET}")
        print(f"  Run the setup script to download xmrig first.")
        return None

    # Check if xmrig is already running
    if xmrig_api.is_running():
        print(f"{C.YELLOW}[INFO] xmrig is already running.{C.RESET}")
        return None

    print(f"{C.CYAN}[STARTING] Launching xmrig...{C.RESET}")

    cmd = [str(XMRIG_EXE), "--config=" + str(XMRIG_CONFIG)]

    if background:
        # Start as background process, capture output to log
        log_file = PROJECT_DIR / "logs" / "xmrig.log"
        log_file.parent.mkdir(exist_ok=True)
        log_fh = open(log_file, "w")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(XMRIG_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        print(f"{C.GREEN}[OK] xmrig started (PID: {proc.pid}){C.RESET}")
        print(f"  Log: {log_file}")
        return proc
    else:
        # Run in foreground
        proc = subprocess.Popen(cmd, cwd=str(XMRIG_DIR))
        return proc


def stop_xmrig(proc=None):
    """Stop the xmrig mining process."""
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"{C.YELLOW}[STOP] xmrig terminated.{C.RESET}")


def monitor_mining(proc, kraken_enabled):
    """Main monitoring loop — prints stats and checks Kraken periodically."""
    print(f"\n{C.BOLD}─" * 62 + f"{C.RESET}")
    print(f"{C.BOLD}  MONITORING — press Ctrl+C to stop{C.RESET}")
    print(f"{C.BOLD}─" * 62 + f"{C.RESET}\n")

    # Wait for xmrig to start up
    print(f"{C.DIM}  Waiting for xmrig API to come online...{C.RESET}")
    for _ in range(30):
        if xmrig_api.is_running():
            print(f"{C.GREEN}  xmrig API online!{C.RESET}\n")
            break
        time.sleep(2)
    else:
        print(f"{C.RED}  xmrig API not responding. Check logs/xmrig.log{C.RESET}")
        return

    start_time = time.time()
    last_kraken_check = 0

    while True:
        try:
            summary = xmrig_api.get_summary()

            if not summary:
                print(f"{C.RED}  [!] Lost connection to xmrig API{C.RESET}")
                time.sleep(5)
                continue

            elapsed = time.time() - start_time
            elapsed_str = str(timedelta(seconds=int(elapsed)))

            # Extract stats
            hashrate = summary.get("hashrate", {})
            total_h = hashrate.get("total", [0])[0] if hashrate.get("total") else 0
            highest_h = hashrate.get("highest", 0) if hashrate.get("highest") else 0

            results = summary.get("results", {})
            shares_good = results.get("shares_good", 0)
            shares_total = results.get("shares_total", 0)
            rejected = results.get("shares_result", {}).get("rejected", 0)
            diff_current = results.get("diff_current", 0)

            connection = summary.get("connection", {})
            pool = connection.get("pool", "unknown")
            ping = connection.get("ping", 0)
            uptime = connection.get("uptime", 0)

            # Estimated earnings (rough: hashrate * block_reward / network_hashrate)
            # block_reward ~ 0.6 XMR, network_hashrate ~ 2-3 GH/s
            # This is a rough estimate
            if total_h > 0:
                network_hashrate = 2_500_000_000  # ~2.5 GH/s, approx
                block_reward = 0.6  # XMR per block
                blocks_per_day = 720  # 2 min blocks
                est_daily_xmr = (total_h / network_hashrate) * block_reward * blocks_per_day
                est_daily_usd = est_daily_xmr * 320  # rough XMR price
            else:
                est_daily_xmr = 0
                est_daily_usd = 0

            # Print stats
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\r{C.DIM}[{ts}]{C.RESET} ", end="")
            print(f"{C.GREEN}{xmrig_api.format_hashrate(total_h):>12}{C.RESET}", end="")
            print(f" {C.DIM}|{C.RESET} ", end="")
            print(f"shares: {C.CYAN}{shares_good}{C.RESET}", end="")
            if rejected > 0:
                print(f" {C.RED}(rej:{rejected}){C.RESET}", end="")
            print(f" {C.DIM}|{C.RESET} ", end="")
            print(f"diff: {diff_current:.0f}", end="")
            print(f" {C.DIM}|{C.RESET} ", end="")
            print(f"pool: {C.YELLOW}{pool.split(':')[0] if ':' in pool else pool}{C.RESET}", end="")
            print(f" {C.DIM}|{C.RESET} ", end="")
            print(f"~{est_daily_xmr:.6f} XMR/day", end="")
            print(f" {C.DIM}|{C.RESET} ", end="")
            print(f"uptime: {elapsed_str}")

            # Periodic Kraken check
            if kraken_enabled and (time.time() - last_kraken_check > KRAKEN_CHECK_INTERVAL):
                last_kraken_check = time.time()
                check_kraken_deposits()

            time.sleep(STATS_INTERVAL)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"\n{C.RED}  [Monitor error] {e}{C.RESET}")
            time.sleep(10)


def check_kraken_deposits():
    """Check Kraken for recent XMR deposits."""
    print(f"\n  {C.MAGENTA}[Kraken] Checking deposit status...{C.RESET}")
    deposits = kraken_client.get_deposit_status("XMR")
    if not deposits:
        print(f"  {C.DIM}[Kraken] No recent deposits found.{C.RESET}")
        return

    for d in deposits:
        status = d.get("status", "unknown")
        amount = d.get("amount", "?")
        txid = d.get("txid", "?")
        ts = d.get("time", "?")
        marker = C.GREEN if status == "Success" else C.YELLOW
        print(f"  {marker}[Kraken] {status}: {amount} XMR (txid: {txid[:16]}...){C.RESET}")


def show_kraken_info():
    """Display Kraken deposit address and balance info."""
    print(f"\n{C.BOLD}  Kraken Integration{C.RESET}")
    print(f"  {'─' * 50}")

    if not kraken_client.check_credentials():
        return

    # Get deposit address
    print(f"\n  {C.CYAN}Fetching XMR deposit address...{C.RESET}")
    addr, expire = kraken_client.get_xmr_deposit_address()
    if addr:
        print(f"  {C.GREEN}XMR Deposit Address:{C.RESET}")
        print(f"  {C.BOLD}{addr}{C.RESET}")
        print(f"\n  To send mined XMR to Kraken:")
        print(f"  1. Transfer from your wallet to the address above")
        print(f"  2. Kraken requires 720 confirmations (~24 hours)")
    else:
        print(f"  {C.YELLOW}Could not retrieve deposit address.{C.RESET}")
        print(f"  Check that your Kraken account supports XMR deposits (US accounts only).")

    # Get balance
    balance = kraken_client.get_balance("XXMR")
    if balance is not None:
        print(f"\n  {C.CYAN}Kraken XMR Balance: {C.BOLD}{balance} XMR{C.RESET}")

    # Check recent deposits
    print(f"\n  {C.CYAN}Recent deposits:{C.RESET}")
    deposits = kraken_client.get_deposit_status("XMR")
    if deposits:
        for d in deposits:
            status = d.get("status", "unknown")
            amount = d.get("amount", "?")
            marker = C.GREEN if status == "Success" else C.YELLOW
            print(f"  {marker}  {status}: {amount} XMR{C.RESET}")
    else:
        print(f"  {C.DIM}  No recent deposits.{C.RESET}")
    print()


def show_status():
    """Show current mining status without starting."""
    print(f"\n{C.BOLD}  Mining Status{C.RESET}")
    print(f"  {'─' * 50}")

    if xmrig_api.is_running():
        summary = xmrig_api.get_summary()
        if summary:
            hashrate = summary.get("hashrate", {})
            total_h = hashrate.get("total", [0])[0] if hashrate.get("total") else 0
            results = summary.get("results", {})
            shares_good = results.get("shares_good", 0)
            connection = summary.get("connection", {})
            pool = connection.get("pool", "unknown")

            print(f"  {C.GREEN}● RUNNING{C.RESET}")
            print(f"  Hashrate:    {xmrig_api.format_hashrate(total_h)}")
            print(f"  Shares:      {shares_good} accepted")
            print(f"  Pool:        {pool}")
            print(f"  Wallet:      {WALLET_ADDRESS[:20]}...")
            print(f"  API:         http://127.0.0.1:18000")
        else:
            print(f"  {C.YELLOW}● API responding but no data{C.RESET}")
    else:
        print(f"  {C.RED}● NOT RUNNING{C.RESET}")
        print(f"  Run 'python miner.py' to start mining.")
    print()


def benchmark():
    """Run xmrig benchmark."""
    print(f"\n{C.CYAN}  Running RandomX benchmark (1M iterations)...{C.RESET}\n")
    cmd = [str(XMRIG_EXE), "--bench=1M"]
    proc = subprocess.Popen(cmd, cwd=str(XMRIG_DIR))
    proc.wait()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Monero CPU Miner Manager")
    parser.add_argument("--status", action="store_true", help="Show current mining status")
    parser.add_argument("--kraken", action="store_true", help="Show Kraken deposit info")
    parser.add_argument("--benchmark", action="store_true", help="Run RandomX benchmark")
    args = parser.parse_args()

    print_banner()
    print_config()

    if args.status:
        show_status()
        return

    if args.kraken:
        show_kraken_info()
        return

    if args.benchmark:
        benchmark()
        return

    # Default: start mining + monitoring
    # Check Kraken credentials
    kraken_enabled = kraken_client.check_credentials()

    # Start xmrig
    proc = start_xmrig(background=True)
    if proc is None and not xmrig_api.is_running():
        return

    # If xmrig was already running, proc is None but we still monitor
    if proc is None:
        proc = type("DummyProc", (), {"pid": "existing", "terminate": lambda self: None})()

    # Set up signal handler for clean shutdown
    def signal_handler(sig, frame):
        print(f"\n\n{C.YELLOW}[SHUTDOWN] Stopping miner...{C.RESET}")
        if hasattr(proc, 'terminate') and proc.pid != "existing":
            stop_xmrig(proc)
        print(f"{C.GREEN}[DONE] Miner stopped. Mined XMR goes to your wallet.{C.RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # If Kraken is enabled, show deposit address at startup
    if kraken_enabled:
        print(f"\n{C.MAGENTA}  Fetching Kraken XMR deposit address...{C.RESET}")
        addr, _ = kraken_client.get_xmr_deposit_address()
        if addr:
            print(f"  {C.GREEN}Kraken XMR Deposit Address:{C.RESET}")
            print(f"  {C.BOLD}  {addr}{C.RESET}")
            print(f"  {C.DIM}  Transfer mined XMR here to send to your Kraken account.{C.RESET}")

    # Start monitoring loop
    try:
        monitor_mining(proc, kraken_enabled)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()
