"""coin_floors.py — THE PER-COIN SCORE FLOOR (Sep 17).

WHY THIS EXISTS: the popup floor is fleet-wide — every coin pays the
same assembled bar. But the floor's court is not the fleet: it is each
coin's OWN blocked trades. ZEC dies at 78 in this regime while SOL
clears at 80 — the same bar buries a habitual marginal payer and
admits a habitual marginal dud. The tide memory (Sep 10) personalizes
the OUT bar from tide-conditioned payer rates; this module
personalizes the SCORE FLOOR itself from each coin's own blocked-trade
distribution: replay the coin's 1h store, find the hours the strategy
scored a buy INSIDE the sub-floor band (the floor's own court — the
hours the bar taxes this coin; conf in band implies a buy, the
strategy buys at 40), grade each blocked hour's forward outcome, and
learn the score under which this coin's blocked buys stop paying.

THE EDGE (the tide memory's shape): edge_pts = median(payer blocked
scores) - median(dud blocked scores), clamped to [0, FLOOR_EDGE_MAX_PTS].
RELIEF ONLY: the learner may ease the fleet's floor for this coin,
never tighten it — a dud-heavy band clamps to 0. The personal bar =
assembled bar - edge, clamped to [FLOOR_BAR_FLOOR, 97] at the gate.

Fail-closed everywhere: no store, thin blocked history (< MIN_BLOCKED),
too few payers (< MIN_PAYERS), or a broken read -> edge 0 -> the fleet
bar unchanged. The scan-path helper NEVER computes synchronously (the
first full replay is minutes): a cold cache says "warming" and ships
no relief until the driver's pass fills it — a stalled scan pass is
worse than a fleet bar.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("coin_floors")

ROOT = Path(__file__).parent
STORE_DIR = ROOT / "surething_data" / "kraken"
FILE = ROOT / "coin_floors.json"

WARMUP = 30          # store warmup hours before the replay judges
HORIZON = 4          # the popup contract's forward window (hours)
RAN_PCT = 2.0        # "it ran" = +2% above the blocked hour's close
BAND_HI = 84.0       # the court's ceiling (the fleet floor's usual top)
BAND_LO = 72.0       # the court's floor (below this, relief can never fire)
FLOOR_EDGE_MAX_PTS = 4.0   # the biggest relief a coin can earn
FLOOR_BAR_FLOOR = 70.0     # the personal bar never ships below this
MIN_BLOCKED = 12     # a coin needs this many blocked hours to have a floor
MIN_PAYERS = 3       # and this many payers inside the band to learn from

# THE RECENCY HALF-LIFE (Sep 17): a blocked trade's weight fades by half
# every DECAY_HALF_LIFE_H hours, so a coin's bar tracks its recent regime
# instead of its all-time distribution. Same convention as the tide
# memory's live layer (7 days) - the two learners share one idea of
# "recent". An old hot streak fades; a recent one carries.
DECAY_HALF_LIFE_H = 24 * 7.0

CACHE_TTL_S = 6 * 3600.0
LIVE_REFRESH_S = 6 * 3600.0

_lock = threading.Lock()
_cache = {"data": None, "ts": 0.0, "err": None}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _evaluate(df, cfg):
    """The scorer (module-level so tests can sandbox it): the real
    strategy's evaluate_strategy. conf-in-band implies a buy (the
    strategy buys at 40), so the court keys on the score alone."""
    from strategy import evaluate_strategy
    return evaluate_strategy(df, cfg)


def _replay_pair(path, evaluator=None, cfg=None) -> dict | None:
    """One store -> the coin's blocked trades inside the court: hours
    scoring a buy-conf in [BAND_LO, BAND_HI), consecutive runs
    collapsed to one trade, each graded end-move and ran (the popup
    contract). Returns None for stores too short to judge."""
    import pandas as _pd
    ev = evaluator or _evaluate
    if cfg is None:
        from config import DEFAULT_CONFIG as cfg  # noqa: F811
    df = _pd.read_feather(path)
    df["date"] = _pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    if len(df) <= WARMUP + HORIZON + 10:
        return None
    blocked = []
    prev = False
    for i in range(WARMUP, len(df) - HORIZON):
        win = df.iloc[: i + 1]
        try:
            s = ev(win.copy(), cfg)
        except Exception:
            prev = False
            continue
        try:
            conf = float(getattr(s, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        in_band = BAND_LO <= conf < BAND_HI
        if in_band:
            if not prev:
                entry = float(win["close"].iloc[-1])
                fwd = df.iloc[i + 1: i + 1 + HORIZON]
                end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
                ran = bool((float(fwd["high"].max()) / entry - 1.0) * 100.0
                           >= RAN_PCT)
                blocked.append({"c": conf, "end": round(end, 2), "ran": ran,
                                "ts": float(df.index[i].timestamp())})
            prev = True
        else:
            prev = False
    return {"pair": Path(path).name.replace("-1h.feather", ""),
            "blocked": blocked}


def _wmedian(pairs: list) -> float:
    """The decayed median: (score, weight) pairs; the median is the
    score where the cumulative weight first crosses half the total -
    the plain median when every weight is 1."""
    if not pairs:
        return float("nan")
    tot = sum(w for _, w in pairs)
    half, acc = tot / 2.0, 0.0
    for c, w in pairs:
        acc += w
        if acc >= half:
            return float(c)
    return float(pairs[-1][0])


def _learn_from(blocked: list, now: float | None = None) -> dict:
    """The learner, factored so the study shares one set of semantics:
    the blocked list -> the coin's floor record (edge clamped to
    [0, FLOOR_EDGE_MAX_PTS], relief only).

    THE RECENCY DECAY (Sep 17): every blocked trade weighs by
    0.5 ** (age_h / DECAY_HALF_LIFE_H) - the same 7-day half-life the
    tide memory's live layer uses - so the bar tracks the coin's recent
    regime, not its all-time distribution. Gates key on DECAYED weight:
    an all-time past can no longer earn relief once it fades, and the
    living court must still hold the floor's full sample size. Raw
    counts only decide the thin message. A trade without a ts
    (hand-built worlds) weighs as fresh."""
    rec = {"edge": 0.0, "n": len(blocked), "payers": 0, "duds": 0,
           "w_n": 0.0, "w_payers": 0.0, "w_duds": 0.0,
           "payer_med": None, "dud_med": None, "why": "thin"}
    if len(blocked) < MIN_BLOCKED:
        return rec
    t = time.time() if now is None else float(now)
    pays, duds = [], []
    w_all = wp = wd = 0.0
    for b in blocked:
        try:
            age_h = max(0.0, (t - float(b["ts"])) / 3600.0)
        except (KeyError, TypeError, ValueError):
            age_h = 0.0
        w = 0.5 ** (age_h / DECAY_HALF_LIFE_H)
        w_all += w
        if b["ran"]:
            pays.append((float(b["c"]), w))
            wp += w
        else:
            duds.append((float(b["c"]), w))
            wd += w
    rec["payers"], rec["duds"] = len(pays), len(duds)
    rec["w_n"] = round(w_all, 2)
    rec["w_payers"], rec["w_duds"] = round(wp, 2), round(wd, 2)
    if w_all < MIN_BLOCKED:
        rec["why"] = (f"expired (decayed weight {w_all:.1f} of "
                      f"{MIN_BLOCKED} needed)")
        return rec
    if wp < MIN_PAYERS or wd < 1.0:
        rec["why"] = (f"no structure (decayed payer weight {wp:.1f} of "
                      f"{MIN_PAYERS:g} needed)")
        return rec
    pm = _wmedian(pays)
    dm = _wmedian(duds)
    rec["payer_med"], rec["dud_med"] = round(pm, 1), round(dm, 1)
    if pm <= dm:
        rec["why"] = "no relief (duds outscore payers in the band)"
        return rec
    rec["edge"] = round(min(FLOOR_EDGE_MAX_PTS, pm - dm), 2)
    rec["why"] = "earned"
    return rec


def _save(data: dict) -> None:
    try:
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(FILE)
    except Exception as exc:
        logger.warning("coin-floors save failed (%s) — the cache still "
                       "serves this cycle", exc)


def compute() -> dict:
    """One full replay of the contest stores -> per-coin floor records.
    HEAVY — ladder-grade weight (~30 min for the 54 stores; the same
    hour-by-hour evaluate_strategy replay). The driver and the warm
    path own it, off the scan path; the scan path never calls this."""
    try:
        paths = sorted(STORE_DIR.glob("*_USD-1h.feather"))
    except Exception as exc:
        with _lock:
            _cache.update({"data": None, "ts": time.time(),
                           "err": f"stores unreadable ({exc})"})
        return {}
    from config import DEFAULT_CONFIG as cfg
    wanted = {str(p).replace("/", "_") for p in (cfg.trading_pairs or [])}
    coins = {}
    for path in paths:
        pair = path.name.replace("-1h.feather", "")
        if wanted and pair not in wanted:
            continue
        try:
            r = _replay_pair(path, cfg=cfg)
        except Exception as exc:
            logger.debug("coin floors: %s skipped (%s)", pair, exc)
            continue
        if r is None:
            continue
        coins[r["pair"]] = _learn_from(r["blocked"])
    data = {"ts": _utcnow_iso(), "coins": coins}
    with _lock:
        _cache.update({"data": data, "ts": time.time(), "err": None})
    _save(data)
    return data


def floor_edge(pair: str) -> tuple[float, str]:
    """(relief_pts, why) for this coin's score floor. pts > 0 eases the
    coin's assembled bar (it pays below it), 0 = the fleet bar stands.
    NEVER computes synchronously: a cold cache says 'warming' — the
    driver's pass fills it, and a stalled scan pass is worse than a
    fleet bar."""
    with _lock:
        data, ts = _cache["data"], _cache["ts"]
    if data is None or time.time() - ts > CACHE_TTL_S:
        return 0.0, "coin floors warming (the driver fills them)"
    c = (data.get("coins") or {}).get(pair or "")
    if not c:
        return 0.0, f"no blocked history for {pair}"
    edge = float(c.get("edge") or 0.0)
    if edge <= 0:
        return 0.0, c.get("why") or "no earned floor edge"
    why = (f"{pair} blocked-payer median {c['payer_med']:.0f}% vs dud "
           f"{c['dud_med']:.0f}% ({c['payers']}P/{c['duds']}D of "
           f"{c['n']} blocked, {float(c.get('w_n') or 0.0):.1f} living)"
           f" -> -{edge:.1f} pts")
    return edge, why


def warm() -> int:
    """Refresh the cache (best-effort). Returns the count of coins with
    earned relief; never raises."""
    try:
        data = compute()
        return sum(1 for c in (data.get("coins") or {}).values()
                   if float(c.get("edge") or 0.0) > 0)
    except Exception as exc:
        logger.info("COIN FLOORS: warm pass failed (%s) — the cache "
                    "stands", exc)
        return 0


def floors_driver_loop(stop, refresh_s: float = LIVE_REFRESH_S,
                       first_delay_s: float = 300.0) -> None:
    """The scanner thread body: one pass shortly after boot (the
    relief ships from the first hours), then every refresh_s. Never
    raises; dies with the stop event."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            n = warm()
            logger.info("COIN FLOORS: refreshed — %d coin(s) with earned "
                        "relief (next in %.0f min)", n, refresh_s / 60.0)
        except Exception as exc:
            logger.info("COIN FLOORS: pass failed (%s) — this cycle "
                        "skips, the cache stands", exc)
        stop.wait(refresh_s)
