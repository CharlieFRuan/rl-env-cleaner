#!/usr/bin/env bash
# Stop SkyRL runs on this node: wrapper process groups, SkyRL entrypoints, the private Ray cluster. Never matches itself
# because the patterns live in this file, not on a command line.
D=${D:-/mnt/local_storage/$USER}
for p in $(pgrep -f "run_overfit_multinode.sh|run_swebench_eval_mini_swe.sh|run_overfit_qwen36_mini_swe.sh"); do
  [ "$p" = "$$" ] && continue
  g=$(ps -o pgid= -p $p | tr -d " "); [ -n "$g" ] && [ "$g" != "$(ps -o pgid= -p $$ | tr -d ' ')" ] && kill -TERM -$g 2>/dev/null
done
sleep 5
for p in $(pgrep -f "main_harbor_skycap"); do [ "$p" != "$$" ] && kill -9 $p 2>/dev/null; done
PATH=$D/SkyRL-overfitting/.venv/bin:$PATH ray stop --force >/dev/null 2>&1
sleep 3
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do kill -9 $pid 2>/dev/null; done
sleep 2
echo "gpu mem: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader | tr '\n' ' ')"
