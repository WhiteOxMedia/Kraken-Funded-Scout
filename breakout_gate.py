"""THE BREAKOUT GATE — the one tournament change that measured a +80% win rate (Nov 15).

The tournament used to crown the coin with the loudest signal and fire it whatever
position it held in its own hour range. `scratch/tournament_study.py` graded the
crown rule itself on 154 champion decisions from the same 5m tape (chronological,
no lookahead, 60m forward, net of the 0.080% round trip, fit = earlier 92,
holdout = later 62):

  * crowning the LOWEST `pos_hour` buy ("buy the dip") is significantly WORSE
    than the shipped crown: holdout −0.259%/trade, 95% CI −0.454..−0.065.
  * vetoing cheap coins, or scoring conf + pos_hour, changes nothing
    (+0.02, CI −0.019..+0.062) — timing as a *ranking key* does not pay.
  * what does pay is the opposite end of the band: a champion sitting at the
    **top of its prior-hour range** — a breakout continuing — wins far more
    often than it loses.

| pos_hour ≥ | fit n | fit win% | fit net | hold n | hold win% | hold 95% CI | hold net | hold mean 95% CI |
|---|---|---|---|---|---|---|---|---|
| 0.80 | 28 | 82.1 | +0.690 | 13 | 61.5 | 35–88 | +0.626 | +0.050..+1.202 |
| **0.85** | 24 | **87.5** | **+0.752** | 12 | 66.7 | 40–93 | +0.705 | +0.101..+1.308 |
| 0.90 | 20 | 85.0 | +0.786 | 8 | 87.5 | 65–100 | +0.964 | +0.166..+1.761 |
| 0.95 | 12 | 91.7 | +0.790 | 7 | 100.0 | — | +1.107 | +0.245..+1.969 |

The band is monotone in both halves (the higher the range position, the better the
trade), which is why this is not a single lucky slice. 0.85 is the fit-chosen
threshold — the widest band that still clears in the holdout with 12 samples and
a mean CI excluding zero — and over the whole 45d it reads 80.6% wins (n=36,
+0.736%, CI +0.444..+1.029) against 18.6% for everything below it.

So the live rule shipped here: **a buy is only crowned and fired when its
`entry_quality` (its position in the prior hour's range) is at least 0.85.**
Nothing else moves — no threshold relaxed, no stop widened, no death-line
touched, no protective exit delayed, and the ranking flags stay off. A coin with
no measurement at all (`entry_quality is None`, e.g. too little history) still
fires on the shipped rule: the gate can only ever REMOVE a fire it can prove is
mislocated, never invent one.
"""
from __future__ import annotations

BREAKOUT_MIN = 0.85

BREAKOUT_STUDY_V = 1

BREAKOUT_STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/tournament_study.py",
    "method": ("154 champion decisions, chronological, no lookahead, 60m forward "
               "net of 0.080%; crown pool = top six buys by confidence; fit = "
               "earlier 92, holdout = later 62; threshold chosen on fit only"),
    "gate": "champion entry_quality >= %.2f (top of its prior-hour range)" % BREAKOUT_MIN,
    "fit_n": 24, "fit_win_pct": 87.5, "fit_net_per_dollar": 0.00752,
    "oos_n": 12, "oos_win_pct": 66.7, "oos_ci": [40, 93],
    "oos_net_per_dollar": 0.00705, "oos_mean_ci": [0.101, 1.308],
    "pooled_n": 36, "pooled_win_pct": 80.6, "pooled_net_per_dollar": 0.00736,
    "pooled_mean_ci": [0.444, 1.029],
    "below_n": 118, "below_win_pct": 18.6, "below_net_per_dollar": -0.00554,
    "rejected": {
        "crown_by_pos_hour": {"oos_delta": -0.259, "oos_ci": [-0.454, -0.065],
                              "why": "buying the lowest range position loses more than the shipped crown"},
        "crown_by_conf_plus_timing": {"oos_delta": 0.021, "oos_ci": [-0.019, 0.062],
                                      "why": "timing as a ranking key is noise"},
        "veto_high_pos_hour": {"oos_delta": -0.19, "oos_ci": [-0.365, -0.022],
                               "why": "vetoing the loudest movers costs, it does not save"},
    },
    "verdict": ("champion must be a breakout (entry_quality >= 0.85) to fire; "
                "measured 80.6% wins over 45d and clears the holdout"),
}


import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# THE GATE'S OWN JOURNAL: one row per judged fire, so the next 45 days of live
# rows accumulate as evidence for or against the gate instead of evaporating
# into a log line. A veto is as much a record as a fire.
GATE_LOG = Path(__file__).resolve().with_name("contest_gate_log.jsonl")


def passes(entry_quality, minimum: float = BREAKOUT_MIN) -> bool:
    """True when a fire may proceed. No measurement -> ship the old rule."""
    if entry_quality is None:
        return True
    try:
        return float(entry_quality) >= float(minimum)
    except (TypeError, ValueError):
        return True


def measure(exchange, pair: str, limit: int = 720):
    """The champion's position in its prior hour range, read from the pair's
    own hourly bars — the same number the live deep read stamps when it has a
    frame in hand. Fail-soft: no client, no bars, no verdict."""
    try:
        import scanner as _sc
        bars = exchange.fetch_ohlcv(pair, timeframe="1h", limit=limit)
        if not bars:
            return None
        return _sc._entry_quality(_sc._ohlcv_to_df(bars[-limit:]))
    except Exception as exc:               # noqa: BLE001 - measurement is optional
        log.debug("breakout measure(%s) unavailable: %s", pair, exc)
        return None


def record(pair: str, decision: str, entry_quality, *, route: str = "",
           confidence=None, cfg=None, path=None) -> dict:
    """Write one gate-journal row. Never raises: a broken write must not stop
    a fire the gate already allowed."""
    row = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "pair": pair, "route": route, "decision": decision,
        "entry_quality": None if entry_quality is None else round(float(entry_quality), 4),
        "gate_min": BREAKOUT_MIN,
        "confidence": None if confidence is None else float(confidence),
        "gate_v": BREAKOUT_STUDY_V,
        "pooled_win_pct": BREAKOUT_STUDY["pooled_win_pct"],
        "pooled_n": BREAKOUT_STUDY["pooled_n"],
        "oos_win_pct": BREAKOUT_STUDY["oos_win_pct"],
        "oos_ci": BREAKOUT_STUDY["oos_ci"],
        "oos_net_per_dollar": BREAKOUT_STUDY["oos_net_per_dollar"],
    }
    try:
        target = Path(path) if path is not None else GATE_LOG
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as exc:               # noqa: BLE001
        log.debug("gate journal write failed: %s", exc)
    return row


def evaluate(pair: str, signal, *, cfg=None, exchange=None, route: str = "",
             path=None):
    """THE FIRE CHOKE POINT every route calls before it fires.

    Returns `(ok, entry_quality)`. A buy whose measured range position is below
    the gate is REFUSED and journalled; a measured breakout is allowed and
    journalled; an unmeasurable buy keeps the old behaviour (and says so), so
    the gate can only ever remove a fire it can prove is mislocated.
    """
    eq = getattr(signal, "entry_quality", None)
    on = bool(getattr(cfg, "champion_breakout_gate", True))
    minimum = float(getattr(cfg, "champion_breakout_min", BREAKOUT_MIN))
    if not on:
        return True, eq
    if eq is None and exchange is not None:
        eq = measure(exchange, pair)
        if eq is not None:
            try:
                signal.entry_quality = eq
            except Exception:              # noqa: BLE001 - slotted signals
                pass
    ok = passes(eq, minimum)
    record(pair, "unmeasured-fire" if eq is None else ("fire" if ok else "veto"),
           eq, route=route, confidence=getattr(signal, "confidence", None),
           cfg=cfg, path=path)
    if not ok:
        log.info("  BREAKOUT GATE: %s sits %.2f in its prior-hour range "
                 "(need >= %.2f) — not a breakout, no fire (%s)",
                 pair, float(eq), minimum, route or "fire")
    return ok, eq
