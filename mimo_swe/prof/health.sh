#!/usr/bin/env bash
# Profiling pass endpoint check: relay (Caddy TLS) -> router -> vLLM, with the API key.
curl -sf -m 30 -H "Authorization: Bearer $(cat /home/charlieruan/mimo/vllm_api_key)" "$(cat /home/charlieruan/mimo/prof/relay_url)/v1/models" >/dev/null
