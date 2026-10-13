#!/bin/bash
#SBATCH --job-name=hold-debug
#SBATCH --account=reformo
#SBATCH --partition=booster
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=288
#SBATCH --exclusive
#SBATCH --output=/e/data1/mmlaion/playground/ruan1/logs/%x_%j.out
# Holds nodes for interactive debugging: `srun --jobid=<id> --overlap -w <node> ...` into it, or run
# `MIMO_NODELIST=<nodes> srun --jobid=<id> --overlap -N1 -n1 -c 288 -w <first node> bash mimo_overfit.sbatch` (-c: steps default to 1 CPU, which starves Ray) to launch a run inside it.
scontrol show hostnames $SLURM_JOB_NODELIST
sleep infinity
