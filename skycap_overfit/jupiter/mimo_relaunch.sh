#!/bin/bash
# Relaunch the driver inside a held mimo_overfit.sbatch allocation (DEBUG_HOLD=1), reusing its Ray cluster and env.
#   srun --jobid=<id> --overlap -N1 -n1 -c 288 -w <head node> bash $WORK/jobs/mimo_relaunch.sh <run_dir> [hydra overrides]
source ~/.bashrc
source ~/.config/jupiter-relay/proxy.env
set -a; source $WORK/charlie_keys.env; set +a
source $1/logs/driver_env_$SLURM_JOB_ID.sh; shift
ulimit -n 524288 2>/dev/null || true
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 UV_OFFLINE=1 LITELLM_LOCAL_MODEL_COST_MAP=True VLLM_NO_USAGE_STATS=1 RAY_USAGE_STATS_ENABLED=0 VLLM_CACHE_ROOT=$DATA/cache/vllm TMPDIR=/tmp
export NCCL_SOCKET_IFNAME=ib0 NCCL_SOCKET_FAMILY=AF_INET NCCL_IB_TIMEOUT=23 GLOO_SOCKET_IFNAME=ib0 VLLM_FORCE_IPV4=1
export NCCL_DEBUG=WARN SKYRL_LD_LIBRARY_PATH_EXPORT=1 SKYRL_PYTHONPATH_EXPORT=1 no_proxy=$NO_PROXY
export LABELED_DAYTONA_DIR=$WORK/rl-env-cleaner/mimo_swe KEYS_FILE=$WORK/charlie_keys.env PYTHONPATH=$WORK/rl-env-cleaner/mimo_swe
export RAY_DEFAULT_OBJECT_STORE_MAX_MEMORY_BYTES=$((32 * 1024 * 1024 * 1024)) RAY_enable_ray_event=0 RAY_task_events_report_interval_ms=0
export SKYRL_RAY_PG_TIMEOUT_IN_S=1800 MIMO_TTL_MINUTES=${MIMO_TTL_MINUTES:-240} SKYCAP_PORT=11500
export PATH=$WORK/SkyRL-overfitting/.venv/bin:$PATH
cd $WORK/SkyRL-overfitting
bash examples/train_integrations/harbor_skycap/run_overfit_qwen36_mini_swe.sh "$@"
