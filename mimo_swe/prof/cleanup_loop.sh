#!/usr/bin/env bash
# Profiling pass: every 10 min delete OUR BUILD_FAILED / stale ERROR sandboxes and log a sandbox count. Stops on prof/STOP.
HERE=/home/charlieruan/mimo; cd $HERE
set -a; . /home/charlieruan/charlie_keys.env; set +a
PY=$HOME/.local/share/uv/tools/harbor/bin/python
while [ ! -e prof/STOP ]; do
  { echo "=== $(date -Is)"; $PY rl-env-cleaner/mimo_swe/cleanup_failed_builds.py 2>/dev/null; $PY rl-env-cleaner/mimo_swe/sandbox_janitor.py --run prof --alert-above 600 2>/dev/null | tail -1; } >> logs/prof_cleanup.log 2>&1
  sleep 600
done
