import sys, json
sys.path.insert(0,'.'); sys.path.insert(0,'scratch')
import numpy as np
import spread_table as ST
import scratch.exit_take_sweep as TS
import scratch.block_bootstrap as BB
from trendride_study import load, prep, NOV1

snap = TS.tokens()
def tolls_for(drop=()):
    out={}
    for s,v in snap.items():
        if s in drop: continue
        row=(ST.load_table().get('pairs') or {}).get(s) or {}
        out[s]=float(row.get('avg_pct') or row.get('day_pct') or v)
    return out

d=load(); ents=json.load(open(TS.ENTRIES))
def run(drop, label):
    t=tolls_for(drop)
    sr={}
    for sym in t:
        if sym not in d['pairs']: continue
        p=prep(d['pairs'][sym]); sr[sym]={'h':p['h'],'l':p['l'],'c':p['c'],'index':{x:i for i,x in enumerate(p['ts'])}}
    e=[x for x in ents if x[0] in t and x[0] in sr]
    nets,_,_,_=TS.simulate(e,sr,t,1.0,0.2,0.08,0.0)
    a=np.asarray(nets,float); sd=float(a.std(ddof=1)); m=float(a.mean())
    th=2*(0.01*m/100)/(0.01*sd/100)**2
    n=int(len(e)/183.0*365)
    p_block,_,_=BB.simulate_paths(nets,0.01,n,1500,50,seed=11)
    p_iid,d_i,_=BB.simulate_paths(nets,0.01,n,1500,0,seed=11)
    print(f"{label:<22} pairs {len(t):>2} n {len(a):>5} e {m:+.4f}% sd {sd:.4f}% theta {th:>6.0f} | 1%|365d iid {p_iid*100:5.1f}% block50 {p_block*100:5.1f}%")
run((), "table tolls (all 17)")
run(("ONDO",), "table, ONDO excluded")
