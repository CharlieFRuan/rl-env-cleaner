#!/usr/bin/env bash
# usage: ray_node.sh head <my_ip> | worker <my_ip> <head_ip> | stop
# Private Ray cluster from SkyRL's .venv (port $RAY_PORT, default 6390; distinct agent ports so it can sit next to another
# Ray instance on the node). Run from a non-interactive ssh with output redirected and < /dev/null, or ssh can hang.
source "$(dirname "${BASH_SOURCE[0]}")/env_2node.sh"
mkdir -p $RAY_TMP
COMMON=(--node-ip-address=$2 --num-gpus=8 --temp-dir=$RAY_TMP --dashboard-agent-listen-port=52370 --metrics-export-port=8095 --runtime-env-agent-port=8096)
case $1 in
  head) ray start --head --port=$RAY_PORT --include-dashboard=false --ray-client-server-port=9021 "${COMMON[@]}" ;;
  worker) ray start --address=$3:$RAY_PORT "${COMMON[@]}" ;;
  stop) for p in $(pgrep -f "$RAY_TMP"); do [ "$p" != "$$" ] && kill -9 $p 2>/dev/null; done; sleep 2
        for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do kill -9 $pid 2>/dev/null; done ;;
esac
