#!/bin/bash
#SBATCH --job-name=vllm-qwen36-smoke
#SBATCH --account=reformo
#SBATCH --partition=booster
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:40:00
#SBATCH --output=/e/data1/mmlaion/playground/ruan1/logs/%x_%j.out
# vLLM (MiMo env) serving Qwen3.6-35B-A3B with the overfit recipe's engine settings, offline, on one GH200 pair.
source ~/.bashrc
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_CACHE_ROOT=$DATA/cache/vllm VLLM_FORCE_IPV4=1
cd /tmp
# vLLM spawns its engine process, which re-imports __main__: the script must be a real file with a main guard.
cat > /tmp/smoke.py <<'PY'
import time
from vllm import LLM, SamplingParams

def main():
  t = time.time()
  llm = LLM(model="/e/data1/mmlaion/playground/ruan1/models/Qwen3.6-35B-A3B", tensor_parallel_size=2,
            gpu_memory_utilization=0.7, max_model_len=128000, gdn_prefill_backend="triton", language_model_only=True)
  print(f"ENGINE_UP in {time.time()-t:.0f}s", flush=True)
  msgs = [[{"role": "user", "content": "What is 17*23? Answer briefly."}]] * 8
  t = time.time()
  outs = llm.chat(msgs, SamplingParams(temperature=1.0, max_tokens=1024))
  n = sum(len(o.outputs[0].token_ids) for o in outs)
  print(f"GEN_OK {n} tokens in {time.time()-t:.1f}s; sample: {outs[0].outputs[0].text[-200:]!r}", flush=True)

if __name__ == "__main__":
  main()
PY
CUDA_VISIBLE_DEVICES=0,1 $DATA/envs/skyrl-mimo/bin/python /tmp/smoke.py
