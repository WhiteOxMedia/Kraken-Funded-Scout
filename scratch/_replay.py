import sys, json, math
sys.path.insert(0,'.'); sys.path.insert(0,'scratch')
import numpy as np
import scratch.exit_take_sweep as TS
import scratch.block_bootstrap as BB
A,B = BB.A, BB.B
entries, sr, tolls = BB.series_and_entries()
res={}
for label,cfg in (("tight",(1.0,0.2,0.08,0.0)),("mid",(1.5,0.2,0.08,0.0)),("ship",(2.0,0.4,0.15,0.0))):
    nets,_t,_h,_c = TS.simulate(entries, sr, tolls, *cfg)
    a=np.asarray(nets,float)
    out={}
    for s in (0.01,0.02,0.05):
        L=np.cumsum(np.log1p(s*a/100.0))
        cross_p = int(np.argmax(L>=A)) if (L>=A).any() else -1
        cross_d = int(np.argmax(L<=-B)) if (L<=-B).any() else -1
        out[f"{s*100:g}%"]={"maxlog":round(float(L[-1]),4),"maxtrough":round(float(np.min(np.minimum.accumulate(L))),4),
                            "pass_at":cross_p,"death_at":cross_d,
                            "verdict":("PASS" if cross_p>=0 and (cross_d<0 or cross_p<cross_d) else
                                       "DEATH" if cross_d>=0 else "ALIVE")}
    res[label]=out
    print(label, out)
json.dump(res, open("scratch/_replay.json","w"), indent=1)
