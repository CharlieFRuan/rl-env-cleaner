"""Mean verifier reward per consecutive block of N finished trials (by start time). usage: step_rewards.py <trials_dir> <N>"""
import glob, json, sys
rs = []
for f in glob.glob(sys.argv[1] + "/*/result.json"):
    try: r = json.load(open(f))
    except Exception: continue
    rs.append((r.get("started_at") or "", ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward") or 0.0))
rs.sort(); n = int(sys.argv[2])
print(" ".join(f"{sum(x[1] for x in rs[i:i+n])/len(rs[i:i+n]):.3f}" for i in range(0, len(rs), n)), f"({len(rs)} trials)")
