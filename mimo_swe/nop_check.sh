#!/usr/bin/env bash
# Null-agent check: rerun every solved task (scored waves + gate smoke) with Harbor's `nop` agent.
# A task whose tests pass untouched (reward 1 here) is broken -> appended to broken_nop.txt and excluded.
# Loops until $HERE/STOP exists; each round is a job jobs/nop-rNNN (NOP_N sandboxes, default 4).
set -uo pipefail
HERE=/home/charlieruan/mimo; SRC=$HERE/rl-env-cleaner/mimo_swe
ALL=$HERE/harbor_tasks/mimo-code
cd "$HERE"; mkdir -p waves; touch broken_nop.txt
tid() { sed -E 's#.*/(format-code-task-[0-9]+)__.*#\1#'; }
r=$(ls -d jobs/nop-r* 2>/dev/null | wc -l)
while [ ! -e STOP ]; do
  solved=$(grep -lx 1 jobs/wave-*/*/verifier/reward.txt jobs/gate-smoke-*/*/verifier/reward.txt 2>/dev/null | tid | sort -u)
  checked=$(ls jobs/nop-r*/format-code-task-*/verifier/reward.txt jobs/gate-nop*/format-code-task-*/verifier/reward.txt 2>/dev/null | tid | sort -u)
  todo=$(comm -23 <(echo "$solved" | grep .) <(echo "$checked" | grep .) | head -40)
  if [ -z "$todo" ]; then sleep 300; continue; fi
  r=$((r + 1)); name=$(printf "nop-r%03d" $r); dir=waves/$name; mkdir -p "$dir"
  for t in $todo; do ln -sfn "$ALL/$t" "$dir/$t"; done
  echo "$(date -Is) start $name: $(echo $todo | wc -w) tasks"
  AGENT=nop N_CONCURRENT=${NOP_N:-4} MIMO_RUN_ID=nop $SRC/run_harbor.sh "$dir" "$name" -r 2 \
    --retry-include SandboxBuildFailedError --retry-include EnvironmentStartTimeoutError > logs/$name.log 2>&1
  bad=$(grep -lx 1 jobs/$name/*/verifier/reward.txt 2>/dev/null | tid)
  [ -n "$bad" ] && echo "$bad" | sed 's/$/  # passes with nop agent/' >> broken_nop.txt
  echo "$(date -Is) done $name: $(ls jobs/$name/*/verifier/reward.txt 2>/dev/null | wc -l) graded; PASS-WITHOUT-CHANGES: ${bad:-none}"
done
