#!/bin/bash
# Build the MiMo (megatron + harbor + skycap) env on a JUPITER login node (aarch64, needs internet).
# Source-built packages (no aarch64 wheels) are compiled against the CUDA/13 module for sm_90 (GH200).
set -eo pipefail
source ~/.bashrc
module load Stages/2026 CUDA/13
export CUDA_HOME=$EBROOTCUDA TORCH_CUDA_ARCH_LIST="9.0" MAX_JOBS=${MAX_JOBS:-32} NVCC_THREADS=4
export TMPDIR=$DATA/tmp; mkdir -p $TMPDIR
cd $WORK/SkyRL-overfitting
UV_PROJECT_ENVIRONMENT=$DATA/envs/skyrl-mimo uv sync --extra megatron --extra harbor --extra skycap --python 3.12 -v 2>&1 | grep -v "^DEBUG" | tail -60
$DATA/envs/skyrl-mimo/bin/python -c "import vllm, ray, torch, megatron.core, transformer_engine.pytorch, harbor, skycap, flashinfer; print('vllm', vllm.__version__, 'ray', ray.__version__, 'torch', torch.__version__)"
echo SETUP_DONE
