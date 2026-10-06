#!/usr/bin/env bash
# Harbor eval of MiMo-V2.6 SWE tasks: mini-swe-agent (in the Daytona sandbox) -> Cloudflare tunnel -> local vLLM Qwen3.8-27B-FP8.
# Usage: run_harbor.sh <task_dir> <job_name> [extra harbor args...]
set -euo pipefail
HERE=/mnt/cluster_storage/charlie/harbor/mimo
TASKS="$1"; JOB="$2"; shift 2
set -a; . /home/ray/default/charlie_keys.env; set +a
export OPENAI_API_BASE="$(cat $HERE/tunnel_url)/v1"
export MSWEA_API_KEY="$(cat $HERE/vllm_api_key)"
exec harbor run -y -p "$TASKS" -o "$HERE/jobs" --job-name "$JOB" \
  -e daytona -n "${N_CONCURRENT:-1}" \
  -a mini-swe-agent -m openai/qwen3.8-27b \
  --ak version=2.4.6 --ak config_file=$HERE/mswea_qwen38.yaml \
  --ae OPENAI_API_KEY="$MSWEA_API_KEY" --ae OPENAI_API_BASE="$OPENAI_API_BASE" \
  "$@"
