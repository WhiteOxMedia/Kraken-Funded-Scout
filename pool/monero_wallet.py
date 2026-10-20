"""
Monero Wallet RPC Client
Communicates with monero-wallet-rpc via HTTP JSON-RPC.
Handles: subaddress generation, transfer scanning, balance checks.
"""
import json
import urllib.request
import time
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────
# These are loaded from environment or set manually
MONERO_RPC_URL = "http://127.0.0.1:18082/json_rpc"
WALLET_NAME = "jackpot_pool"
REQUIRED_CONFIRMATIONS = 10  # blocks before crediting payment
MONERO_BLOCK_TIME = 120     # ~2 min per block


def _rpc(method, params=None):
    """Send a JSON-RPC request to monero-wallet-rpc."""
    payload = {
        "jsonrpc": "2.0",
        "id": "0",
        "method": method,
        "params": params or {},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(MONERO_RPC_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if "error" in result and result["error"]:
                return {"error": result["error"].get("message", str(result["error"]))}
            return result.get("result", {})
    except Exception as e:
        return {"error": str(e)}


def rpc_available():
    """Check if monero-wallet-rpc is available."""
    result = _rpc("get_height")
    return "error" not in result


def get_balance():
    """Get the wallet's total and unlocked balance."""
    result = _rpc("get_balance")
    if "error" in result:
        return None
    return {
        "balance": result.get("balance", 0) / 1e12,
        "unlocked_balance": result.get("unlocked_balance", 0) / 1e12,
    }


def create_subaddress(label=""):
    """Create a new subaddress for a user."""
    params = {"account_index": 0, "label": label}
    result = _rpc("create_address", params)
    if "error" in result:
        return None, None
    return result.get("address"), result.get("address_index", 0)


def get_transfers(min_height=None, in_only=True, subaddr_indices=None):
    """
    Scan for incoming transfers since min_height.
    Returns list of {txid, amount, height, confirmations, subaddr_index, timestamp}

    Pass subaddr_indices to let the wallet filter server-side; checkout
    polling uses it so each poll only walks the one order's subaddress
    instead of the whole wallet history.
    """
    params = {
        "in": in_only,
        "out": False,
        "pending": False,
        "failed": False,
        "pool": False,
    }
    if min_height is not None:
        params["min_height"] = min_height
    if subaddr_indices:
        params["subaddr_indices"] = [int(i) for i in subaddr_indices]

    result = _rpc("get_transfers", params)
    if "error" in result:
        return None

    transfers = []
    current_height = _rpc("get_height").get("height", 0)
    in_items = result.get("in", [])

    for tx in in_items:
        transfers.append({
            "txid": tx.get("txid"),
            "amount": tx.get("amount", 0) / 1e12,
            "height": tx.get("height", 0),
            "confirmations": current_height - tx.get("height", 0) if tx.get("height", 0) > 0 else 0,
            "subaddr_index": tx.get("subaddr_index", {}).get("minor", -1),
            "timestamp": tx.get("timestamp", 0),
            "payment_id": tx.get("payment_id", ""),
        })

    return transfers


def get_transfer_by_txid(txid):
    """Get a specific transfer by txid."""
    params = {"txid": txid}
    result = _rpc("get_transfer_by_txid", params)
    if "error" in result:
        return None

    transfer = result.get("transfer", {})
    current_height = _rpc("get_height").get("height", 0)

    return {
        "txid": transfer.get("txid"),
        "amount": transfer.get("amount", 0) / 1e12,
        "height": transfer.get("height", 0),
        "confirmations": current_height - transfer.get("height", 0) if transfer.get("height", 0) > 0 else 0,
        "subaddr_index": transfer.get("subaddr_index", {}).get("minor", -1),
        "timestamp": transfer.get("timestamp", 0),
        "confirmed": transfer.get("height", 0) > 0 and
                     (current_height - transfer.get("height", 0)) >= REQUIRED_CONFIRMATIONS,
    }


def get_height():
    """Get current blockchain height."""
    result = _rpc("get_height")
    if "error" in result:
        return 0
    return result.get("height", 0)


def scan_new_payments(known_subaddrs, min_height=0):
    """
    Scan for new incoming payments to specific subaddresses.
    Returns payments matching known subaddresses that meet confirmation depth.
    """
    transfers = get_transfers(min_height=min_height)
    if transfers is None:
        return []

    results = []
    for tx in transfers:
        sub_idx = tx["subaddr_index"]
        if sub_idx in known_subaddrs:
            results.append({
                "txid": tx["txid"],
                "subaddr_index": sub_idx,
                "amount": tx["amount"],
                "height": tx["height"],
                "confirmations": tx["confirmations"],
                "confirmed": tx["confirmations"] >= REQUIRED_CONFIRMATIONS,
                "user": known_subaddrs[sub_idx],  # maps subaddr → user_id
                "timestamp": tx["timestamp"],
            })

    return results
