#!/bin/bash
# usage: srun --jobid=<id> --overlap -N1 -n1 -w <node> bash proc_inspect.sh <pattern> <ray_tmp>
source ~/.bashrc
PAT=$1; RT=$2
PIDS=$(pgrep -u $USER -f "$PAT" | grep -vx $$)
for p in $PIDS; do echo "== $p $(ps -o etime=,args= -p $p | cut -c1-120)"; $HOME/.local/bin/py-spy dump --pid $p 2>&1 | head -40; done
echo "== runtime_env logs"; ls $RT/session_latest/logs 2>/dev/null | head -50; tail -n 15 $RT/session_latest/logs/runtime_env_agent.log 2>/dev/null
