#!/usr/bin/env bash
# Keep running smoke batches of random, not-yet-run MiMo SWE tasks until $HERE/STOP exists.
# Each batch is its own Harbor job (jobs/smoke-bNNN). Waits for any running harbor job first.
#   BATCH=10 N_CONCURRENT=1 setsid nohup ./smoke_loop.sh > smoke_loop.log 2>&1 &
set -uo pipefail
HERE=/mnt/cluster_storage/charlie/harbor/mimo
ALL=/mnt/cluster_storage/charlie/datasets/harbor_tasks/mimo-code
BATCH="${BATCH:-10}"
cd "$HERE"
while pgrep -f "harbor ru[n] -y" >/dev/null; do sleep 30; done
b=$(ls -d jobs/smoke-b* 2>/dev/null | wc -l)
while [ ! -e STOP ]; do
  # model + tunnel must be up, or every trial would just fail
  if ! curl -sf -m 20 -H "Authorization: Bearer $(cat vllm_api_key)" "$(cat tunnel_url)/v1/models" >/dev/null; then
    echo "$(date) endpoint down (vLLM or tunnel); stopping"; exit 1
  fi
  b=$((b + 1)); name=$(printf "smoke-b%03d" $b); dir=tasks_$name
  # tasks already tried: graded anywhere, or attempted in a smoke-b* batch (errors are reviewed, not retried)
  done_ids=$( (ls -d jobs/*/format-code-task-*/verifier/reward.txt jobs/smoke-b*/format-code-task-* 2>/dev/null) | sed -E 's#.*/(format-code-task-[0-9]+)__.*#\1#' | sort -u)
  mkdir -p "$dir"
  ls "$ALL" | grep -vxF -f <(echo "$done_ids"; echo NONE) | shuf -n "$BATCH" --random-source=<(yes $b) \
    | while read -r t; do ln -sfn "$ALL/$t" "$dir/$t"; done
  [ -z "$(ls "$dir")" ] && { echo "$(date) no tasks left"; exit 0; }
  echo "$(date) start $name: $(ls "$dir" | tr '\n' ' ')"
  ./run_harbor.sh "$dir" "$name" > "$name.log" 2>&1
  echo "$(date) done $name (exit $?): $(python3 -c "
import json,glob
r=[open(f).read().strip() for f in glob.glob('jobs/$name/*/verifier/reward.txt')]
s=json.load(open('jobs/$name/result.json'))['stats']
print(f'{sum(x==\"1\" for x in r)}/{len(r)} solved, {s[\"n_errored_trials\"]} errored')" 2>&1)"
done
echo "$(date) STOP file found; exiting"
