#!/usr/bin/env bash
# Driver for a multi-node overfit run; run on the Ray head after ray_node.sh head (and worker on the other nodes).
# usage: run_overfit_multinode.sh <head_ip> <tasks_dir> <run_name> [extra hydra overrides...]
# env: RELAY_HOST (required), NUM_NODES (2), KEYS_FILE (DAYTONA_API_KEY, WANDB_API_KEY), and the recipe's knobs:
#      TRAIN_PATHS (all|final), GROUP_SIZE, LR, EPOCHS, R3, CKPT_INTERVAL, MODEL, RESUME_MODE (latest)
source "$(dirname "${BASH_SOURCE[0]}")/env_2node.sh"
export RAY_ADDRESS=$1:$RAY_PORT NUM_NODES=${NUM_NODES:-2} USE_VENV=1
export RELAY_HOST=${RELAY_HOST:?set RELAY_HOST to the relay host name} SKYCAP_PORT=${SKYCAP_PORT:-11500}
export TASKS_DIR=$2 RUN_NAME=$3 RUN_DIR=$D/runs/$3 LABELED_DAYTONA_DIR=$D/mimo_swe KEYS_FILE=${KEYS_FILE:-$HOME/keys.env}
export MODEL=${MODEL:-$D/hf/Qwen3.6-35B-A3B} MIMO_TTL_MINUTES=${MIMO_TTL_MINUTES:-240}
shift 3
cd $D/SkyRL-overfitting
exec bash examples/train_integrations/harbor_skycap/run_overfit_qwen36_mini_swe.sh "$@"
