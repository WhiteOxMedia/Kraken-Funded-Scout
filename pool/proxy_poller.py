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


def _api_get(path):
    """One HTTP brain for the proxy API (token, timeout, errors)."""
    try:
        req = urllib.request.Request(
            f"{PROXY_API}{path}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Authorization": f"Bearer {PROXY_TOKEN}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ProxyPoller] Error fetching {path}: {e}")
        return None


def fetch_proxy_stats():
    """Fetch the proxy's aggregate summary (uptime, totals) — useful
    for logs, NOT for per-miner data."""
    return _api_get("/2/summary")


def fetch_miners():
    """The proxy's connected MINERS with identities: /1/miners rows
    are self-describing (the 'format' field names the columns) and the
    'user' column is 'wallet_address.worker_name' — the exact shape
    the pool expects."""
    return _api_get("/1/miners")


def fetch_workers():
    """The proxy's per-RIG stats: accepted/rejected/invalid share
    counters (LIFETIME, monotonic) and hashrate buckets, keyed by
    rig_id. /1/workers, NOT /2/summary — the v2 summary's 'workers'
    is a COUNT on this proxy build, so the original draft (which
    iterated it as a list) could never have parsed a single worker;
    the live smoke caught it on first contact."""
    return _api_get("/1/workers")


def parse_workers(miners_doc, workers_doc=None):
    """
    Join the proxy's two views into the poller's worker dicts.

    Identity comes from /1/miners (user = 'wallet_address.worker_name',
    columns self-described by the doc's 'format' field); share
    counters and hashrate come from /1/workers keyed by rig_id, with
    a worker-name fallback for rigs whose rig_id is the worker name.
    Counters are LIFETIME values — update_workers computes per-poll
    deltas against the DB, so monotonic totals are exactly right.
    """
    stats = {}
    for r in (workers_doc or {}).get("workers", []) or []:
        if not isinstance(r, list) or len(r) < 6:
            continue
        # observed row shape: [rig_id, ip, status, accepted, rejected,
        # invalid, hashes_total, diff, h10s, h60s, h15m, h1h, h6h]
        stats[str(r[0] or "")] = {
            "accepted": int(r[3] or 0),
            "invalid": int(r[5] or 0),
            "hashrate": (max(float(x or 0) for x in r[8:14])
                         if len(r) >= 9 else 0.0),
        }

    workers = []
    for row in (miners_doc or {}).get("miners", []) or []:
        if not isinstance(row, list):
            continue
        m = dict(zip((miners_doc or {}).get("format", []), row))
        user = str(m.get("user", "") or "")
        if not user:
            continue
        wallet, _, worker_name = user.partition(".")
        rig_stats = (stats.get(str(m.get("rig_id", "")))
                     or stats.get(worker_name) or {})
        workers.append({
            "wallet_address": wallet,
            "worker_name": worker_name,
            "hashrate": rig_stats.get("hashrate", 0.0),
            "shares_valid": rig_stats.get("accepted", 0),
            "shares_invalid": rig_stats.get("invalid", 0),
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
            miners_doc = fetch_miners()
            workers_doc = fetch_workers()
            if miners_doc is None:
                print(f"[ProxyPoller] Proxy not responding at {PROXY_API}")
            else:
                workers = parse_workers(miners_doc, workers_doc)
                if workers:
                    print(f"[ProxyPoller] {len(workers)} miner row(s) parsed")
                    update_workers(workers)
                else:
                    print(f"[ProxyPoller] No workers connected")

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[ProxyPoller] Stopped.")
            break
        except Exception as e:
            print(f"[ProxyPoller] Error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_poller()
