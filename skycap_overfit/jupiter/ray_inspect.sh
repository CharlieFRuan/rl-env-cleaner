#!/bin/bash
# usage (inside a job): srun --jobid=<id> --overlap -N1 -n1 -w <head> bash ray_inspect.sh <ray_address>
source ~/.bashrc
export PATH=$WORK/SkyRL-overfitting/.venv/bin:$PATH
ray status --address=$1 2>&1 | grep -vE "^\s*$" | head -30
echo "--- pending/alive actors"
RAY_ADDRESS=$1 ray list actors --filter "state!=DEAD" --limit 50 2>&1 | head -40
