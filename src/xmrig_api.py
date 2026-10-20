"""
XMRig HTTP API client for monitoring mining statistics.
"""
import json
import urllib.request


XMRIG_API_URL = "http://127.0.0.1:18000"


def _get(endpoint):
    """Send a GET request to the xmrig API."""
    url = f"{XMRIG_API_URL}{endpoint}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_summary():
    """Get overall mining summary."""
    return _get("/2/summary")


def get_threads():
    """Get per-thread mining stats."""
    return _get("/2/threads")


def is_running():
    """Check if xmrig API is responding."""
    return _get("/2/summary") is not None


def format_hashrate(h):
    """Format hashrate in human-readable units."""
    if h is None or h == 0:
        return "0 H/s"
    if h >= 1_000_000:
        val = h / 1_000_000
        return f"{val:.2f} MH/s"
    elif h >= 1_000:
        val = h / 1_000
        return f"{val:.2f} kH/s"
    else:
        return f"{h:.1f} H/s"
