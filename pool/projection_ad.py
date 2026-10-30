"""projection_ad.py — THE ADVERTISEMENT-ONLY PROJECTION SURFACE (Oct 26).

`jackpot_projection` is an ADVERTISEMENT, not an account. Each row is a
simulated or estimated "possible future jackpot": the real pot plus a
referral seed computed from Kraken referral status, refreshed hourly by
the worker. It holds no money, it is nobody's balance, and it is never
the number the pool is actually holding.

WHY THIS MODULE EXISTS. That table used to be readable from anywhere —
the worker read it for its own spike guard, and any handler could read
a row and paint it somewhere. The admin dashboard's "Current Jackpot"
card got its figure from it once, which told the operator they held
$337 when the pot held $620. Logic in `server.py` was fixed; a source
that merely *happens* not to be read is a convention, not a guarantee.
So the table now lives behind this module, and only this module:

    publish_estimate(...)   the worker's WRITE (keeps the spike guard)
    read_estimate(slot)     the ONE display read

THE DOOR IS GUARDED, NOT JUST NAMED. `read_estimate` refuses when the
URL being served is an admin route (`AdminReadForbidden`). The active
URL is carried in a ContextVar that `server.py` sets for every request,
so an admin handler that reaches for an estimate fails at the moment it
tries, loudly, instead of quietly putting an estimate in an operator's
dashboard. Writes stay allowed from admin: the operator's "run the
worker" trigger legitimately generates an estimate — it just never gets
to read one back as a pot.

THE ESTIMATE CANNOT MASQUERADE AS MONEY. It arrives as an `Estimate`,
which carries only projection prose (seed, users, freshness, source)
and cannot be converted to a number: it has no `__float__`, and the one
arithmetic it offers, `total_with(real_jackpot_usd)`, has to be HANDED
the real pot from `jackpot_engine`. This module cannot produce a pot
figure on its own, which is the point.

House style: no imports of the worker (kraken_referral imports THIS,
not the reverse), settings read directly through `database`, and a
failure to read degrades to None rather than to a number.
"""
import contextvars
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import database as db

# The worker's configuration names live here so there is ONE definition:
# the TTL that marks a row stale, and the % move that flags a spike.
TTL_SETTING = "KRAKEN_REFERRAL_TTL_SECONDS"          # default 3600s
SPIKE_SETTING = "KRAKEN_PROJECTION_SPIKE_PCT"        # default 25%

# Every URL under these prefixes is an OPERATOR surface. A projection is
# not an operator figure, so it may not be read while one is being served.
ADMIN_PREFIXES = ("/api/admin", "/admin")

_ACTIVE_PATH = contextvars.ContextVar("projection_ad_active_path",
                                      default=None)


class AdminReadForbidden(RuntimeError):
    """An admin endpoint tried to read the advertisement table."""


class NotAnAdSurface(ValueError):
    """A read asked for a surface that is not a public advertisement."""


class AdSlot(str, Enum):
    """The only places allowed to display a projection. Closed set: an
    unknown slot is refused, so 'admin' can never become a slot name."""

    HERO = "hero"                      # the card under the hero plate
    REFERRAL_PAGE = "referral_page"    # the referral explainer page


def mark_active_path(path):
    """Bind the URL being served to this request. Returns the token for
    `clear_active_path`; `server.py`'s middleware does this for every
    request so the guard sees the real surface."""
    return _ACTIVE_PATH.set(str(path or ""))


def clear_active_path(token):
    _ACTIVE_PATH.reset(token)


def active_path():
    return _ACTIVE_PATH.get()


def is_admin_surface(path=None):
    """True when the URL being served (or asked about) is an operator
    surface. Admin prefixes are matched on the PATH so query strings and
    trailing slashes cannot sidestep the guard."""
    p = str(active_path() if path is None else path or "").split("?")[0]
    return any(p == pre or p.startswith(pre + "/") for pre in ADMIN_PREFIXES)


def _setting(name, default, cast):
    raw = db.get_setting(name, "")
    try:
        return cast(raw) if raw not in ("", None) else default
    except (TypeError, ValueError):
        return default


def freshness_ttl():
    """Seconds after which a row is stale (min 60). One definition, used
    by the display read AND by the worker's config endpoint."""
    return max(60, _setting(TTL_SETTING, 3600, int))


def spike_pct():
    return _setting(SPIKE_SETTING, 25.0, float)


@dataclass(frozen=True)
class Estimate:
    """AN ADVERTISEMENT. Read it as prose, never as a balance:
    `referral_seed_usd` is what referrals MIGHT add, `total_with()`
    needs the real pot handed in, and the object refuses `float()`."""

    slot: AdSlot
    referral_seed_usd: float
    count_of_users: int
    updated_at: float
    source: str
    fresh: bool
    anomaly: int

    def __float__(self):
        raise TypeError(
            "an Estimate is an advertisement, not an amount — the pot "
            "comes from jackpot_engine.get_jackpot_balance()")

    def total_with(self, real_jackpot_usd):
        """`possible future pot` = the pot the pool really holds + the
        estimated seed. The real half is never invented here."""
        return round(float(real_jackpot_usd) + self.referral_seed_usd, 2)


def read_estimate(slot):
    """THE ONLY READ of `jackpot_projection`.

    `slot` must be an `AdSlot`. Raises `AdminReadForbidden` when the
    request being served is an admin route — the operator's pot figure
    comes from the engine's balance, never from this table. Returns None
    when no row has been written yet (the card then renders current-only).
    """
    if not isinstance(slot, AdSlot):
        raise NotAnAdSurface(
            f"projection reads are advertisement-only; {slot!r} is not an "
            f"AdSlot (known: {[s.value for s in AdSlot]})")
    if is_admin_surface():
        raise AdminReadForbidden(
            f"jackpot_projection is an advertisement and may not be read "
            f"while serving {active_path()!r}; the operator's pot figure is "
            f"jackpot_engine.get_jackpot_balance()")
    row = _latest_row()
    if not row:
        return None
    return Estimate(
        slot=slot,
        referral_seed_usd=round(float(row["referral_seed"] or 0.0), 2),
        count_of_users=int(row["count_of_users_included"] or 0),
        updated_at=float(row["updated_at"] or 0.0),
        source=str(row["source"] or ""),
        fresh=(time.time() - float(row["updated_at"] or 0.0))
        <= freshness_ttl(),
        anomaly=int(row["anomaly"] or 0))


def publish_estimate(possible_usd, seed_usd, current_usd, users, source,
                     spike_limit=None):
    """The worker's WRITE — one append-only row per run.

    Reads the previous row itself to answer the delta-spike question, so
    the worker never needs a display read (and an admin-triggered run
    never touches `read_estimate`). Returns the guard's verdict:

        {"anomaly": 0|1, "previous_usd": float|None, "delta_pct": float}

    `current_usd` is stored as provenance of the run, NOT as a figure
    anyone displays: the snapshot goes stale the moment the pot moves,
    so readers take the seed only and add the LIVE pot themselves.
    """
    possible = round(float(possible_usd), 2)
    prev = _latest_row()
    previous = None
    delta = None
    anomaly = 0
    if prev and prev["possible_future_jackpot"]:
        previous = float(prev["possible_future_jackpot"])
        if previous > 0:
            delta = abs(possible - previous) / previous * 100.0
            if delta > float(spike_pct() if spike_limit is None
                             else spike_limit):
                anomaly = 1
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO jackpot_projection (possible_future_jackpot,"
            " referral_seed, current_jackpot, count_of_users_included,"
            " anomaly, source) VALUES (?, ?, ?, ?, ?, ?)",
            (possible, round(float(seed_usd), 2), round(float(current_usd), 2),
             int(users), anomaly, str(source or "")))
    return {"anomaly": anomaly, "previous_usd": previous,
            "delta_pct": round(delta, 2) if delta is not None else None}


def _latest_row() -> Optional[dict]:
    """Internal: the prior row, for the write's spike guard and the
    display read. Not part of the surface — the name is private on
    purpose, and admin callers are refused before they get here."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM jackpot_projection ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
