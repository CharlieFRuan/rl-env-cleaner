#!/usr/bin/env bash
# Harbor eval of MiMo-V2.6 SWE tasks: mini-swe-agent 2.4.6 (in a labeled Daytona sandbox, agent phase
# allowlisted to the model endpoint only) -> Cloudflare tunnel -> affinity router -> 32x vLLM Qwen3.6-35B-A3B BF16.
# Usage: run_harbor.sh <task_dir> <job_name> [extra harbor args...]     (AGENT=nop for null-agent runs)
set -euo pipefail
HERE=/home/charlieruan/mimo
SRC=$HERE/rl-env-cleaner/mimo_swe
TASKS="$1"; JOB="$2"; shift 2
set -a; . /home/charlieruan/charlie_keys.env; set +a
export PATH=$HOME/.local/bin:/usr/local/bin:$PATH PYTHONPATH=$SRC${PYTHONPATH:+:$PYTHONPATH}
export MIMO_RUN_ID="${MIMO_RUN_ID:-mimo-qwen36}"
URL="$(cat $HERE/tunnel_url)"; HOST="${URL#https://}"
COMMON=(-y -p "$TASKS" -o "$HERE/jobs" --job-name "$JOB" -e daytona -n "${N_CONCURRENT:-60}"
        --environment-import-path labeled_daytona:LabeledDaytonaEnv --allow-agent-host "$HOST")
if [ "${AGENT:-mini-swe-agent}" = nop ]; then
  exec harbor run "${COMMON[@]}" -a nop "$@"
fi
KEY="$(cat $HERE/vllm_api_key)"; export MSWEA_API_KEY="$KEY" OPENAI_API_KEY="$KEY" OPENAI_API_BASE="$URL/v1"
exec harbor run "${COMMON[@]}" -k "${K_ATTEMPTS:-4}" \
  -a mini-swe-agent -m openai/qwen3.6-35b-a3b \
  --ak version=2.4.6 --ak config_file=$SRC/mswea_qwen36.yaml \
  --ae OPENAI_API_KEY="$KEY" --ae OPENAI_API_BASE="$URL/v1" --ae LITELLM_LOCAL_MODEL_COST_MAP=True \
  "$@"
