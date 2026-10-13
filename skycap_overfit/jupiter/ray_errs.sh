#!/bin/bash
# usage: srun --jobid=<id> --overlap -N1 -n1 -w <node> bash ray_errs.sh <ray_tmp>
# Shows the tail of non-empty worker stderr logs (secrets filtered).
D=$1/session_latest/logs
for f in $(ls -t $D/worker-*.err 2>/dev/null); do
  if [ -s $f ] && grep -qE "Traceback|Error|error" $f; then echo "=== $f"; grep -vE "API_KEY|TOKEN|SECRET" $f | grep -E "Traceback|Error|error|File \"/e/project1" | tail -15; fi
done | head -80
