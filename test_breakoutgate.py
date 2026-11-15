"""test_breakoutgate.py — the breakout gate, and the study that earned it (Nov 15).

The gate is the one tournament change this session measured into existence, so it
gets the same contract treatment as the ranking flags: the rule, its defaults, its
record, its live wire, and the promise that it can only ever REMOVE a fire.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
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
_orig_gate_log = bg.GATE_LOG
bg.GATE_LOG = pathlib.Path(tempfile.mkdtemp()) / "gate_section4.jsonl"


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
    bg.GATE_LOG = _orig_gate_log

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

print("== 6. the choke point: every fire judged once, and journalled either way")
import tempfile

_tmp = pathlib.Path(tempfile.mkdtemp()) / "gate.jsonl"
_real_log = bg.GATE_LOG
try:
    bg.GATE_LOG = _tmp
    _sig_low = sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])
    _sig_low.entry_quality = 0.40
    _ok1, _eq1 = bg.evaluate("DIP/USD", _sig_low, cfg=config.TradingConfig(), route="test")
    _rows1 = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
    check("6.1 a sub-gate buy is refused at the choke point", _ok1 is False and _eq1 == 0.40)
    check("6.2 the refusal is a journal row with gate v, entry_quality and pooled/OOS",
          _rows1[-1]["decision"] == "veto" and _rows1[-1]["gate_v"] == 1
          and _rows1[-1]["entry_quality"] == 0.4
          and _rows1[-1]["pooled_win_pct"] == bg.BREAKOUT_STUDY["pooled_win_pct"]
          and len(_rows1[-1]["oos_ci"]) == 2, _rows1[-1])
    _sig_high = sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])
    _sig_high.entry_quality = 0.95
    _ok2, _ = bg.evaluate("RIP/USD", _sig_high, cfg=config.TradingConfig(), route="test")
    _rows2 = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
    check("6.3 a breakout buy is allowed and journalled as a fire",
          _ok2 is True and _rows2[-1]["decision"] == "fire" and _rows2[-1]["pair"] == "RIP/USD")
    _sig_none = sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"])
    _ok3, _ = bg.evaluate("NEW/USD", _sig_none, cfg=config.TradingConfig(), route="test")
    _rows3 = [json.loads(l) for l in _tmp.read_text(encoding="utf-8").splitlines()]
    check("6.4 an unmeasurable buy keeps the old behaviour, and says so",
          _ok3 is True and _rows3[-1]["decision"] == "unmeasured-fire")
    _cfgoff2 = config.TradingConfig()
    _cfgoff2.champion_breakout_gate = False
    _n_before = len(_rows3)
    _ok4, _ = bg.evaluate("DIP/USD", _sig_low, cfg=_cfgoff2, route="test")
    _n_after = len([l for l in _tmp.read_text(encoding="utf-8").splitlines() if l.strip()])
    check("6.5 with the gate off nothing is refused and nothing is journalled",
          _ok4 is True and _n_after == _n_before)

    class _Bars:
        def fetch_ohlcv(self, *a, **k):
            bars = []
            for i in range(30):
                px = 10.0 + i * 0.1
                bars.append([i * 3600000, px, px + 0.05, px - 0.05, px, 100.0])
            return bars

    class _DeadBars(_Bars):
        def fetch_ohlcv(self, *a, **k):
            raise RuntimeError("dead")

    _measured = bg.measure(_Bars(), "X/USD")
    check("6.6 the choke can measure a pair it was not handed a frame for",
          _measured is not None and 0.9 < _measured <= 1.0, _measured)
    check("6.7 ... and a dead venue measures nothing (no invented verdict)",
          bg.measure(_DeadBars(), "X/USD") is None)
finally:
    bg.GATE_LOG = _real_log

_src = open("background_scanner.py", encoding="utf-8").read()
_send = _src[_src.index("def send_alert("):]
_send = _send[:_send.index("def log_winner(")]
check("6.8 the alert route judges every buy at the choke point before any write",
      "_bk_gate.evaluate(" in _send
      and _send.index("_bk_gate.evaluate(") < _send.index("write_buy_signal"))
check("6.9 the mover route is judged too, before its popup",
      "route=\"mover\"" in _src
      and _src.index("_m_gate_ok") < _src.index("notify_big(_m_title"))
check("6.10 the crown's own refusal is journalled, not just logged",
      "_bk_gate.record(best_pair or" in _src)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
