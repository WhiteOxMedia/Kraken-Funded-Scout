"""
Proxy Poller — connects to xmrig-proxy's HTTP API and tracks
share submissions per miner, feeding data into the jackpot engine.

xmrig-proxy must be configured with HTTP API enabled on port 9090.
Each miner connects with: wallet_address.worker_name as their username.
"""
import os
import sys
import time
import json
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import database as db
import jackpot_engine as engine
try:                                    # real MinerYield for the funding
    import miner_yield                  # formula (fail-soft — the poller's
except Exception:                       # ticket/shares duties never depend
    miner_yield = None                  # on it)

PROXY_API = "http://127.0.0.1:9090"
PROXY_TOKEN = "xmrjackpot2026"
POLL_INTERVAL = 10  # seconds


def fetch_proxy_stats():
    """Fetch worker stats from xmrig-proxy API."""
    try:
        req = urllib.request.Request(
            f"{PROXY_API}/2/summary",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Authorization": f"Bearer {PROXY_TOKEN}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ProxyPoller] Error fetching proxy stats: {e}")
        return None


def parse_workers(summary):
    """
    Extract per-worker stats from xmrig-proxy summary.
    xmrig-proxy reports workers in the 'workers' array.
    Each worker has: id, hashrate, shares_good, shares_total, etc.
    """
    workers = []
    raw_workers = summary.get("workers", [])

    for w in raw_workers:
        # Worker ID in xmrig-proxy is the username: wallet_address.worker_name
        worker_id = w.get("id", "")
        if not worker_id:
            continue

        parts = worker_id.split(".", 1)
        wallet_address = parts[0]
        worker_name = parts[1] if len(parts) > 1 else ""

        workers.append({
            "wallet_address": wallet_address,
            "worker_name": worker_name,
            "hashrate": w.get("hashrate", 0),
            "shares_valid": w.get("shares_good", 0),
            "shares_invalid": w.get("shares_total", 0) - w.get("shares_good", 0),
        })

    return workers


def update_workers(workers):
    """Update miner stats in database and allocate free tickets."""
    for w in workers:
        # Ensure miner exists
        miner = db.get_or_create_miner(w["wallet_address"], w["worker_name"])

        # Calculate delta shares since last update
        prev_shares = miner["shares_valid"]
        delta_valid = max(0, w["shares_valid"] - prev_shares)
        delta_invalid = max(0, w["shares_invalid"] - miner["shares_invalid"])

        if delta_valid > 0 or delta_invalid > 0:
            # Update stats
            db.update_miner_stats(
                w["wallet_address"],
                delta_valid,
                delta_invalid,
                w["hashrate"],
            )

            # Allocate free tickets based on new shares
            if delta_valid > 0:
                new_tickets = engine.allocate_free_tickets(miner["id"], delta_valid)
                if new_tickets > 0:
                    print(f"[ProxyPoller] {w['wallet_address'][:16]}... earned {new_tickets} free ticket(s)")

    # The charter's MinerYield: feed the real per-worker hashrate to
    # the funding observer (fail-soft — a yield problem must never
    # break share tracking or ticket allocation)
    if miner_yield is not None:
        try:
            miner_yield.note_workers(workers)
            miner_yield.flush_daily()
        except Exception as e:
            print(f"[ProxyPoller] miner-yield note failed: {e}")

    # Check if a drawing is scheduled
    result = engine.check_drawing_schedule()
    if result.get("status") == "drawn":
        print(f"[ProxyPoller] 🎰 DRAWING #{result['drawing_id']}!")
        print(f"  Winner: {result.get('winner', 'unknown')}")
        print(f"  Jackpot: {result.get('jackpot_amount', 0):.6f} XMR")
        print(f"  House tax: {result.get('house_tax', 0):.6f} XMR")
        print(f"  Payout: {result.get('payout', 0):.6f} XMR")


def run_poller():
    """Main poller loop."""
    print("[ProxyPoller] Starting — polling xmrig-proxy at", PROXY_API)
    print(f"[ProxyPoller] Poll interval: {POLL_INTERVAL}s")
    print("[ProxyPoller] Press Ctrl+C to stop\n")

    while True:
        try:
            summary = fetch_proxy_stats()
            if summary:
                workers = parse_workers(summary)
                if workers:
                    print(f"[ProxyPoller] {len(workers)} worker(s) active")
                    update_workers(workers)
                else:
                    print(f"[ProxyPoller] No workers connected")
            else:
                print(f"[ProxyPoller] Proxy not responding at {PROXY_API}")

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[ProxyPoller] Stopped.")
            break
        except Exception as e:
            print(f"[ProxyPoller] Error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_poller()
