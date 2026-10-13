# SkyCap overfit runs on JSC JUPITER (aarch64 GH200, offline compute nodes)

Port of the GCP B200 setup (`../` and `../../cross_cluster_handoff.md`) to JUPITER: Slurm, 4× GH200 (95 GB usable)
per node, aarch64, **no internet on compute nodes**. Code: `CharlieFRuan/SkyRL` branch `jupiter` (on `skycap-mini-swe`).

## Layout on JUPITER (user ruan1)
| What | Where |
|---|---|
| SkyRL (branch `jupiter`) | `$WORK/SkyRL-overfitting`, `.venv` → `$DATA/envs/skyrl-mimo` |
| this repo (branch `jupiter`) | `$WORK/rl-env-cleaner`; `$WORK/jobs/*` are symlinks into this dir |
| Model | `$DATA/models/Qwen3.6-35B-A3B`, `$DATA/models/Qwen3.5-9B` |
| Tasks | `$DATA/mimo/{overfit4,overfit16fast}` (from HF `CharlieFRuan/mimo-v2.6-harbor-tasks`) |
| Runs (ckpts ~450 GB) | `/e/scratch/reformo/ruan1/mimo-runs/<run>` (scratch: purged after 90 days unused) |

## Network (`relay/`)
JSC forbids outgoing SSH and private keys on its storage, so no `ssh -R/-D` tunnels.
- **Outbound** (Daytona API, Daytona's S3 build contexts, W&B): `relay/connect_proxy.py` on a login node's IB address
  (compute nodes reach login nodes over IPoIB). CONNECT-only, Basic auth, host allowlist. Jobs set `HTTPS_PROXY`.
  The Daytona SDK uploads build contexts with obstore, which ignores `HTTPS_PROXY`; `mimo_swe/labeled_daytona.py`
  passes it as `proxy_url`.
- **Inbound** (Daytona sandbox → SkyCap on a compute node): GCP VM `jupiter-skycap-relay` (Caddy, `relay/vm_startup.sh`,
  configured via instance metadata only). A chisel client on the login node dials `wss://tunnel.<relay>` (HTTPS/443) and
  forwards the VM's 127.0.0.1:1080 to `relay/socks_gate.py` on the login node; Caddy routes
  `https://<relay>/skycap/<10.128.x.x>/<11500-11549>/…` through it. The gate admits only nodes of this user's RUNNING
  Slurm jobs (from squeue) and SkyCap ports, so the public relay cannot reach anything else on the JUPITER network.
- Start/stop all three on ONE login node: `relay/login_services.sh start|stop|status` (writes
  `~/.config/jupiter-relay/proxy.env`; `relay_host` and credentials live in that dir, mode 600). Login nodes may reboot: check `status`
  before submitting.

## Env
`build_mimo_env.sh` (login node, CUDA/13 module for the few source builds). SkyRL `jupiter` branch fixes for aarch64/Hopper:
flashinfer markers include aarch64; pip `nvidia-cuda-nvcc` pinned to 13.0 (was 13.4 vs 13.0 runtime headers → every
TileLang/fla JIT compile failed on sm_90).

## Running
```bash
# 35B, 4 nodes: TP4 EP8 + CP2 (CP needed on 95 GB GPUs: 128k-token microbatches OOM otherwise), 2 vLLM engines x TP2 per node
DEBUG_HOLD=1 RUN_NAME=qwen36-jupiter-overfit4-g16-final-lr5e-6 sbatch --time=06:00:00 --time-min=03:00:00 mimo_overfit.sbatch
# 9B, 2 nodes: TP4 (intra-node) x CP2 (across nodes), 4 vLLM engines x TP1 per node
RUN_NAME=... MODEL=$DATA/models/Qwen3.5-9B SERVED_MODEL_NAME=qwen3.5-9b RENDERER=qwen3.5 MEGATRON_TP=4 MEGATRON_CP=2 \
  MEGATRON_EP=1 TP_SIZE=1 NUM_ENGINES=8 TASKS=overfit16fast GROUP_SIZE=8 sbatch -N 2 mimo_overfit.sbatch
```
- `MEGATRON_CP` defaults to 2 in `mimo_overfit.sbatch`.
- Same `RUN_NAME` resumes from the latest checkpoint (`CKPT_INTERVAL`, default 5 here). Max wall-clock is 12 h.
- `DEBUG_HOLD=1`: on driver failure the job keeps its nodes and Ray cluster; fix, then relaunch the driver with
  `srun --jobid=<id> --overlap -N1 -n1 -c 288 -w <head> bash mimo_relaunch.sh <run_dir>`.
- `hold_nodes.sbatch`: an allocation that only sleeps, to `srun` runs into (`MIMO_NODELIST=<nodes> srun ... -c 288 ...
  bash mimo_overfit.sbatch`). **Always pass `-c 288`**: steps default to 1 CPU, and Ray then hangs in `ray.init`.
- Debug helpers (run with `srun --jobid ... --overlap`): `ray_inspect.sh`, `ray_errs.sh`, `ray_logs_grep.sh`,
  `proc_inspect.sh` (py-spy from `uv tool install py-spy`).

## Resuming (e.g. after a maintenance window)
1. On a login node: `relay/login_services.sh status`; if anything is down, `start` (login nodes reboot during
   maintenance; `proxy.env` records which login node the proxy is on, so jobs follow it).
2. Relay VM up? `gcloud compute instances describe jupiter-skycap-relay --zone europe-west3-a --format='value(status)'`
   (`gcloud compute instances start ...` if it was stopped; its IP is static).
3. Resubmit with the same `RUN_NAME` (and the same model/parallelism env); the trainer loads the newest checkpoint
   in `$RUN_DIR/ckpts`. Do not delete `$RUN_DIR/trials` of a run you resume.

## Timings (35B, 4 tasks × 16, 4 nodes)
Engine cold start ~5 min; step 1: generate 924 s, policy_train 482 s, weight sync 14 s (~28 min/step).
