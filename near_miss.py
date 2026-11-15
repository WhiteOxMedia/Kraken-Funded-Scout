"""THE NEAR-MISS RECORDER — the sample wall's answer (Nov 15).

Every study this session hit the same wall: 12–15 trades per holdout span. The
gate and the crown produce far more *decisions* than fills — a champion vetoed at
`entry_quality 0.78` is a data point, not a trade — and until now those decisions
were written down with a verdict and nothing else. This module records the ones
near the line, with the features that decided them, and resolves each against
real 5m closes: what the fire would have returned over the hold window, its worst
adverse excursion, and whether it would have hit the −3% hard stop.

It is a SHADOW: it appends to its own journal, never touches the contest watch,
the account, the bridge or any order, and never changes a decision. The crown and
the mover route call it after they have already decided.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PATH = Path(__file__).resolve().with_name("contest_near_miss.jsonl")
HORIZON_MIN = 180.0        # the shipped hold window
STOP_PCT = 3.0             # the shipped contest guard (read-only here)
NEAR_MARGIN = 0.15         # how far under the gate counts as "near"
MAX_PER_CYCLE = 3


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _write(row: dict, path) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as exc:                     # noqa: BLE001
        log.debug("near-miss append failed: %s", exc)


def features(df, entry_quality, confidence) -> dict:
    """The decision features, from the same hourly frame the gate used."""
    out = {"entry_quality": None if entry_quality is None else round(float(entry_quality), 4),
           "confidence": None if confidence is None else round(float(confidence), 2),
           "ext4h": None, "base_pct": None}
    try:
        cl = [float(c) for c in df["close"]]
        lo = [float(c) for c in df["low"]]
        if len(cl) > 5:
            out["ext4h"] = round((cl[-1] / cl[-5] - 1.0) * 100.0, 3)
            base = min(lo[-12:]) if len(lo) >= 12 else min(lo)
            if base > 0:
                out["base_pct"] = round((cl[-1] / base - 1.0) * 100.0, 3)
    except Exception:                            # noqa: BLE001
        pass
    return out


def record_pool(cands, all_data, champion_pair, cfg, *, source="scanner",
                path=None) -> int:
    """Journal the vetoed champion and the pool's near-misses for this cycle."""
    gate_min = float(getattr(cfg, "champion_breakout_min", 0.85))
    rows = []
    for pair, r in (cands or {}).items():
        eq = getattr(r, "entry_quality", None)
        if eq is None:
            continue
        if pair == champion_pair:
            reason = f"champion vetoed: entry_quality {eq:.2f} < {gate_min:.2f}"
        elif gate_min - NEAR_MARGIN <= eq < gate_min:
            reason = f"near miss: entry_quality {eq:.2f} within {NEAR_MARGIN:.2f} of {gate_min:.2f}"
        else:
            continue
        row = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
               "ts_epoch": round(_now_ts(), 3), "pair": pair, "route": "crown",
               "source": source, "reason": reason, "gate_min": gate_min,
               "resolved": False,
               **features((all_data or {}).get(pair), eq,
                          getattr(r.signal, "confidence", None))}
        rows.append(row)
        if len(rows) >= MAX_PER_CYCLE:
            break
    for row in rows:
        _write(row, path or PATH)
    return len(rows)


def record_veto(pair, signal, eq, df, cfg, *, route, source="scanner", path=None) -> None:
    """Journal one single refused fire (the mover-bridge route)."""
    gate_min = float(getattr(cfg, "champion_breakout_min", 0.85))
    row = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
           "ts_epoch": round(_now_ts(), 3), "pair": pair, "route": route,
           "source": source,
           "reason": f"fire vetoed: entry_quality {eq:.2f} < {gate_min:.2f}",
           "gate_min": gate_min, "resolved": False,
           **features(df, eq, getattr(signal, "confidence", None))}
    _write(row, path or PATH)


def _bars_between(exchange, pair, start_s, end_s):
    try:
        bars = exchange.fetch_ohlcv(pair, timeframe="5m",
                                    since=int(start_s * 1000), limit=80)
    except Exception as exc:                     # noqa: BLE001
        log.debug("near-miss resolve fetch failed for %s: %s", pair, exc)
        return []
    out = []
    for b in bars or []:
        ts = float(b[0]) / 1000.0
        if start_s <= ts <= end_s:
            out.append((ts, float(b[1]), float(b[2]), float(b[3]), float(b[4])))
    return out


def resolve(exchange, cfg=None, *, path=None, now_s=None, limit=8) -> int:
    """Fill forward return / MAE / hard-stop-hit for rows old enough to judge.

    Read-only against the exchange, write-only to this journal. Returns how many
    rows were resolved.
    """
    target = Path(path or PATH)
    if not target.exists():
        return 0
    now_s = _now_ts() if now_s is None else now_s
    try:
        rows = [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception as exc:                     # noqa: BLE001
        log.debug("near-miss journal unreadable: %s", exc)
        return 0
    cost_pct = 0.08
    try:
        import contest_account as _ca
        cost_pct = float(_ca.COST_ROUNDTRIP_PCT)
    except Exception:                            # noqa: BLE001
        pass
    done = 0
    for row in rows:
        if done >= limit:
            break
        if row.get("resolved"):
            continue
        ts = float(row.get("ts_epoch") or 0.0)
        if not ts or now_s - ts < HORIZON_MIN * 60.0:
            continue
        pair = row.get("pair") or ""
        if "/" not in pair:
            row["resolved"] = True
            row["resolved_note"] = "unpriceable pair id"
            done += 1
            continue
        bars = _bars_between(exchange, pair, ts, ts + HORIZON_MIN * 60.0)
        if len(bars) < 3:
            continue                              # no tape for it; retry next cycle
        entry = bars[0][4] or bars[0][1]
        if entry <= 0:
            continue
        last = bars[-1][4]
        worst = min(b[3] for b in bars)
        peak = max(b[2] for b in bars)
        peak_pct = round((peak / entry - 1.0) * 100.0, 4)
        row.update({"resolved": True,
                    "resolved_ts": datetime.now(timezone.utc).astimezone().isoformat(),
                    "bars": len(bars),
                    "fwd_pct": round((last / entry - 1.0) * 100.0 - cost_pct, 4),
                    "mae_pct": round((worst / entry - 1.0) * 100.0, 4),
                    # THE PATH, not just the endpoint: a take-profit can only be
                    # judged by whether the peak offered it.
                    "peak_pct": peak_pct,
                    "reached_2pct": bool(peak_pct >= 2.0),
                    "hit_hard_stop": bool((worst / entry - 1.0) * 100.0 <= -STOP_PCT),
                    "hold_min": HORIZON_MIN})
        done += 1
    try:
        target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    except Exception as exc:                      # noqa: BLE001
        log.debug("near-miss journal rewrite failed: %s", exc)
    return done


def backfill_peaks(exchange, *, path=None, limit=200) -> int:
    """Fill `peak_pct` / `reached_2pct` on rows resolved before they were recorded.

    Same read-only fetch, same journal. Needed because the take-profit question is
    "did the trade offer +2%?", which the first resolved rows never stored.
    """
    target = Path(path or PATH)
    if not target.exists():
        return 0
    rows = [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    done = 0
    for row in rows:
        if done >= limit:
            break
        if not row.get("resolved") or row.get("peak_pct") is not None:
            continue
        pair = row.get("pair") or ""
        ts = float(row.get("ts_epoch") or 0.0)
        if "/" not in pair or not ts:
            continue
        bars = _bars_between(exchange, pair, ts, ts + HORIZON_MIN * 60.0)
        if len(bars) < 3:
            continue
        entry = bars[0][4] or bars[0][1]
        if entry <= 0:
            continue
        peak_pct = round((max(b[2] for b in bars) / entry - 1.0) * 100.0, 4)
        row["peak_pct"] = peak_pct
        row["reached_2pct"] = bool(peak_pct >= 2.0)
        done += 1
    try:
        target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    except Exception as exc:                     # noqa: BLE001
        log.debug("near-miss peak backfill failed: %s", exc)
    return done


def seed_from_gate_log(gate_log_path, *, path=None) -> int:
    """One-shot: turn the gate journal's real veto rows into resolvable near-misses.

    These rows came from real cycles; seeding them just gives the recorder the
    decisions that were already recorded before it existed. No order, no balance.
    """
    target = Path(path or PATH)
    src = Path(gate_log_path)
    if not src.exists():
        return 0
    added = 0
    seen = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                seen.add((r.get("pair"), round(float(r.get("ts_epoch") or 0.0))))
            except Exception:
                continue
    for line in src.read_text(encoding="utf-8").splitlines():
        try:
            g = json.loads(line)
        except Exception:
            continue
        if g.get("decision") != "veto" or g.get("source") != "scanner":
            continue
        try:
            ts_epoch = datetime.fromisoformat(g["ts"]).timestamp()
        except Exception:
            continue
        key = (g.get("pair"), round(ts_epoch))
        if key in seen:
            continue
        row = {"ts": g["ts"], "ts_epoch": round(ts_epoch, 3), "pair": g["pair"],
               "route": g.get("route"), "source": "gate-log-seed",
               "reason": f"seeded from gate journal: veto at entry_quality {g.get('entry_quality')}",
               "gate_min": g.get("gate_min"),
               "entry_quality": g.get("entry_quality"),
               "confidence": g.get("confidence"),
               "ext4h": None, "base_pct": None, "resolved": False}
        _write(row, target)
        seen.add(key)
        added += 1
    return added
