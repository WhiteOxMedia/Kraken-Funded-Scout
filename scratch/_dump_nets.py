"""Dump every measured trade's net_pct for the contest maths (no new analysis)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import remeasure_live_config as R
from trendride_study import NOV1, load, prep

d = load(); trades = []
for sym, bars in d["pairs"].items():
    p = prep(bars)
    trades += [t for t in (R.trade(p, i) for i in R.live_entries(p)) if t]
trades.sort(key=lambda t: t[0])
out = {"all": [round(t[1], 6) for t in trades],
       "november": [round(t[1], 6) for t in trades if t[0] >= NOV1]}
json.dump(out, open(os.path.join(HERE, "_live_trade_nets.json"), "w"))
print(len(out["all"]), "trades,", len(out["november"]), "in November")
