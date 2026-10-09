#!/bin/bash
# One-shot health snapshot of the profiling pass (jobs/prof-r1) for the periodic check-ins.
cd /home/charlieruan/mimo
date; echo "conf: $(tr '\n' ' ' < prof/prof.conf)  runner pids: $(pgrep -f "[c]ontinuous_runner.py" | tr '\n' ' ')"
grep reconcile logs/prof_runner.log | tail -1
echo "outcomes: $(grep DONE logs/prof_runner.log | awk '{print $4}' | sort | uniq -c | tr '\n' ' ')  circuit_breaker: $(grep -c CIRCUIT logs/prof_runner.log)"
echo "infra (last 5):"; grep "DONE.*infra" logs/prof_runner.log | tail -5 | cut -c1-180
echo "--- last 15 min trials (LLM/tool latency, exceptions)"; python3 /home/charlieruan/mimo/rl-env-cleaner/mimo_swe/prof/llm_health.py $(date -d '-15 min' +%s) 2>&1 | grep -vE "LLM calls >300s"
python3 - <<'PY'
import json,glob,time
from datetime import datetime as D
p=lambda x:D.fromisoformat(x.replace('Z','+00:00')).timestamp()
e=[];oom=0
for f in glob.glob('jobs/prof-r1/*/result.json'):
    import os
    if os.path.getmtime(f)<time.time()-900: continue
    r=json.load(open(f)); es=r.get('environment_setup') or {}
    if es.get('finished_at'): e.append(p(es['finished_at'])-p(es['started_at']))
    try:
        if 'oom_kill 0 ' not in open(os.path.dirname(f)+'/verifier/test-stdout.txt').read(): oom+=1
    except Exception: pass
e.sort()
if e: print(f"env setup (finished last 15m) n={len(e)} p50={e[len(e)//2]:.0f}s p90={e[int(.9*(len(e)-1))]:.0f}s max={e[-1]:.0f}s; trials with oom_kill>0: {oom}")
PY
bash /home/charlieruan/mimo/rl-env-cleaner/mimo_swe/prof/real_conc.sh
echo "--- relay + engines"
timeout 90 gcloud compute ssh mimo-relay --zone us-west3-c --command 'uptime; echo "conns443: $(ss -tn state established "( sport = :443 )" | wc -l)  router_errors_15m: $(sudo journalctl -u mimo-router --since -15min | grep -ciE "error|exception")"; r=0; w=0; for b in $(cat /opt/mimo-relay/backends.txt); do m=$(curl -s --max-time 5 $b/metrics); r=$((r+$(echo "$m" | awk "/^vllm:num_requests_running/{s+=\$2}END{printf \"%d\",s}"))); w=$((w+$(echo "$m" | awk "/^vllm:num_requests_waiting/{s+=\$2}END{printf \"%d\",s}"))); done; echo "engines running=$r waiting=$w"' 2>&1 | grep -vE "^(Updating|Warning|External IP)" | tail -3
