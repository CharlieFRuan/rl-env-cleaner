"""Per-task pass rate and trial duration from a Harbor trials dir. usage: task_stats.py <trials_dir> [since_iso]"""
import json, sys, glob, statistics as st
from datetime import datetime
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
since = t(sys.argv[2] + "+00:00") if len(sys.argv) > 2 else None
rows = {}
for f in glob.glob(sys.argv[1] + "/*/result.json"):
    try: r = json.load(open(f))
    except Exception: continue
    if not r.get("finished_at") or not r.get("started_at"): continue
    if since and t(r["started_at"]) < since: continue
    vr = r.get("verifier_result") or {}
    rew = (vr.get("rewards") or {}).get("reward")
    if rew is None: rew = 0.0
    dur = (t(r["finished_at"]) - t(r["started_at"])).total_seconds() / 60
    rows.setdefault(r["task_name"].split("/")[-1], []).append((float(rew), dur, bool(r.get("exception_info"))))
print(f"{'task':45s} {'n':>4} {'pass':>5} {'p50m':>5} {'p90m':>5} {'maxm':>5} {'exc':>4}")
for k, v in sorted(rows.items(), key=lambda kv: -sum(x[0] for x in kv[1]) / len(kv[1])):
    d = sorted(x[1] for x in v)
    print(f"{k:45s} {len(v):4d} {sum(x[0] for x in v)/len(v):5.2f} {st.median(d):5.1f} {d[int(.9*(len(d)-1))]:5.1f} {d[-1]:5.1f} {sum(x[2] for x in v):4d}")
