"""Prove the shadow recorder on real cycles: seed the gate journal's real vetoes,
resolve them against real 5m closes, then show it wrote nothing else."""
import hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import ccxt, near_miss as nm

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else "missing"
acc, watch = sha(ROOT/"contest_account.json"), sha(ROOT/"contest_watch.json")
added = nm.seed_from_gate_log(ROOT/"contest_gate_log.jsonl")
print(f"seeded {added} near-miss row(s) from the gate journal's real vetoes")
ex = ccxt.kraken({"enableRateLimit": True})
done = nm.resolve(ex, None, limit=20)
rows = [json.loads(l) for l in nm.PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
res = [r for r in rows if r.get("resolved")]
print(f"rows {len(rows)} | resolved now {done} | total resolved {len(res)}")
for r in res[-4:]:
    print(f"  {r['pair']:9s} eq={r['entry_quality']} fwd={r['fwd_pct']:+.3f}% mae={r['mae_pct']:+.3f}% stop={r['hit_hard_stop']} bars={r.get('bars')} ts={r['ts'][11:19]}")
print("account", acc == sha(ROOT/"contest_account.json"), "watch", watch == sha(ROOT/"contest_watch.json"))
