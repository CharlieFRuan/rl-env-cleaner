# Common env for multi-node overfit runs. Sourced by ray_node.sh and run_overfit_multinode.sh; Ray workers inherit it from
# the raylet. Override D (work dir with SkyRL-overfitting/, mimo_swe/ and caches) and NCCL_IFACE per cluster.
D=${D:-/mnt/local_storage/$USER}
ulimit -n 524288  # NCCL socket transport opens many sockets; ssh shells default to 1024 ("Too many open files")
export UV_CACHE_DIR=$D/uvcache UV_PYTHON_INSTALL_DIR=$D/uv-python VLLM_CACHE_ROOT=$D/vllm-cache HF_HOME=$D/hf-home TMPDIR=$D/tmp
export PATH=$D/SkyRL-overfitting/.venv/bin:/usr/local/bin:/usr/local/cuda/bin:$PATH
# GCP B200 cluster: cross-node NCCL over TCP sockets on the primary NIC, because the gIB plugin (NCCL 2.27) failed in vLLM's
# NCCL 2.29 init ("invalid usage"). Only Megatron's data-parallel all-reduce crosses nodes, once per step, so sockets are
# fast enough. On a cluster with working IB/RoCE NCCL, set KEEP_NCCL_ENV=1 to leave the NCCL env alone.
if [ "${KEEP_NCCL_ENV:-0}" != 1 ]; then
  unset $(env | grep -o "^NCCL_[A-Z_]*") 2>/dev/null
  export LD_LIBRARY_PATH=$(echo "${LD_LIBRARY_PATH:-}" | tr ":" "\n" | grep -v -i -E "gib|nccl" | paste -sd:)
  export NCCL_NET=Socket NCCL_SOCKET_IFNAME=${NCCL_IFACE:-enp0s19}
fi
export NCCL_DEBUG=WARN SKYRL_LD_LIBRARY_PATH_EXPORT=1 SKYRL_PYTHONPATH_EXPORT=1
export PYTHONPATH=$D/mimo_swe  # labeled_daytona.py (this repo's mimo_swe/)
export RAY_DEFAULT_OBJECT_STORE_MAX_MEMORY_BYTES=$((32 * 1024 * 1024 * 1024)) RAY_enable_ray_event=0 RAY_task_events_report_interval_ms=0
export SKYRL_RAY_PG_TIMEOUT_IN_S=1800
RAY_PORT=${RAY_PORT:-6390}
RAY_TMP=$D/tmp/ray2n
