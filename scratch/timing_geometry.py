"""The geometry of an 80% win rate: what win% is reachable while still net-positive?"""
import itertools, json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from timing_engine_fast import prep, rolling_mean, rolling_min, stats, COST, NOV1
from entry_edge_study import load_tape

def run(data, dip_bars, target, stop, timeout, filt):
    rows=[]
    for sym,(ts,h,l,c) in data.items():
        m48=rolling_mean(c,48); m288=rolling_mean(c,288)
        low_n=rolling_min(l,dip_bars+1)
        up=(c>m48)&(c>m288)
        dip=c<=low_n*1.0015
        turn=np.r_[False,c[1:]>c[:-1]]
        mask=up&dip&turn
        if filt=="momentum24":
            mask &= (c/np.roll(c,288)>1.01)
        elif filt=="deep_dip":
            mask &= (c/low_n-1.0 < 0.004)
        idx=np.where(mask)[0]; idx=idx[idx>300]
        tgt=1+target/100; stp=1-stop/100
        last=-1
        for i in idx:
            if i<=last: continue
            e=c[i]; j0=i+1; j1=min(i+1+timeout,len(c)-1)
            if j0>=len(c)-1: break
            sh=h[j0:j1+1]; sl=l[j0:j1+1]
            ht=np.where(sh>=e*tgt)[0]; hs=np.where(sl<=e*stp)[0]
            ft=ht[0] if len(ht) else 10**9; fs=hs[0] if len(hs) else 10**9
            if ft<=fs and ft<10**9: pct,k=target-COST,j0+ft
            elif fs<10**9: pct,k=-stop-COST,j0+fs
            else: k=j1; pct=(c[k]/e-1)*100-COST
            rows.append((ts[i],pct,(ts[k]-ts[i])/60)); last=k
    rows.sort(); return rows

data=prep(load_tape()); print("symbols",len(data))
print(f"  {'filt':<10}{'dip':>4}{'tgt':>6}{'stop':>6}{'bars':>5} | {'n':>5}{'win%':>7}{'net':>9}{'avg':>8}{'DD':>8} | Nov n  Nov win%   Nov net")
out={}
for filt,(dip,target,stop,timeout) in itertools.product(("none","momentum24","deep_dip"),
        itertools.product((2,3),(0.15,0.20,0.25,0.30),(1.0,1.5,2.0),(12,24))):
    rows=run(data,dip,target,stop,timeout,filt)
    tr=[r for r in rows if r[0]<NOV1]; nv=[r for r in rows if r[0]>=NOV1]
    a,at,an=stats(rows),stats(tr),stats(nv)
    k=f"{filt}_d{dip}_t{target:g}_s{stop:g}_k{timeout}"
    out[k]={"whole":a,"train":at,"nov":an,"filt":filt,"dip":dip,"target":target,"stop":stop,"timeout":timeout}
    print(f"  {filt:<10}{dip:>4}{target:>6.2f}{stop:>6.1f}{timeout:>5} | {a['n']:>5}{a['win_pct']:>7.1f}{a['net']:>+9.1f}{a['avg']:>+8.3f}{a['max_dd']:>8.1f} | {an['n']:>5}  {an['win_pct']:>7.1f}  {an['net']:>+8.1f}")
pos={k:v for k,v in out.items() if v["train"]["net"]>0 and v["nov"]["net"]>0}
print(f"\n  net-positive on BOTH train and Nov: {len(pos)}")
for k,v in sorted(pos.items(), key=lambda kv:-kv[1]["whole"]["win_pct"])[:8]:
    print(f"    {k:<34} win {v['whole']['win_pct']:>5.1f}%  net {v['whole']['net']:>+8.1f}  Nov {v['nov']['net']:>+7.1f} ({v['nov']['win_pct']:.1f}%)")
best80=[k for k,v in out.items() if v["whole"]["win_pct"]>=80]
print(f"  configs at >=80% wins: {len(best80)}")
json.dump({"results":out,"positive_both":sorted(pos)}, open("scratch/_timing_geometry.json","w"), indent=1)
print("wrote scratch/_timing_geometry.json")
