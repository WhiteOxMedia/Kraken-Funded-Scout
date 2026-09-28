"""_gf_series.py - THE PER-DAY GF SEAM (Sep 28).

One shared class for every study that wants the guard factor's REAL
time dimension (the refinement _referee_gfdelta.py introduced): the
machinery runs each day at that day's factor (profit_guard.self_adjust
marks one MST day per row), so a simulated width should read the
factor live on the BAR's day - not one frozen state value.

What the seam owns:
  - the day->factor map from profit_guard.factor_history() (or any
    [[day, factor], ...] rows handed in - hermetic tests use that);
  - the frozen baseline (the state file's current factor - what every
    existing study priced, the parity anchor);
  - the chain-back rule: pre-series bars carry the newest known factor
    (every historical ride carried SOME real factor); the 1.0 floor
    only when no series exists at all;
  - bar-level lookup on the WRITER'S day key (MST / America/Denver -
    self_adjust marks once per MST day; UTC disagrees 6h/day);
  - the span gate: studies report their refinement only once the
    series covers MIN_DAYS distinct days (~2 weeks, the operator's
    accrual ask); before that they print their frozen rows and an
    honest NOT-YET line.

Day-key semantics match _referee_gfdelta.py exactly; this module is
now the single source of that mapping (the referee study imports it).

ASCII-pure; read-only over the state file.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
MIN_DAYS = 14                # ~2 weeks of series before the delta matters
DEFAULT_GF = 1.0             # profit_guard's floor factor


class GFSeries:
    """The guard factor's time dimension for one study run."""

    def __init__(self, rows=None, frozen=None, state_file=None):
        """        rows: [[day, factor], ...] or [{'day', 'factor'}] dicts;
        frozen: the baseline factor (defaults to the state file's top
        value, else DEFAULT_GF); state_file: explicit path (sandbox
        hook), else profit_guard's live state via factor_history()."""
        if rows is None:
            import profit_guard as _pg
            if state_file is not None:
                _pg.STATE_FILE = Path(state_file)
            hist = _pg.factor_history()
        else:
            hist = [{"day": r[0], "factor": r[1]} if isinstance(r, (list, tuple))
                    else r for r in rows]
        self.hist = sorted(
            (h for h in hist if isinstance(h, dict)
             and h.get("day") and isinstance(h.get("factor"), (int, float))),
            key=lambda h: str(h["day"]))
        self.series = {str(h["day"]): float(h["factor"]) for h in self.hist}
        if frozen is None:
            frozen = float(self.hist[-1]["factor"]) if self.hist \
                else DEFAULT_GF
        self.frozen = float(frozen)
        self.n_series = 0
        self.n_fallback = 0

    @property
    def span_days(self) -> int:
        return len(self.series)

    @property
    def span(self) -> str | None:
        return f"{min(self.series)}..{max(self.series)}" if self.series \
            else None

    def day_factor(self, day: str) -> float:
        """The factor live on an MST day: the series row first, the
        newest known factor chained backward (and forward - a future
        stamp rides the latest known until self_adjust speaks), the
        1.0 floor only when no series exists at all."""
        f = self.series.get(day)
        if f is None:
            f = float(self.hist[-1]["factor"]) if self.hist else DEFAULT_GF
        return f

    def at(self, ts) -> float:
        """Bar-level lookup: the factor live on the bar's MST day (the
        writer's day key). ts: tz-aware pandas.Timestamp / datetime."""
        d = ts.tz_convert(MST).strftime("%Y-%m-%d")
        if d in self.series:
            self.n_series += 1
        else:
            self.n_fallback += 1
        return self.day_factor(d)

    def ready(self) -> bool:
        """The span gate: True once the series covers MIN_DAYS days."""
        return self.span_days >= MIN_DAYS

    def notyet_line(self) -> str:
        return (f"NOT YET: the series covers {self.span_days} day(s); "
                f"the refinement reports at >= {MIN_DAYS} "
                f"(~2 weeks of real accrual)")


if __name__ == "__main__":
    s = GFSeries()
    print(f"frozen factor: {s.frozen:g}   span: {s.span or 'NONE'} "
          f"({s.span_days} day(s))   gate: >= {MIN_DAYS} -> "
          f"{'READY' if s.ready() else s.notyet_line()}")
