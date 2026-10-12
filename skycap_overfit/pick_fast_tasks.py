"""Rank MiMo tasks for an overfit run: about half solved in eval (c=1..3 of 4) and in profiling, fast, 2 vCPU, no leak/heavy flags.
usage: MIMO_DATA=<hf download dir of CharlieFRuan/mimo-v2.6-harbor-tasks, tarball extracted> python pick_fast_tasks.py [--all]"""
import csv, json, os, re, statistics as st
from collections import defaultdict
from datetime import datetime
M = os.environ.get("MIMO_DATA", "mimo-data")
t = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
ev = defaultdict(list)
for l in open(f"{M}/stats/eval_attempts_qwen36.jsonl"):
    r = json.loads(l)
    if r["validity"] != "valid": continue
    ev[r["task"]].append(r)
bad = set()
for f in ["leak_flagged.txt", "heavy_ids.txt", "excluded_infra.txt", "broken_nop.txt"]:
    try: bad |= {re.split(r"\s", x.strip())[0] for x in open(f"{M}/stats/{f}") if x.strip() and not x.startswith("#")}
    except FileNotFoundError: pass
prof = {r["task"]: r for r in csv.DictReader(open(f"{M}/stats/profile_tasks.csv"))}
ptr = defaultdict(list)
for r in csv.DictReader(open(f"{M}/stats/profile_trials.csv")):
    if r["status"] == "valid": ptr[r["task"]].append(float(r["reward"] or 0))
rows = []
for k, v in ev.items():
    v = sorted(v, key=lambda r: r["started_at"])[:4]
    if len(v) < 4 or k in bad or any(r["leak_flags"] for r in v) or any(r["review_flagged"] for r in v): continue
    c = sum(r["reward"] for r in v)
    if not 1 <= c <= 3: continue
    p = prof.get(k)
    if not p or k not in ptr: continue
    pc = sum(ptr[k]) / len(ptr[k])
    if not 0.2 <= pc <= 0.8: continue
    if int(p["oom_attempts"]) or int(p["verifier_timeouts"]): continue
    toml = open(f"{M}/mimo-code/{k}/task.toml").read()
    cpus = int(re.search(r"^cpus = (\d+)", toml, re.M).group(1))
    if cpus > 2: continue
    dur = max((t(r["finished_at"]) - t(r["started_at"])).total_seconds() for r in v)
    steps = max(r["steps"] for r in v)
    peak = max(r["input_tokens"] / max(r["steps"], 1) for r in v)  # rough average context per call
    cost = max(dur, float(p["max_agent_s"])) + float(p["max_verifier_s"]) + float(p["max_env_setup_s"])
    rows.append((cost, k, c, round(pc, 2), round(dur), round(float(p["max_agent_s"])), round(float(p["max_verifier_s"])), round(float(p["max_env_setup_s"])), steps, round(peak / 1000)))
rows.sort()
print("cost task evalc profpass eval_max_s prof_agent_s verif_s setup_s max_steps avgctx_k")
import sys
for r in (rows if "--all" in sys.argv else rows[:40]): print(*[round(r[0])] + list(r[1:]))
print(len(rows), "candidates")
