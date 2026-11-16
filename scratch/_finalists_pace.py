import json, os, sys
import numpy as np
HERE=os.path.dirname(os.path.abspath('.')); sys.path.insert(0,'.'); sys.path.insert(0,'scratch')
import scratch.exit_take_sweep as S
from trendride_study import load, prep

tolls=S.tokens(); entries=[e for e in json.load(open(S.ENTRIES)) if e[0] in tolls]
d=load(); series={}
for sym in tolls:
    if sym not in d["pairs"]: continue
    p=prep(d["pairs"][sym]); series[sym]={"h":p["h"],"l":p["l"],"c":p["c"],"index":{t:i for i,t in enumerate(p["ts"])}}
entries=[e for e in entries if e[0] in series]; entries.sort(key=lambda r:r[1])
PACE=len(entries)/183.0
print(f"entries {len(entries):,}  pace {PACE:.1f}/day")
cases=[(1.0,0.2,0.08,2.0),(1.0,0.2,0.08,0.0),(1.5,0.2,0.08,0.0),(2.0,0.4,0.15,0.0)]
for c in cases:
    nets,*_=S.simulate(entries,series,tolls,*c)
    out=[]
    for s in (0.01,0.02):
        for days in (180,365,730):
            p,dd=S.mc(nets,s,int(PACE*days),paths=1200,seed=4)
            out.append(f"{s*100:g}%/{days}d {p*100:.0f}%/{dd*100:.0f}%")
    print(f"{c}: "+"  ".join(out))
