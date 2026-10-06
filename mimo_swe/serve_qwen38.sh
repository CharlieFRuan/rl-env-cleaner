#!/usr/bin/env bash
# Qwen3.8-27B-FP8 on one H100 for the MiMo SWE Harbor eval. API key required: it is exposed via a Cloudflare tunnel.
exec /mnt/local_storage/charlie/vllm-env/.venv/bin/vllm serve /mnt/cluster_storage/charlie/models/Qwen3.8-27B-FP8 \
  --served-model-name qwen3.8-27b --port 8011 --host 127.0.0.1 \
  --api-key "$(cat /mnt/cluster_storage/charlie/harbor/mimo/vllm_api_key)" \
  --kv-cache-dtype fp8 --max-model-len 153600 --gpu-memory-utilization 0.90 \
  --language-model-only --enable-prefix-caching --max-num-seqs 16 \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --speculative-config '{"method":"mtp","num_speculative_tokens":3}'
