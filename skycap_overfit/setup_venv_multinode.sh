#!/usr/bin/env bash
# Build the non-isolated SkyRL .venv (megatron + harbor + skycap) used by the 2-node runs (driver and Ray workers share it).
set -euo pipefail
D=${D:-/mnt/local_storage/$USER}
export UV_CACHE_DIR=$D/uvcache UV_PYTHON_INSTALL_DIR=$D/uv-python TMPDIR=$D/tmp
cd $D/SkyRL-overfitting  # clone of CharlieFRuan/SkyRL branch skycap-mini-swe
uv sync --extra megatron --extra harbor --extra skycap 2>&1 | tail -3
.venv/bin/python -c "import vllm, ray, torch, megatron.core, harbor, skycap; print('vllm', vllm.__version__, 'ray', ray.__version__, 'torch', torch.__version__)"
echo SETUP_DONE
