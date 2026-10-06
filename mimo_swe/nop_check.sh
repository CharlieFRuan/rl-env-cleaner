#!/usr/bin/env bash
# Null-agent check: rerun every solved smoke task with Harbor's `nop` agent (no changes to the repo).
# A task whose tests pass untouched (reward 1 here) is broken: it rewards doing nothing.
# Loops until $HERE/STOP exists; each round is a job jobs/nop-rNNN.
set -uo pipefail
HERE=/mnt/cluster_storage/charlie/harbor/mimo
ALL=/mnt/cluster_storage/charlie/datasets/harbor_tasks/mimo-code
cd "$HERE"
set -a; . /home/ray/default/charlie_keys.env; set +a
r=$(ls -d jobs/nop-r* 2>/dev/null | wc -l)
while [ ! -e STOP ]; do
  solved=$(grep -lx 1 jobs/smoke-b*/*/verifier/reward.txt 2>/dev/null | sed -E 's#.*/(format-code-task-[0-9]+)__.*#\1#' | sort -u)
  checked=$(ls -d jobs/nop-r*/format-code-task-* 2>/dev/null | sed -E 's#.*/(format-code-task-[0-9]+)__.*#\1#' | sort -u)
  todo=$(comm -23 <(echo "$solved" | grep .) <(echo "$checked" | grep .))
  if [ -z "$todo" ]; then sleep 300; continue; fi
  r=$((r + 1)); name=$(printf "nop-r%03d" $r); dir=tasks_$name; mkdir -p "$dir"
  for t in $todo; do ln -sfn "$ALL/$t" "$dir/$t"; done
  echo "$(date) start $name: $(echo $todo | wc -w) tasks"
  harbor run -y -p "$dir" -o "$HERE/jobs" --job-name "$name" -e daytona -n 4 -a nop > "$name.log" 2>&1
  bad=$(grep -lx 1 jobs/$name/*/verifier/reward.txt 2>/dev/null | sed -E 's#.*/(format-code-task-[0-9]+)__.*#\1#')
  echo "$(date) done $name: $(ls jobs/$name/*/verifier/reward.txt 2>/dev/null | wc -l) graded; PASS-WITHOUT-CHANGES: ${bad:-none}"
done
