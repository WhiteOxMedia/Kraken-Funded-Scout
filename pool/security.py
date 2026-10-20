"""
Security module — rate limiting, replay protection, nonce store.
Pure Python, no external dependencies. In-memory (resets on restart).
"""
import time
import secrets
import threading


class RateLimiter:
    """Token bucket rate limiter — per-key, thread-safe."""

    def __init__(self, max_requests, window_seconds):
        self.max = max_requests
        self.window = window_seconds
        self.buckets = {}  # key -> list of timestamps
        self.lock = threading.Lock()

    def check(self, key):
        """Returns True if request is allowed, False if rate-limited."""
        now = time.time()
        cutoff = now - self.window

        with self.lock:
            timestamps = self.buckets.get(key, [])

            # Prune expired entries
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= self.max:
                self.buckets[key] = timestamps
                return False

            timestamps.append(now)
            self.buckets[key] = timestamps

            # Periodic cleanup of stale keys
            if len(self.buckets) > 10000:
                stale = [k for k, v in self.buckets.items() if all(t <= cutoff for t in v)]
                for k in stale:
                    del self.buckets[k]

            return True


# ─── Rate limiter instances ─────────────────────────────────
# Public stats: 30 req/min per IP
stats_limiter = RateLimiter(max_requests=30, window_seconds=60)
# Auth endpoints: 20 req/min per IP (login/register)
auth_limiter = RateLimiter(max_requests=20, window_seconds=60)
# account CREATION is its own abuse surface (Sep 26 E2E):
# 5 registrations / hour / IP
register_limiter = RateLimiter(max_requests=5, window_seconds=3600)
# the miner ingest gate (Sep 27): 12 signed share-delta calls per
# minute per source is far above any real poller cadence and far
# below forgery-flood territory
shares_limiter = RateLimiter(max_requests=12, window_seconds=60)


class NonceStore:
    """Prevent replay attacks — one-time-use nonces for sensitive operations."""

    def __init__(self, ttl_seconds=300):
        self.seen = {}  # nonce -> expiry timestamp
        self.ttl = ttl_seconds
        self.lock = threading.Lock()

    def generate(self):
        """Generate a new nonce to give to the client."""
        return secrets.token_hex(16)

    def consume(self, nonce):
        """Return True if nonce is valid and unused, False if already used/expired."""
        now = time.time()
        with self.lock:
            # Clean expired
            expired = [n for n, exp in self.seen.items() if now > exp]
            for n in expired:
                del self.seen[n]

            if nonce in self.seen:
                return False

            self.seen[nonce] = now + self.ttl
            return True


# Global nonce store for drawing triggers
draw_nonce_store = NonceStore(ttl_seconds=300)


def require_auth(request):
    """Require authentication, return user or raise 401.
    
    Imported by scout_endpoints.py. Delegates to server.py's
    get_user_from_request when available; otherwise raises 401.
    """
    try:
        from .server import get_user_from_request
    except ImportError:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# Avoid circular import: HTTPException imported here
from fastapi import HTTPException
