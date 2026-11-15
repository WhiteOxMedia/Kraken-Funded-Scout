"""THE EXIT STUDY RECORD — measured, and it says "keep the shipped exit" (Nov 15).

`scratch/exit_edge_study.py` marks every fired entry's full forward path from the
same 5m tape and grades the exit variants over the SAME entries and windows:
take-profit distance, stop distance (only ever tightened) and hold horizon,
charged the journal's median round-trip cost, paired against the shipped exit,
with the earlier 60% of the history as the fit window and the later 40% an
untouched holdout.

**No variant clears.** Every holdout delta's 95% interval straddles zero, and
every variant is net negative per dollar on the holdout, including the shipped
exit. So the live exit behaviour is unchanged: no stop is widened, no death-line
touched, no protective exit delayed. This record exists so the negative result
travels with every fire instead of being re-argued later.
"""
from __future__ import annotations

EXIT_STUDY_V = 1

EXIT_STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/exit_edge_study.py",
    "method": ("chronological, no lookahead; each fired entry's full 5m path; "
               "every variant marked over the same entries and windows; net of "
               "the journal's median 0.080% round trip; paired vs shipped exit; "
               "fit = earlier 60%, holdout = later 40%"),
    "entries": 147,
    "fit_n": 88,
    "oos_n": 59,
    "cost_pct": 0.080,
    "shipped": {"name": "tp 3.0 / stop 3.0 / hold 180",
                "oos_win_pct": 30.5, "oos_net_per_dollar": -0.00473},
    "variants": {
        "hold 60": {"oos_delta_pct": 0.138, "oos_ci": [-0.111, 0.387],
                    "oos_win_pct": 27.1, "oos_net_per_dollar": -0.00335},
        "hold 120": {"oos_delta_pct": -0.041, "oos_ci": [-0.255, 0.173],
                     "oos_win_pct": 32.2, "oos_net_per_dollar": -0.00514},
        "hold 240": {"oos_delta_pct": -0.057, "oos_ci": [-0.205, 0.091],
                     "oos_win_pct": 35.6, "oos_net_per_dollar": -0.00530},
        "tp 1.5": {"oos_delta_pct": -0.036, "oos_ci": [-0.131, 0.059],
                   "oos_win_pct": 30.5, "oos_net_per_dollar": -0.00509},
        "tp 2.0": {"oos_delta_pct": -0.045, "oos_ci": [-0.096, 0.006],
                   "oos_win_pct": 30.5, "oos_net_per_dollar": -0.00518},
        "tp 4.0": {"oos_delta_pct": 0.028, "oos_ci": [-0.011, 0.068],
                   "oos_win_pct": 30.5, "oos_net_per_dollar": -0.00445},
        "tp 5.0": {"oos_delta_pct": 0.045, "oos_ci": [-0.025, 0.115],
                   "oos_win_pct": 30.5, "oos_net_per_dollar": -0.00428},
        "stop 2.0 (tighter)": {"oos_delta_pct": -0.069, "oos_ci": [-0.267, 0.128],
                               "oos_win_pct": 28.8, "oos_net_per_dollar": -0.00542},
        "stop 1.5 (tighter)": {"oos_delta_pct": 0.001, "oos_ci": [-0.203, 0.206],
                               "oos_win_pct": 28.8, "oos_net_per_dollar": -0.00472},
        "tp 2.0 + hold 120": {"oos_delta_pct": -0.058, "oos_ci": [-0.275, 0.158],
                              "oos_win_pct": 32.2, "oos_net_per_dollar": -0.00531},
        "tp 2.0 + stop 2.0": {"oos_delta_pct": -0.097, "oos_ci": [-0.298, 0.103],
                              "oos_win_pct": 28.8, "oos_net_per_dollar": -0.00570},
    },
    "winners": [],
    "verdict": ("no variant clears the shipped exit out of sample — exit "
                "behaviour unchanged, no stop widened, no death-line relaxed"),
}
