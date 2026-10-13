#!/bin/bash
#SBATCH --job-name=mimo-overfit
#SBATCH --account=reformo
#SBATCH --partition=booster
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=288
#SBATCH --exclusive
#SBATCH --time=12:00:00
#SBATCH --output=/e/data1/mmlaion/playground/ruan1/logs/%x_%j.out
#
# MiMo SWE overfit run (Qwen3.6-35B-A3B, SkyRL + SkyCap + mini-swe-agent in Daytona) on JUPITER, N nodes x 4 GH200.
# Port of rl-env-cleaner/skycap_overfit/{env_2node,ray_node,run_overfit_multinode}.sh (GCP, 2x8 B200) to Slurm.
#
# Needs, on a login node, `jobs/relay/login_services.sh start` (chisel tunnel to the GCP relay + HTTPS proxy): compute
# nodes have no internet. Resubmitting the same RUN_NAME resumes from the latest checkpoint (12 h wall-clock limit).
#
#   RUN_NAME=... TASKS=overfit4 GROUP_SIZE=16 TRAIN_PATHS=final LR=5.0e-6 EPOCHS=35 sbatch mimo_overfit.sbatch [hydra overrides]
# Qwen3.5-9B (dense) on 2 nodes:
#   RUN_NAME=... MODEL=$DATA/models/Qwen3.5-9B SERVED_MODEL_NAME=qwen3.5-9b RENDERER=qwen3.5 MEGATRON_TP=2 MEGATRON_EP=1 \
#     TP_SIZE=1 NUM_ENGINES=8 TASKS=overfit16fast GROUP_SIZE=8 sbatch -N 2 mimo_overfit.sbatch
source ~/.bashrc
set -eo pipefail
ulimit -n 524288 2>/dev/null || ulimit -n $(ulimit -Hn)

export RUN_NAME=${RUN_NAME:-qwen36-jupiter-overfit4-g16-final-lr5e-6}
export TASKS_DIR=$DATA/mimo/${TASKS:-overfit4}
export RUN_DIR=/e/scratch/reformo/ruan1/mimo-runs/$RUN_NAME   # ckpts ~450 GB each: scratch, not data1/project1
export MODEL=${MODEL:-$DATA/models/Qwen3.6-35B-A3B}
export LABELED_DAYTONA_DIR=$WORK/rl-env-cleaner/mimo_swe KEYS_FILE=$WORK/charlie_keys.env
export MIMO_TTL_MINUTES=${MIMO_TTL_MINUTES:-240}
REPO=$WORK/SkyRL-overfitting   # CharlieFRuan/SkyRL branch jupiter; .venv -> $DATA/envs/skyrl-mimo
mkdir -p $RUN_DIR/logs

# --- network: outbound through the login-node proxy, inbound through the GCP relay (see jobs/relay/) ---
source ~/.config/jupiter-relay/proxy.env   # HTTPS_PROXY, RELAY_HOST, RELAY_LOGIN_NODE
NODES=($(scontrol show hostnames ${MIMO_NODELIST:-$SLURM_JOB_NODELIST}))  # MIMO_NODELIST: when run as a step inside a held allocation
ips=(); for n in "${NODES[@]}"; do ips+=($(getent ahostsv4 $n.jupiter.internal | awk 'NR==1{print $1}')); done
HEAD_IP=${ips[0]}
export NO_PROXY="localhost,127.0.0.1,.jupiter.internal,$(IFS=,; echo "${ips[*]}")" ; export no_proxy=$NO_PROXY
curl -s -m 20 -o /dev/null -w "proxy check (daytona): %{http_code}\n" https://app.daytona.io/api/health \
  || { echo "outbound proxy on $RELAY_LOGIN_NODE unreachable: run jobs/relay/login_services.sh start" >&2; exit 1; }

# --- offline caches, NCCL over InfiniBand ---
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 UV_OFFLINE=1 LITELLM_LOCAL_MODEL_COST_MAP=True VLLM_NO_USAGE_STATS=1 RAY_USAGE_STATS_ENABLED=0
export VLLM_CACHE_ROOT=$DATA/cache/vllm TMPDIR=/tmp
export NCCL_SOCKET_IFNAME=ib0 NCCL_SOCKET_FAMILY=AF_INET NCCL_IB_TIMEOUT=23 GLOO_SOCKET_IFNAME=ib0 VLLM_FORCE_IPV4=1
export NCCL_DEBUG=WARN SKYRL_LD_LIBRARY_PATH_EXPORT=1 SKYRL_PYTHONPATH_EXPORT=1
export PYTHONPATH=$LABELED_DAYTONA_DIR
export RAY_DEFAULT_OBJECT_STORE_MAX_MEMORY_BYTES=$((32 * 1024 * 1024 * 1024)) RAY_enable_ray_event=0 RAY_task_events_report_interval_ms=0
export SKYRL_RAY_PG_TIMEOUT_IN_S=1800
export PATH=$REPO/.venv/bin:$PATH
RAY_PORT=6390 RAY_TMP=/tmp/ray_$SLURM_JOB_ID
COMMON="--num-gpus=4 --temp-dir=$RAY_TMP --dashboard-agent-listen-port=52370 --metrics-export-port=8095 --runtime-env-agent-port=8096"

echo "nodes: ${NODES[*]} ips: ${ips[*]} head $HEAD_IP relay $RELAY_HOST via $RELAY_LOGIN_NODE"
trap 'mkdir -p $RUN_DIR/ray_logs/$SLURM_JOB_ID; for n in ${NODES[@]}; do srun --overlap -N1 -n1 -w $n --cpu-bind=none bash -c "cp -r $RAY_TMP/session_latest/logs $RUN_DIR/ray_logs/$SLURM_JOB_ID/\$(hostname -s)" 2>/dev/null; done' EXIT

# --- private Ray cluster: head on node 0, workers on the rest (one srun step per node, kept alive with --block) ---
srun --overlap -N1 -n1 -w ${NODES[0]} --cpu-bind=none ray start --head --block --port=$RAY_PORT --include-dashboard=false \
  --ray-client-server-port=9021 --node-ip-address=$HEAD_IP $COMMON > $RUN_DIR/logs/ray_head_$SLURM_JOB_ID.log 2>&1 &
sleep 30
for i in $(seq 1 $((${#NODES[@]} - 1))); do
  srun --overlap -N1 -n1 -w ${NODES[$i]} --cpu-bind=none ray start --block --address=$HEAD_IP:$RAY_PORT \
    --node-ip-address=${ips[$i]} $COMMON > $RUN_DIR/logs/ray_worker${i}_$SLURM_JOB_ID.log 2>&1 &
done
want=$((4 * ${#NODES[@]}))
for t in $(seq 1 60); do
  gpus=$(ray status --address=$HEAD_IP:$RAY_PORT 2>/dev/null | grep -oP '/\K[0-9.]+(?= GPU)' | head -1 || true)
  [[ "${gpus%.*}" == "$want" ]] && break; sleep 10
done
ray status --address=$HEAD_IP:$RAY_PORT | grep -E "GPU|node_" || true
[[ "${gpus%.*}" == "$want" ]] || { echo "Ray cluster has ${gpus:-0} GPUs, want $want" >&2; exit 1; }

# --- driver (on node 0, the batch host) ---
export RAY_ADDRESS=$HEAD_IP:$RAY_PORT USE_VENV=1 NUM_NODES=${#NODES[@]} NUM_GPUS=4
export NUM_ENGINES=${NUM_ENGINES:-$((2 * NUM_NODES))} TP_SIZE=${TP_SIZE:-2}   # 2 vLLM engines x TP 2 per 4-GPU node
export GROUP_SIZE=${GROUP_SIZE:-16} TRAIN_PATHS=${TRAIN_PATHS:-final} LR=${LR:-5.0e-6} EPOCHS=${EPOCHS:-35}
export CKPT_INTERVAL=${CKPT_INTERVAL:-5} SKYCAP_PORT=11500
cd $REPO
# DEBUG_HOLD=1: if the driver fails, keep the allocation (and the Ray cluster) so the run can be fixed and relaunched
# without requeueing: `srun --jobid=$SLURM_JOB_ID --overlap -N1 -n1 -c 288 -w <head node> bash $WORK/jobs/mimo_relaunch.sh <run_dir>`.
env | grep -E "^(RUN_NAME|TASKS_DIR|RUN_DIR|MODEL|SERVED_MODEL_NAME|RENDERER|MEGATRON_[A-Z]+|NUM_[A-Z]+|TP_SIZE|GROUP_SIZE|TRAIN_PATHS|LR|EPOCHS|CKPT_INTERVAL|RAY_ADDRESS|NO_PROXY|USE_VENV|MAX_MODEL_LEN|GPU_MEMORY_UTILIZATION)=" \
  | sed 's/^/export /; s/=\(.*\)$/="\1"/' > $RUN_DIR/logs/driver_env_$SLURM_JOB_ID.sh
rc=0
bash examples/train_integrations/harbor_skycap/run_overfit_qwen36_mini_swe.sh "$@" || rc=$?
if [[ $rc != 0 && "${DEBUG_HOLD:-0}" == 1 ]]; then
  echo "driver failed (rc=$rc); DEBUG_HOLD=1, holding the allocation. env: $RUN_DIR/logs/driver_env_$SLURM_JOB_ID.sh"
  sleep infinity
fi
exit $rc
