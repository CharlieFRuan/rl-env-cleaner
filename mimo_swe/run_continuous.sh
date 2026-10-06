#!/usr/bin/env bash
# Launch the continuous scheduler with credentials and PYTHONPATH set.  Log: logs/continuous.log
HERE=/home/charlieruan/mimo; cd $HERE
set -a; . /home/charlieruan/charlie_keys.env; set +a
export PATH=$HOME/.local/bin:/usr/local/bin:$PATH PYTHONPATH=$HERE/rl-env-cleaner/mimo_swe MIMO_RUN_ID=scored
exec $HOME/.local/share/uv/tools/harbor/bin/python $HERE/run_bin/continuous_runner.py
