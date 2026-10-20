"""
signal_priority.py — growth-rate prioritization for fired signals.

Maintains rolling data per signal and ranks active signals by a weighted
priority score combining volatility and liquidity over the last N intervals.

References SPEC.md:
  - Required data per signal: signal_id, prices[], volumes[], spreads[], timestamps[]
  - Core metrics: volatility, liquidity (basic + enhanced with spreads)
  - Priority score: w_v*volatility + w_l*liquidity (w_v=0.7, w_l=0.3)
  - Ranking logic: sort descending, expose Top / Mid / Low movers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

N: int = 10
W_VOLATILITY: float = 0.7
W_LIQUIDITY: float = 0.3


# ---------------------------------------------------------------------------
# Signal data container
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    signal_id: str
    prices: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    spreads: List[float | None] = field(default_factory=list)
    timestamps: List[Any] = field(default_factory=list)

    def update(
        self,
        price: float,
        volume: float,
        spread: Optional[float] = None,
        timestamp: Any = None,
    ) -> None:
        """Append a new interval observation to the rolling arrays."""
        self.prices.append(price)
        self.volumes.append(volume)
        if spread is not None:
            self.spreads.append(spread)
        else:
            self.spreads.append(None)
        self.timestamps.append(timestamp)

    def last_n(self, arr: List, n: int) -> List:
        """Return the last n elements of arr (sliced, not a view)."""
        if not arr:
            return []
        return arr[-n:]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_volatility(signal: Signal, n: int = N) -> float:
    """|P_last - P_first| / P_first over last n prices, or 0 if insufficient."""
    prices = signal.prices
    if len(prices) < n:
        return 0.0
    p_first = prices[-n]
    p_last = prices[-1]
    if p_first == 0:
        return 0.0
    return abs(p_last - p_first) / p_first


def compute_liquidity(signal: Signal, n: int = N) -> float:
    """Basic liquidity = sum(volumes[-n:]).

    Enhanced: if spreads present and avg spread > 0, divide by avg_spread.
    """
    volumes = signal.volumes
    spreads = signal.spreads
    if not volumes:
        return 0.0

    recent_volumes = volumes[-n:]
    liquidity = sum(recent_volumes)

    if spreads and len(spreads) >= n:
        recent_spreads = [s for s in spreads[-n:] if s is not None]
        if recent_spreads:
            avg_spread = mean(recent_spreads)
            if avg_spread > 0:
                liquidity = liquidity / avg_spread

    return liquidity


def compute_priority(signal: Signal) -> float:
    """Weighted priority score for a single signal."""
    vol = compute_volatility(signal, N)
    liq = compute_liquidity(signal, N)
    return W_VOLATILITY * vol + W_LIQUIDITY * liq


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

SignalScore = Tuple[Signal, float]


def rank_signals(signals: List[Signal]) -> List[SignalScore]:
    """Compute priority for every active signal and return sorted (signal, score)."""
    scored = [(s, compute_priority(s)) for s in signals]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def classify_movers(
    scored: List[SignalScore],
    *,
    top_ratio: float = 0.2,
    low_ratio: float = 0.2,
) -> dict[str, List[SignalScore]]:
    """Partition ranked signals into Top / Mid / Low movers."""
    if not scored:
        return {"top": [], "mid": [], "low": []}

    n = len(scored)
    top_count = max(1, int(round(top_ratio * n)))
    low_count = max(1, int(round(low_ratio * n)))

    if top_count + low_count >= n:
        top_count = max(1, n // 3)
        low_count = max(1, n // 3)

    return {
        "top": scored[:top_count],
        "mid": scored[top_count : n - low_count],
        "low": scored[-low_count:] if low_count else [],
    }


# ---------------------------------------------------------------------------
# Convenience: build/update helpers
# ---------------------------------------------------------------------------

def build_signal(signal_id: str, **observations: Any) -> Signal:
    """Convenience constructor for a signal from keyword observations.

    Pass parallel arrays via prices, volumes, spreads, timestamps, or pass
    a sequence of single-interval observations with price/volume/spread/...
    """
    sig = Signal(signal_id=signal_id)
    if "prices" in observations:
        sig.prices = list(observations["prices"])
    if "volumes" in observations:
        sig.volumes = list(observations["volumes"])
    if "spreads" in observations:
        sig.spreads = list(observations["spreads"])
    if "timestamps" in observations:
        sig.timestamps = list(observations["timestamps"])
    return sig


def batch_update(
    signals: List[Signal],
    intervals: List[dict[str, Any]],
) -> None:
    """Update multiple signals with a list of interval observations.

    Each interval dict may include signal_id, price, volume, spread, timestamp.
    """
    by_id = {s.signal_id: s for s in signals}
    for it in intervals:
        sid = it.get("signal_id")
        if sid not in by_id:
            by_id[sid] = Signal(signal_id=sid)
            signals.append(by_id[sid])
        by_id[sid].update(
            price=it.get("price", 0.0),
            volume=it.get("volume", 0.0),
            spread=it.get("spread"),
            timestamp=it.get("timestamp"),
        )


if __name__ == "__main__":
    # Quick self-test / smoke test when run directly.
    s = Signal(signal_id="BTCUSD")
    for i in range(12):
        s.update(price=100 + i * 2, volume=1000 + i * 50, spread=0.5)
    priority = compute_priority(s)
    print(f"Signal {s.signal_id}: priority={priority:.4f}")
    print("Volatility:", compute_volatility(s))
    print("Liquidity:", compute_liquidity(s))

    scored = rank_signals([s])
    print("Ranked:", scored)
    print("Classified:", classify_movers(scored))
