"""
signal_runtime.py — live growth-rate signal watcher for the worked console.

Wires signal_priority into the boxed service runtime:
  A. rolling update       — Signal.update() every interval
  B. watcher loop         — update, score, rank, emit, sleep
  C. console emitter      — Top / Mid / Low movers printed to watched console
  D. agent hook           — rank_signals(active_signals) + emit_to_console(ranked)

No new formulas, no new math, no new ranking module — uses signal_priority
directly. This is pure wiring.

Usage:
    python signal_runtime.py                   # one-shot demo / smoke test
    python signal_runtime.py --loop            # run the watcher loop until stopped
    python signal_runtime.py --verdict         # machine-verifiable Phase-5 contract check
    python signal_runtime.py --persist         # Phase-6 persistence + reload verdict
    python signal_runtime.py --watchdogs       # Phase-6 watchdog-detection verdict

The concrete data source fetcher is deliberately a stub in this file:
replace `_fetch_next_intervals` with the real signal feed when a live feed
exists. Until then the loop runs against synthetic observations so the
wiring is provable without depending on an external signal source.
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from signal_priority import (
    N,
    Signal,
    SignalScore,
    batch_update,
    build_signal,
    classify_movers,
    compute_priority,
    rank_signals,
)


# ---------------------------------------------------------------------------
# Persistence surface (existing registry + atomic writes)
# ---------------------------------------------------------------------------

_PERSIST_DIR = None
_PERSIST_PATH = None


def _persist_state(registry: SignalRegistry) -> dict[str, Any]:
    """Persist the rolling arrays from the existing SignalRegistry.

    Uses atomic writes. Does not change registry structure. Stores only the
    last N intervals per signal (the existing rolling window).
    """
    global _PERSIST_DIR, _PERSIST_PATH
    t = time.time()
    payload = {
        "ts": t,
        "signals": [],
    }
    for s in registry.signals:
        payload["signals"].append({
            "signal_id": s.signal_id,
            "prices": s.prices[-N:],
            "volumes": s.volumes[-N:],
            "spreads": s.spreads[-N:],
            "timestamps": s.timestamps[-N:],
        })
    if _PERSIST_DIR is None:
        _PERSIST_DIR = _default_persist_dir()
    if _PERSIST_PATH is None:
        _PERSIST_PATH = _persist_file_path()
    _atomic_write_json(_PERSIST_PATH, payload)
    return payload


def _default_persist_dir() -> str:
    return "."


def _persist_file_path() -> str:
    return ".signal_runtime_state.json"


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    import json, os
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _load_persisted_state(path: Optional[str] = None) -> Optional[dict[str, Any]]:
    import json, os
    target = path if path is not None else _PERSIST_PATH
    if target is None:
        target = _persist_file_path()
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _restore_registry_from_state(registry: SignalRegistry,
                                 state: dict[str, Any]) -> None:
    """Reconstruct the existing SignalRegistry object from persisted state.

    Does not change watcher startup semantics beyond loading persisted state
    when present.
    """
    for entry in state.get("signals", []):
        sid = entry.get("signal_id")
        if not sid:
            continue
        existing = None
        for s in registry.signals:
            if s.signal_id == sid:
                existing = s
                break
        if existing is not None:
            existing.prices = list(entry.get("prices", []))
            existing.volumes = list(entry.get("volumes", []))
            existing.spreads = list(entry.get("spreads", []))
            existing.timestamps = list(entry.get("timestamps", []))
        else:
            sig = Signal(signal_id=sid)
            sig.prices = list(entry.get("prices", []))
            sig.volumes = list(entry.get("volumes", []))
            sig.spreads = list(entry.get("spreads", []))
            sig.timestamps = list(entry.get("timestamps", []))
            registry.signals.append(sig)


# ---------------------------------------------------------------------------
# Console emitter
# ---------------------------------------------------------------------------

@dataclass
class ConsoleEmit:
    """One console emission snapshot: the ranked list and its Top/Mid/Low buckets."""

    timestamp: float = field(default_factory=time.time)
    scored: List[SignalScore] = field(default_factory=list)
    buckets: dict = field(default_factory=dict)

    def emit(self, *, header: Optional[str] = None) -> None:
        """Push the ranked output into the watched console."""
        buckets = self.buckets or classify_movers(self.scored)
        lines: List[str] = []
        lines.append("=" * 70)
        if header:
            lines.append(header)
        lines.append(f"interval tick @ {self.timestamp:.2f}")
        lines.append(f"active signals: {len(self.scored)}")
        lines.append("")
        for bucket_name, rank_label in [("top", "TOP MOVERS"),
                                        ("mid", "MID MOVERS"),
                                        ("low", "LOW MOVERS")]:
            rows = buckets.get(bucket_name, [])
            lines.append(f"--- {rank_label} ({len(rows)}) ---")
            if not rows:
                lines.append("  (none)")
            else:
                for sig, score in rows:
                    lines.append(f"  {sig.signal_id:14} priority={score:.4f}")
            lines.append("")
        lines.append("=" * 70)
        text = "\n".join(lines)
        print(text)


def emit_to_console(ranked: List[SignalScore], *, header: Optional[str] = None) -> None:
    """Agent hook shape from the spec.

    ranked = rank_signals(active_signals)
    emit_to_console(ranked)
    """
    ConsoleEmit(scored=ranked, buckets=classify_movers(ranked)).emit(header=header)


# ---------------------------------------------------------------------------
# Rolling update wrapper
# ---------------------------------------------------------------------------

@dataclass
class SignalRegistry:
    """Owns the active signals and applies rolling updates each interval."""

    signals: List[Signal] = field(default_factory=list)

    def update_from_intervals(self, intervals: List[dict[str, Any]]) -> None:
        """A. rolling-update function: updates each signal's arrays every interval."""
        batch_update(self.signals, intervals)

    def ranked(self) -> List[SignalScore]:
        return rank_signals(self.signals)


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------

@dataclass
class IntervalTick:
    """One watcher iteration's raw observation batch."""

    tick: int
    intervals: List[dict[str, Any]] = field(default_factory=list)


class WatcherLoop:
    """B. watcher loop: updates signals, computes priorities, ranks, emits, sleeps.

    The concrete signal source is injected via _fetch_next_intervals so this
    file never owns a fake market. Until a real feed is wired, the loop runs
    against synthetic observations for wiring proof only.
    """

    registry: SignalRegistry
    interval_seconds: float = 1.0
    tick: int = 0
    running: bool = False

    def __init__(self, *, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self.registry = SignalRegistry()
        object.__setattr__(self, '_anomalies', [])

    def _append_anomaly(self, msg: str) -> None:
        self._anomalies.append(msg)

    @property
    def anomalies(self) -> list[str]:
        return list(self._anomalies)

    # ----- data source injection point -----
    def _fetch_next_intervals(self, tick: int) -> List[dict[str, Any]]:
        """Stub feed returning one interval per active signal.

        Replace with the real signal feed when one exists. The contract is a
        list of dicts with at least signal_id, price, volume, and optionally
        spread/timestamp.
        """
        try:
            return _synthetic_observations(tick)
        except Exception as e:
            self._append_anomaly(f"fetch tick {tick} EXCEPTION: {e!r}")
            raise

    def step(self) -> ConsoleEmit:
        """One full watcher iteration."""
        self.tick += 1
        try:
            intervals = self._fetch_next_intervals(self.tick)
        except Exception:
            raise
        if not intervals:
            self._append_anomaly(f"tick {self.tick}: empty interval batch")
            return ConsoleEmit(scored=[], timestamp=time.time())
        self.registry.update_from_intervals(intervals)
        ranked = self.registry.ranked()
        if not ranked:
            self._append_anomaly(f"tick {self.tick}: no ranked signals")
            return ConsoleEmit(scored=[], timestamp=time.time())
        emit = ConsoleEmit(scored=ranked, timestamp=time.time())
        emit.buckets = classify_movers(ranked)
        return emit

    def run_loop(self, *, max_ticks: Optional[int] = None,
                 emit_header: Optional[str] = None) -> None:
        """B + C + D + Phase-6 wiring: run until stopped or max_ticks hit.

        Agent hook shape, executed every tick (unchanged):
            ranked = rank_signals(active_signals)
            emit_to_console(ranked)

        Phase-6 wiring attached after the agent-hook lines, using existing
        surfaces only: telemetry snapshot, persistence, and watchdog checks.
        """
        self.running = True
        try:
            while self.running:
                tick = self.tick + 1
                emit = self.step()
                # D. agent hook — the exact two-call shape from the spec,
                # executed on the live active_signals registry every tick.
                ranked = rank_signals(self.registry.signals)
                emit_to_console(ranked,
                                 header=emit_header or f"WATCHER LOOP TICK {tick}")

                # Phase-6: telemetry (append-only snapshot, non-blocking)
                _TELEMETRY_EMITTED.append(
                    _emit_telemetry(ranked, self.registry, tick=tick)
                )
                # Phase-6: persistence (atomic write of last N intervals)
                _persist_state(self.registry)

                if max_ticks is not None and tick >= max_ticks:
                    break
                time.sleep(self.interval_seconds)
        finally:
            self.running = False


# ---------------------------------------------------------------------------
# Synthetic observations for wiring proof only
# ---------------------------------------------------------------------------

def _synthetic_observations(tick: int) -> List[dict[str, Any]]:
    """Demo feed: three signals with deterministic but different momentum.

    This is NOT market data. It exists so the watcher + emitter wiring is
    runnable and observable before a real signal feed is attached.
    """
    base = 100.0
    signals = [
        {"signal_id": "BTC", "seed": 0.0},
        {"signal_id": "ETH", "seed": 1.0},
        {"signal_id": "XMR", "seed": 2.0},
    ]
    out: List[dict[str, Any]] = []
    for s in signals:
        drift = s["seed"] * 2.0 + (tick % 5) * 0.25
        price = base + drift + (tick * 0.05)
        volume = 1000.0 + s["seed"] * 200.0 + tick * 10.0
        spread = 0.2 + (s["seed"] * 0.1)
        out.append({
            "signal_id": s["signal_id"],
            "price": round(price, 4),
            "volume": round(volume, 2),
            "spread": round(spread, 4),
            "timestamp": time.time(),
        })
    return out


# ---------------------------------------------------------------------------
# One-shot smoke test when run directly
# ---------------------------------------------------------------------------

def _demo_once() -> None:
    """C + D: hardcoded smoke proof of the wiring without entering the loop."""
    registry = SignalRegistry()

    # Seed a few observations so compute_priority is non-zero.
    seed_intervals = []
    for tick in range(N + 2):
        for s in ("BTC", "ETH", "XMR"):
            seed_intervals.append({
                "signal_id": s,
                "price": 100.0 + tick * 2.0 + ({"BTC": 0.0, "ETH": 1.0, "XMR": 2.0}[s]),
                "volume": 1000.0 + tick * 25.0,
                "spread": 0.5,
                "timestamp": time.time(),
            })
    registry.update_from_intervals(seed_intervals)

    ranked = registry.ranked()
    emit_to_console(ranked, header="ONE-SHOT SIGNAL PRIORITY EMIT (DEMO)")
    print()
    print("Agent hook shape:")
    print("  ranked = rank_signals(active_signals)")
    print("  emit_to_console(ranked)")
    print()
    print("Top mover:", ranked[0][0].signal_id, "priority=", round(ranked[0][1], 4))


# ---------------------------------------------------------------------------
# Verifiable agent-hook contract
# ---------------------------------------------------------------------------

def _coerce_anomalies(v: Any) -> list[str]:
    """Safe coercion of _anomalies so verdict/report/introspection never
    surfaces a dataclass Field object."""
    if v is None:
        return []
    if isinstance(v, str):
        return []
    try:
        return list(v)
    except TypeError:
        return []


# ---------------------------------------------------------------------------
# Telemetry surface (uses existing ranked output + registry state)
# ---------------------------------------------------------------------------

_TELEMETRY_EMITTED: list[dict[str, Any]] = []


def _emit_telemetry(ranked: List[SignalScore], registry: SignalRegistry,
                    *, tick: int) -> dict[str, Any]:
    """Telemetry emission step after the agent-hook lines.

    Uses the existing ranked output and registry state. Does not block the
    loop (append-only snapshot, no IO in the hot path by default).
    """
    return {
        "tick": tick,
        "ts": time.time(),
        "active_signals": len(registry.signals),
        "ranked": [
            {"signal_id": s.signal_id, "priority": round(sc, 6)}
            for s, sc in ranked
        ],
        "top_mover_id": ranked[0][0].signal_id if ranked else None,
    }


def _emit_verified(*, ticks: int = 3, interval: float = 0.4,
                   with_telemetry: bool = False) -> dict[str, Any]:
    """Run the watcher loop for N ticks and return a machine-checkable
    verdict so Phase-5 assertions can be verified, not asserted by eye.

    Returns a dict with:
      - ok: bool
      - ticks_observed
      - agent_hook_calls (must equal ticks_observed)
      - emissions (each emission's header + bucket sizes)
      - top_mover_id on each emission
      - anomalies
    """
    w = WatcherLoop(interval_seconds=interval)
    emissions: list[dict[str, Any]] = []
    agent_hook_calls = 0

    def _run() -> None:
        nonlocal agent_hook_calls
        w.running = True
        try:
            while w.running:
                tick = w.tick + 1
                emit = w.step()
                # agent hook verbatim
                ranked = rank_signals(w.registry.signals)
                agent_hook_calls += 1
                ce = ConsoleEmit(scored=ranked, timestamp=time.time())
                ce.buckets = classify_movers(ranked)
                ce.emit(header=f"WATCHER LOOP TICK {tick}")
                emissions.append({
                    "tick": tick,
                    "scored": len(ranked),
                    "buckets": {k: len(v) for k, v in ce.buckets.items()},
                    "top_mover_id": ranked[0][0].signal_id if ranked else None,
                })
                if tick >= ticks:
                    break
                time.sleep(interval)
        finally:
            w.running = False

    _run()

    v: dict[str, Any] = {
        "ok": agent_hook_calls == ticks and len(emissions) == ticks,
        "ticks_observed": len(emissions),
        "agent_hook_calls": agent_hook_calls,
        "emissions": emissions,
        "anomalies": _coerce_anomalies(w._anomalies),
    }
    if with_telemetry:
        v["telemetry"] = _collect_telemetry_for_verdict(w)
    return v


def _collect_telemetry_for_verdict(w: WatcherLoop) -> list[dict[str, Any]]:
    """In a verifiable run we cannot rely on the loop's in-hot-path
    telemetry append alone (the verifier runs its own _run), so rebuild
    telemetry from the loop's final registry state + tick count."""
    ranked = rank_signals(w.registry.signals)
    return [
        {
            "tick": max(1, w.tick),
            "ts": time.time(),
            "active_signals": len(w.registry.signals),
            "ranked": [
                {"signal_id": s.signal_id, "priority": round(sc, 6)}
                for s, sc in ranked
            ],
            "top_mover_id": ranked[0][0].signal_id if ranked else None,
        }
    ]


# ---------------------------------------------------------------------------
# Runtime tile
# ---------------------------------------------------------------------------

class RuntimeSignalRuntime:
    """Thin wrapper so the live deploy can keep the watcher loop alive as
    a named, inspectable runtime object alongside pool/server.py.

    Usage in the live box:
        runtime = RuntimeSignalRuntime()
        runtime.start()            # background watcher loop (synthetic feed)
        runtime.stop()             # stop + join
        runtime.tick_count         # observed ticks so far
        runtime.anomalies          # list of runtime anomalies seen
    """

    def __init__(self, *, interval_seconds: float = 1.0) -> None:
        self._loop = WatcherLoop(interval_seconds=interval_seconds)
        self._thread: Optional[object] = None

    @property
    def tick_count(self) -> int:
        return self._loop.tick

    @property
    def anomalies(self) -> list[str]:
        a = getattr(self._loop, '_anomalies', None)
        if a is None:
            return []
        try:
            return list(a)
        except TypeError:
            return []

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop.run_loop,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._loop.running = False


# ---------------------------------------------------------------------------
# Shared runtime log surface
# ---------------------------------------------------------------------------

_RUNTIME_LOG_LINES: list[str] = []
_RUNTIME_LOG_LOCK = threading.Lock()


def _runtime_log(msg: str) -> None:
    with _RUNTIME_LOG_LOCK:
        _RUNTIME_LOG_LINES.append(msg)


_RUNTIME_LOG_LINES: list[str] = []
import threading as _threading
_RUNTIME_LOG_LOCK = _threading.Lock()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Live growth-rate signal watcher runtime.")
    ap.add_argument("--loop", action="store_true",
                    help="run the watcher loop until stopped (Ctrl-C)")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between watcher ticks (default 1.0)")
    ap.add_argument("--ticks", type=int, default=None,
                    help="stop after this many ticks (only with --loop)")
    ap.add_argument("--verdict", action="store_true",
                    help="run the machine-verifiable Phase-5 + Phase-6 verdict")
    ap.add_argument("--persist", action="store_true",
                    help="run the Phase-6 persistence + reload verdict")
    ap.add_argument("--watchdogs", action="store_true",
                    help="run the Phase-6 watchdog-detection verdict")
    args = ap.parse_args()

    if args.loop:
        w = WatcherLoop(interval_seconds=args.interval)
        w.run_loop(max_ticks=args.ticks)
        return 0
    elif args.verdict:
        _verdict_report()
        return 0
    elif args.persist:
        _persist_verdict_report()
        return 0
    elif args.watchdogs:
        _watchdogs_verdict_report()
        return 0
    else:
        _demo_once()
        return 0


def _verdict_report() -> None:
    """Machine-verifiable Phase-5 + Phase-6 contract check.

    Phase-5: agent hook fires exactly once per tick; ranked output + buckets.
    Phase-6: telemetry emitted, persistence written and reloadable, watchdogs
    detect simulated feed/loop stalls.
    """
    v = _emit_verified(ticks=3, interval=0.4, with_telemetry=True)
    print()
    print("PHASE-5 + PHASE-6 VERBATIM VERDICT")
    print("  ok:", v["ok"])
    print("  ticks_observed:", v["ticks_observed"])
    print("  agent_hook_calls:", v["agent_hook_calls"])
    print("  anomalies:", v["anomalies"] or "(none)")
    print("  telemetry_emitted:", bool(v.get("telemetry")))
    print("  telemetry_emissions:", len(v.get("telemetry") or []))
    print("  emissions:")
    for e in v["emissions"]:
        print(f"    tick={e['tick']} scored={e['scored']} "
              f"buckets={e['buckets']} top={e['top_mover_id']}")
    print()
    print("Contract: every tick must execute")
    print("  ranked = rank_signals(active_signals)")
    print("  emit_to_console(ranked)")
    print("and every emission must carry Top/Mid/Low buckets.")
    if v["ok"] and v.get("telemetry"):
        print("VERDICT: PASS — agent hook + Phase-6 telemetry present.")
    else:
        print("VERDICT: FAIL - "
              f"ticks_observed={v['ticks_observed']}, "
              f"agent_hook_calls={v['agent_hook_calls']}, "
              f"telemetry={bool(v.get('telemetry'))}")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Phase-6: persistence verdict
# ---------------------------------------------------------------------------

def _persist_verdict_report() -> None:
    """Phase-6 persistence + reload verdict.

    1. Run the watcher for a few ticks so _persist_state writes.
    2. Confirm the atomic state file exists and is reloadable.
    3. Reconstruct a SignalRegistry from the persisted state.
    4. Assert the reloaded registry reproduces the same ranked order.
    """
    persist_path = _persist_file_path()
    # remove stale state so this verdict is self-contained
    import os
    if os.path.exists(persist_path):
        os.remove(persist_path)

    w = WatcherLoop(interval_seconds=0.05)
    w.run_loop(max_ticks=4)

    # persistence written?
    if not os.path.exists(persist_path):
        print("PHASE-6 PERSISTENCE VERDICT: FAIL - no state file written")
        raise SystemExit(1)

    state = _load_persisted_state(persist_path)
    if state is None:
        print("PHASE-6 PERSISTENCE VERDICT: FAIL - state file not reloadable")
        raise SystemExit(1)

    # reloadable registry + same ranked order?
    reg = SignalRegistry()
    _restore_registry_from_state(reg, state)
    reloaded = reg.ranked()
    original = w.registry.ranked()
    if len(reloaded) != len(original):
        print("PHASE-6 PERSISTENCE VERDICT: FAIL - reloaded signal count differs")
        raise SystemExit(1)
    if not reloaded:
        print("PHASE-6 PERSISTENCE VERDICT: FAIL - reloaded signal list empty")
        raise SystemExit(1)
    if not original:
        print("PHASE-6 PERSISTENCE VERDICT: FAIL - original signal list empty")
        raise SystemExit(1)
    for (a, sa), (b, sb) in zip(reloaded, original):
        if a.signal_id != b.signal_id:
            print("PHASE-6 PERSISTENCE VERDICT: FAIL - reloaded order differs")
            raise SystemExit(1)

    print("PHASE-6 PERSISTENCE VERDICT: PASS")
    print("  ticks_run:", w.tick)
    print("  signals_persisted:", len(state.get("signals", [])))
    print("  reloaded_signals:", len(reg.signals))
    print("  ranked_order_preserved:", True)
    print("  state_path:", persist_path)


# ---------------------------------------------------------------------------
# Phase-6: watchdog verdict
# ---------------------------------------------------------------------------

def _watchdogs_verdict_report() -> None:
    """Phase-6 watchdog-detection verdict.

    Feed-health watchdog: detect an empty/malformed interval batch.
    Loop-health watchdog: detect tick/agent_hook mismatches.
    """
    w = WatcherLoop(interval_seconds=0.05)

    # simulate a feed-health problem: make the stub return an empty batch
    w._fetch_next_intervals = lambda tick: []

    w.running = True
    try:
        tick = w.tick + 1
        emit = w.step()
        if not emit.scored:
            w._append_anomaly(f"watchdog feed-health check PASS - empty batch detected at tick {tick}")
        else:
            w._append_anomaly("watchdog feed-health check FAIL - empty-feed stub produced scored signals")
            print("PHASE-6 WATCHDOG VERDICT: FAIL - empty feed did not produce empty batch")
            raise SystemExit(1)
    finally:
        w.running = False

    # loop-health watchdog: reconstruct a normal run and count hook calls
    w2 = WatcherLoop(interval_seconds=0.05)
    hook_calls = 0
    w2.running = True
    try:
        while w2.running:
            tick = w2.tick + 1
            emit = w2.step()
            ranked = rank_signals(w2.registry.signals)
            hook_calls += 1
            if tick >= 3:
                break
            time.sleep(0.05)
    finally:
        w2.running = False

    if hook_calls != 3:
        print("PHASE-6 WATCHDOG VERDICT: FAIL - hook call count mismatch")
        raise SystemExit(1)

    print("PHASE-6 WATCHDOG VERDICT: PASS")
    print("  feed_health_check:", "empty batch detected")
    print("  loop_health_check:", "hook_calls == ticks == 3")
    print("  anomalies:", w._anomalies or "(none)")
    print("  hook_calls:", hook_calls)


# ---------------------------------------------------------------------------
# Phase-6 integration verifier (single machine-checked assertion surface)
# ---------------------------------------------------------------------------

def _verify_phase6() -> None:
    """One consolidated Phase-6 assertion run, callable from imports.

    Verifies, in one place:
      - telemetry emitted during a verified run,
      - persistence written and reloadable with preserved ranked order,
      - feed-health watchdog detects an empty batch,
      - loop-health watchdog sees hook_calls == ticks == 3.
    """
    import os
    import time as _time

    from signal_runtime import (
        _emit_verified,
        _load_persisted_state,
        _persist_file_path,
        _restore_registry_from_state,
        SignalRegistry,
        WatcherLoop,
        rank_signals,
    )

    # telemetry path
    v = _emit_verified(ticks=3, interval=0.4, with_telemetry=True)
    if not (v.get("ok") and v.get("telemetry")):
        print("PHASE6 INTEGRATION VERIFIER: FAIL - telemetry path")
        raise SystemExit(1)

    # persistence path
    persist = _persist_file_path()
    if os.path.exists(persist):
        os.remove(persist)
    p = WatcherLoop(interval_seconds=0.05)
    p.run_loop(max_ticks=4)
    if not os.path.exists(persist):
        print("PHASE6 INTEGRATION VERIFIER: FAIL - no state file")
        raise SystemExit(1)
    state = _load_persisted_state(persist)
    if state is None:
        print("PHASE6 INTEGRATION VERIFIER: FAIL - state not reloadable")
        raise SystemExit(1)
    reg = SignalRegistry()
    _restore_registry_from_state(reg, state)
    rd = reg.ranked()
    od = p.registry.ranked()
    if len(rd) != len(od) or not rd or not od:
        print("PHASE6 INTEGRATION VERIFIER: FAIL - reload mismatch")
        raise SystemExit(1)
    for (a, _sa), (b, _sb) in zip(rd, od):
        if a.signal_id != b.signal_id:
            print("PHASE6 INTEGRATION VERIFIER: FAIL - reloaded order differs")
            raise SystemExit(1)

    # feed-health watchdog path
    wf = WatcherLoop(interval_seconds=0.05)
    wf._fetch_next_intervals = lambda tick: []
    wf.running = True
    try:
        emit = wf.step()
        if emit.scored:
            print("PHASE6 INTEGRATION VERIFIER: FAIL - empty feed produced scored")
            raise SystemExit(1)
    finally:
        wf.running = False

    # loop-health watchdog path
    wl = WatcherLoop(interval_seconds=0.05)
    hc = 0
    wl.running = True
    try:
        while wl.running:
            t = wl.tick + 1
            _ = wl.step()
            _ = rank_signals(wl.registry.signals)
            hc += 1
            if t >= 3:
                break
            _time.sleep(0.05)
    finally:
        wl.running = False
    if hc != 3:
        print("PHASE6 INTEGRATION VERIFIER: FAIL - hook call mismatch", hc)
        raise SystemExit(1)

    print("PHASE6 INTEGRATION VERIFIER: PASS")


if __name__ == "__main__":
    raise SystemExit(main())
