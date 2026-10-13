#!/bin/bash
#SBATCH --job-name=skyrl-gsm8k-dummy
#SBATCH --account=reformo
#SBATCH --partition=booster
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=288
#SBATCH --time=01:00:00
#SBATCH --output=/e/data1/mmlaion/playground/ruan1/logs/%x_%j.out

# Smoke test: SkyRL GRPO, Qwen2.5-0.5B-Instruct, 640 GSM8K prompts (10 steps), 1 node / 4x GH200.
# Compute nodes have no internet: everything below must already be downloaded on a login node.
source ~/.bashrc
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 UV_OFFLINE=1
# W&B offline on the compute node; sync from a login node afterwards: wandb sync $OUT/wandb/offline-run-*
source $WORK/charlie_keys.env
export WANDB_MODE=offline
PY=$DATA/envs/skyrl/bin/python
D=$DATA/datasets/gsm8k
OUT=$FSCRATCH/skyrl-gsm8k-dummy/$SLURM_JOB_ID
mkdir -p $OUT
export WANDB_DIR=$OUT
# Ray writes engine/worker logs under /tmp (RAM disk, gone with the node): keep a copy.
trap 'cp -r /tmp/ray/session_latest/logs $OUT/ray_logs 2>/dev/null' EXIT
cd $WORK/SkyRL
hostname; nvidia-smi -L

$PY -m skyrl.train.entrypoints.main_base \
  data.train_data="['$D/train_640.parquet']" \
  data.val_data="['$D/validation_128.parquet']" \
  trainer.algorithm.advantage_estimator=grpo \
  trainer.policy.model.path=Qwen/Qwen2.5-0.5B-Instruct \
  trainer.placement.colocate_all=true \
  trainer.strategy=fsdp \
  trainer.placement.policy_num_gpus_per_node=4 \
  trainer.placement.critic_num_gpus_per_node=4 \
  trainer.placement.ref_num_gpus_per_node=4 \
  generator.inference_engine.num_engines=4 \
  generator.inference_engine.tensor_parallel_size=1 \
  trainer.epochs=1 \
  trainer.eval_batch_size=128 \
  trainer.eval_before_train=true \
  trainer.eval_interval=5 \
  trainer.update_epochs_per_batch=1 \
  trainer.train_batch_size=64 \
  trainer.policy_mini_batch_size=32 \
  trainer.micro_forward_batch_size_per_gpu=16 \
  trainer.micro_train_batch_size_per_gpu=16 \
  trainer.ckpt_interval=1000 \
  trainer.max_prompt_length=512 \
  generator.sampling_params.max_generate_length=512 \
  trainer.policy.optimizer_config.lr=1.0e-6 \
  trainer.algorithm.use_kl_loss=true \
  generator.inference_engine.backend=vllm \
  generator.inference_engine.run_engines_locally=true \
  generator.inference_engine.weight_sync_backend=nccl \
  generator.batched=true \
  environment.env_class=gsm8k \
  generator.n_samples_per_prompt=4 \
  generator.inference_engine.gpu_memory_utilization=0.7 \
  trainer.logger=wandb \
  trainer.project_name=gsm8k \
  trainer.run_name=gsm8k_dummy_$SLURM_JOB_ID \
  trainer.resume_mode=null \
  trainer.log_path=$OUT/logs \
  trainer.ckpt_path=$OUT/ckpts \
  trainer.export_path=$OUT/exports
