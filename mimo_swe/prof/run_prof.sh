#!/usr/bin/env bash
# Launch the continuous runner for the resource-profiling pass (2 vCPU / 8 GiB / 20 GiB for every task, 4 attempts,
# sampler on). Trials: jobs/prof-r1/. Knobs: prof/prof.conf. Stop launching: touch prof/STOP.
HERE=/home/charlieruan/mimo; cd $HERE
set -a; . /home/charlieruan/charlie_keys.env; set +a
export PATH=$HOME/.local/bin:/usr/local/bin:$PATH PYTHONPATH=$HERE/rl-env-cleaner/mimo_swe
export MIMO_RUN_ID=prof MIMO_PROFILE=1 MIMO_JOB=prof-r1 MIMO_JOBS_GLOB="jobs/prof-r1" \
       MIMO_TEMPLATE=$HERE/prof/trial_template_prof.json MIMO_CONF=$HERE/prof/prof.conf MIMO_URL_FILE=$HERE/prof/relay_url \
       MIMO_EXCLUDED=$HERE/prof/excluded_infra.txt MIMO_STOP=$HERE/prof/STOP MIMO_HEALTH_CMD=$HERE/prof/health.sh MIMO_POOL_WORKERS=600
exec 9>$HERE/prof/runner.lock; flock -n 9 || { echo "another profiling runner holds prof/runner.lock; exiting"; exit 1; }
exec $HOME/.local/share/uv/tools/harbor/bin/python $HERE/rl-env-cleaner/mimo_swe/continuous_runner.py
