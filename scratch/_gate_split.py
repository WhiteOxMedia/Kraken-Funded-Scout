import sys, os, json
sys.path.insert(0,'.'); sys.path.insert(0,'scratch')
import numpy as np
import scratch.drift_gate as G

nets, tss, desc = G.series_shipped()
t = np.asarray(tss); a = np.asarray(nets)
OCT1 = t[np.searchsorted(t, 1759276800)] if False else None
import datetime as dt
def ts(s): return dt.datetime.strptime(s,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
K = 1000.0 if max(tss) > 1e11 else 1.0
may1, oct1, nov1 = ts("2026-05-01")*K, ts("2026-10-01")*K, ts("2026-11-01")*K
def seg(got, lo, hi):
    x=[v for v,tt in got if lo<=tt<hi]
    return (len(x), round(float(np.mean(x)),4)) if x else (0,None)
rows={}
for name, fn in (("baseline", lambda: [(v,tt) for v,tt in zip(nets,tss)]),
                 ("mean50_shadow", lambda: G.run_gate(nets,tss,"mean",50,mode="shadow")),
                 ("mean200_shadow", lambda: G.run_gate(nets,tss,"mean",200,mode="shadow"))):
    got=fn(); rows[name]={}
    for lab,lo,hi in (("May-Sep",may1,oct1),("Oct",oct1,nov1),("Nov",nov1,1e18*K)):
        rows[name][lab]=seg(got,lo,hi)
    print(f"{name:<15} " + "  ".join(f"{k} n={v[0]:>4} {v[1]}" for k,v in rows[name].items()))
json.dump(rows, open("scratch/_gate_split.json","w"), indent=1)
