"""test_breakoutgate.py — the breakout gate, and the study that earned it (Nov 15).

The gate is the one tournament change this session measured into existence, so it
gets the same contract treatment as the ranking flags: the rule, its defaults, its
record, its live wire, and the promise that it can only ever REMOVE a fire.
"""
from __future__ import annotations

import json
import re
import sys
import traceback

import background_scanner as bs
import breakout_gate as bg
import config
import scanner as sc

PASS = FAIL = 0
lines = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  {detail}")


print("== 1. the rule: a breakout in its own hour range, and nothing else")
check("1.1 the gate is 0.85 by default", bg.BREAKOUT_MIN == 0.85, bg.BREAKOUT_MIN)
check("1.2 an unmeasured fire (None) proceeds — the gate can only remove fires",
      bg.passes(None) is True)
check("1.3 a top-of-range champion passes", bg.passes(0.85) and bg.passes(0.99))
check("1.4 a coin mid-range does not", bg.passes(0.849) is False and bg.passes(0.2) is False)
check("1.5 garbage fails open, never closed", bg.passes("n/a") is True)

print("== 2. it is switchable, and its shape cannot relax anything")
check("2.1 the config ships the gate ON and names the threshold",
      config.TradingConfig().champion_breakout_gate is True
      and config.TradingConfig().champion_breakout_min == 0.85)
check("2.2 the gate is judged on buy fires only — exits never touch it",
      'best_signal.action == "buy"' in
      open("background_scanner.py", encoding="utf-8").read().split("THE BREAKOUT GATE (Nov 15)")[1])
import ast
_tree = ast.parse(open("breakout_gate.py", encoding="utf-8").read())
_names = {n.id for n in ast.walk(_tree) if isinstance(n, ast.Name)}
_attrs = {n.attr for n in ast.walk(_tree) if isinstance(n, ast.Attribute)}
check("2.3 the gate touches no stop, goal or death-line — it only judges the entry",
      not {n for n in _names | _attrs
           if any(k in n.lower() for k in ("death", "stop", "goal", "leverage"))},
      sorted(_names | _attrs))

print("== 3. the study behind it is a record, not a claim")
st = bg.BREAKOUT_STUDY
check("3.1 it names the gate, the sample and the method",
      "0.85" in st["gate"] and st["fit_n"] > 0 and st["oos_n"] > 0
      and "no lookahead" in st["method"], st["gate"])
check("3.2 the holdout clears: win rate above the 18.6% rest, mean CI above zero",
      st["oos_win_pct"] > st["below_win_pct"] and st["oos_mean_ci"][0] > 0
      and len(st["oos_ci"]) == 2)
check("3.3 the pool, 45 days, reads at or above the +80% target",
      st["pooled_win_pct"] >= 80.0 and st["pooled_mean_ci"][0] > 0,
      (st["pooled_n"], st["pooled_win_pct"]))
check("3.4 the rejected crown rules are recorded with their numbers",
      set(st["rejected"]) == {"crown_by_pos_hour", "crown_by_conf_plus_timing",
                              "veto_high_pos_hour"}
      and st["rejected"]["crown_by_pos_hour"]["oos_ci"][1] < 0)
check("3.5 the verdict says what ships", "entry_quality >= 0.85" in st["verdict"])

print("== 4. the live champion path obeys it (the real function, not a copy)")
_orig_eq = sc._entry_quality


class _DeadEx:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("offline")

    def fetch_ticker(self, *a, **k):
        return {"high": 10.0, "low": 8.0, "last": 9.0, "quoteVolume": 5e6}


def _crown(eq):
    sc._entry_quality = lambda df, n=None, _v=eq: _v
    res = {"DIP/USD": sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])}
    return bs.deep_crown_pool(_DeadEx(), res, {"DIP/USD": object()}, config.TradingConfig(),
                              "DIP/USD", res["DIP/USD"], 50)


try:
    _bp, _bs, _c, _r = _crown(0.40)
    check("4.1 a champion mid-range is NOT crowned and does NOT fire",
          _bp is None and _bs is None, (_bp, _bs))
    _bp2, _bs2, _c2, _r2 = _crown(0.93)
    check("4.2 a champion at the top of its hour range fires, with the read attached",
          _bp2 == "DIP/USD" and _bs2 is not None and _bs2.entry_quality == 0.93,
          (_bp2, getattr(_bs2, "entry_quality", None)))
    _cfgoff = config.TradingConfig()
    _cfgoff.champion_breakout_gate = False
    sc._entry_quality = lambda df, n=None: 0.10
    _res3 = {"DIP/USD": sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])}
    _bp3, _bs3, _c3, _r3 = bs.deep_crown_pool(_DeadEx(), _res3, {"DIP/USD": object()},
                                             _cfgoff, "DIP/USD", _res3["DIP/USD"], 50)
    check("4.3 with the flag off the old behaviour stands (so it is reversible)",
          _bp3 == "DIP/USD" and _bs3 is not None, _bp3)
    sc._entry_quality = lambda df, n=None: None
    _res4 = {"DIP/USD": sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])}
    _bp4, _bs4, _c4, _r4 = bs.deep_crown_pool(_DeadEx(), _res4, {"DIP/USD": object()},
                                             config.TradingConfig(), "DIP/USD",
                                             _res4["DIP/USD"], 50)
    check("4.4 an unmeasured champion still fires (fail-open, never fail-closed)",
          _bp4 == "DIP/USD")
finally:
    sc._entry_quality = _orig_eq

print("== 5. the evidence rides the fire and the close")
_row = {"breakout_gate": dict(st), "breakout_gate_v": bg.BREAKOUT_STUDY_V}
_fam = bs._family_fields(_row)
check("5.1 the journal's one writer carries the gate's study",
      (_fam.get("breakout_gate") or {}).get("pooled_win_pct") == st["pooled_win_pct"]
      and _fam.get("breakout_gate_v") == 1)
check("5.2 a legacy row lands empty and versioned",
      bs._family_fields(None)["breakout_gate"] is None
      and bs._family_fields(None)["breakout_gate_v"] == 0)
_src_bg = open("background_scanner.py", encoding="utf-8").read()
check("5.3 the row is stamped with it at fire time, beside the exit study",
      '"breakout_gate": dict(_bk_gate.BREAKOUT_STUDY)' in _src_bg
      and '"exit_study": dict(EXIT_STUDY)' in _src_bg)
check("5.4 the gate runs inside the live crown function, before it returns",
      _src_bg.index("BREAKOUT GATE: %s sits") <
      _src_bg.index("return best_pair, best_signal, cands, ranks"))

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
