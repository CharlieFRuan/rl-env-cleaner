#!/usr/bin/env bash
# Every 10 min: endpoint health (auto-restart router/tunnel), failed-build cleanup, status snapshot.
HERE=/home/charlieruan/mimo; SRC=$HERE/rl-env-cleaner/mimo_swe; cd $HERE
PY=$HOME/.local/share/uv/tools/harbor/bin/python
set -a; . /home/charlieruan/charlie_keys.env; set +a
while [ ! -e STOP ]; do
  { echo "=== $(date -Is)"
    $SRC/endpoint_health.sh && echo "endpoint ok" || echo "ENDPOINT DOWN"
    $PY $SRC/cleanup_failed_builds.py 2>/dev/null
    $PY $SRC/sandbox_janitor.py --alert-above 110 2>/dev/null | tail -1
    ./status.sh 6
  } >> logs/maintenance.log 2>&1
  sleep 600
done
