"""fire_attribution.py — WHICH RELIEF MECHANISM UNLOCKED THE FIRE? (Sep 17).

WHY THIS EXISTS: several mechanisms now lower the walls — the clamp fix
(the personal OUT bar ships inside [80, 86), the band the bugged clamp
erased at 86), the per-coin score floors (a coin's blocked-trade court),
and the defer pass (the brain's timing hold rides the trend). A marginal
fire could come from any of them; knowing WHICH is paying decides what
gets tuned next. This module is the single source of truth for that
attribution — the fire paths stamp it into the fired snapshots, and
diag_fireattrib.py prices it in dollars.

THE RULE (necessity, not presence): a mechanism gets the fire only when
the fire would NOT have happened without it —
  clamp fix  necessary iff the fire is an OUT personal-bar fire and
             quick < 86 (the bugged clamp would have shipped 86 and
             buried it),
  coin floor necessary iff quick < the un-relieved base bar,
  both       when each alone would have buried it,
  defer      the fire exists only because the defer carve overrode a
             contradictory hold (no bar unlocked it — the brain did),
  fast_route the route waived the floor by design (never relief),
  none       the fire cleared every wall on its own.

Pure functions, no IO. Unknown shapes attribute to "none" (fail-closed:
nothing is tuned from an unknown).
"""

# attribution source constants (snapshot-safe semantic strings)
SRC_CLAMP = "clamp_fix"        # the OUT personal bar's [80,86) band
SRC_COIN = "coin_floor"        # per-coin score-floor relief
SRC_BOTH = "clamp_fix+coin_floor"
SRC_DEFER = "defer_pass"       # the brain's timing-defer carve
SRC_FAST = "fast_route"        # the fast route waived the floor (not relief)
SRC_NONE = "none"

CLAMP_WALL = 86.0   # the bugged clamp's floor — the band's old wall


def attribute_fire(quick: float | None, eff_bar: float | None,
                   base_bar: float | None, clamp_court: bool,
                   coin_pts: float | None, defer: bool = False,
                   fast_route: bool = False,
                   coin_name: str = SRC_COIN) -> dict:
    """Attribute one fire to the mechanism(s) that unlocked it.

    quick       — the score that fired
    eff_bar     — the binding bar it cleared (the personal OUT bar when
                  clamp_court, else the assembled floor)
    base_bar    — the bar WITHOUT the personalization (the raw learned
                  bar when clamp_court, else the un-relieved floor)
    clamp_court — True when the binding bar was the OUT personal bar
                  (the clamp fix's court)
    coin_pts    — truthy when a coin-level relief applies to the binding
                  bar (the score floor off-court; the coin's TIDE MEMORY
                  on-court — name it via coin_name)
    defer       — the fire rode the timing-defer pass
    fast_route  — the fast route waived the floor (attribution excluded)
    coin_name   — the coin-level mechanism's label (SRC_COIN default;
                  "tide_memory" on the OUT personal bar's court)

    Returns {source, unlock_pts, would_have_fired_unclamped,
             would_have_fired_unrelieved}. The would_have_* fields are
    None when that counterfactual doesn't apply.
    """
    res = {"source": SRC_NONE, "unlock_pts": 0.0,
           "would_have_fired_unclamped": None,
           "would_have_fired_unrelieved": None}

    if fast_route:
        res["source"] = SRC_FAST
        return res
    if quick is None or eff_bar is None:
        if defer:
            res["source"] = SRC_DEFER
        return res
    quick = float(quick)
    eff_bar = float(eff_bar)
    if quick < eff_bar:
        return res   # not a fire against the binding bar — nothing to attribute

    if clamp_court:
        res["would_have_fired_unclamped"] = bool(quick >= CLAMP_WALL)
    if base_bar is not None:
        res["would_have_fired_unrelieved"] = bool(quick >= float(base_bar))

    # necessity: would the fire have died without this mechanism?
    clamp_nec = bool(clamp_court) and quick < CLAMP_WALL
    coin_nec = (bool(coin_pts) and base_bar is not None
                and quick < float(base_bar))
    if clamp_nec and coin_nec:
        res["source"] = f"{SRC_CLAMP}+{coin_name}"
    elif clamp_nec:
        res["source"] = SRC_CLAMP
        res["unlock_pts"] = round(CLAMP_WALL - quick, 1)
    elif coin_nec:
        res["source"] = coin_name
        res["unlock_pts"] = round(float(base_bar) - quick, 1)
    elif defer:
        res["source"] = SRC_DEFER
    return res


def mover_unlock(quick: float | None, fleet_bar: float,
                 relieved_bar: float | None) -> dict:
    """The mover path's unlock: did the coin-floor relief re-read pass
    a candidate the fleet bar had killed? Returns {unlocked_by, pts} —
    unlocked_by is SRC_COIN when the relieved bar admits what the fleet
    bar denied, else SRC_NONE (pts is the relief's width)."""
    pts = 0.0
    if fleet_bar is not None and relieved_bar is not None:
        pts = float(fleet_bar) - float(relieved_bar)
    if quick is None or pts <= 0:
        return {"unlocked_by": SRC_NONE, "pts": 0.0}
    quick = float(quick)
    if quick < float(fleet_bar) and quick >= float(relieved_bar):
        return {"unlocked_by": SRC_COIN, "pts": pts}
    return {"unlocked_by": SRC_NONE, "pts": pts}
