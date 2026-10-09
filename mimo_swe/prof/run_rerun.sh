#!/usr/bin/env bash
# Follow-up rerun of flagged tasks from the profiling pass: prof/run_rerun.sh cpu4|mem16
#   cpu4:  CPU-flagged tasks (verifier timeout / >600 s / throttled+slow) at 4 vCPU, 8 GiB  -> jobs/prof-cpu4
#   mem16: tasks with any OOM kill at 2 vCPU, 16 GiB                                         -> jobs/prof-mem16
# Task list prof/order_$1.txt, template prof/trial_template_$1.json, knobs prof/$1.conf. Stop launching: touch prof/STOP_$1.
N=$1; HERE=/home/charlieruan/mimo; cd $HERE
set -a; . /home/charlieruan/charlie_keys.env; set +a
export PATH=$HOME/.local/bin:/usr/local/bin:$PATH PYTHONPATH=$HERE/rl-env-cleaner/mimo_swe
export MIMO_RUN_ID=prof MIMO_PROFILE=1 MIMO_JOB=prof-$N MIMO_JOBS_GLOB="jobs/prof-$N" MIMO_ORDER=$HERE/prof/order_$N.txt \
       MIMO_TEMPLATE=$HERE/prof/trial_template_$N.json MIMO_CONF=$HERE/prof/$N.conf MIMO_URL_FILE=$HERE/prof/relay_url \
       MIMO_EXCLUDED=$HERE/prof/excluded_$N.txt MIMO_STOP=$HERE/prof/STOP_$N MIMO_HEALTH_CMD=$HERE/prof/health.sh MIMO_POOL_WORKERS=600
exec 9>$HERE/prof/runner_$N.lock; flock -n 9 || { echo "another $N runner holds the lock; exiting"; exit 1; }
exec $HOME/.local/share/uv/tools/harbor/bin/python $HERE/rl-env-cleaner/mimo_swe/continuous_runner.py
