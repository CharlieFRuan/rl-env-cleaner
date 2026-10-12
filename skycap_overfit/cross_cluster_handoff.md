# Cross-cluster handoff: Qwen3.6-35B-A3B overfit runs with SkyRL + SkyCap + mini-swe-agent on MiMo tasks

Everything another cluster needs to reproduce, or continue, the overfitting runs done on the GCP B200 cluster
(2026-10-09 to 2026-10-11). Written 2026-10-12.

## 0. TL;DR

1. Get the code and data:
   - Code: `CharlieFRuan/SkyRL`, branch `skycap-mini-swe`.
   - Glue: `CharlieFRuan/rl-env-cleaner`, branch `skycap-overfit` (this file is in it).
   - Data: Hugging Face dataset `CharlieFRuan/mimo-v2.6-harbor-tasks` (private).
   - Model: `Qwen/Qwen3.6-35B-A3B`.
2. Build one non-isolated `.venv` per node, at the same path on every node.
3. Make the SkyCap servers on the training nodes reachable over HTTPS from Daytona sandboxes. We used a small public relay
   VM running Caddy (§4).
4. Start a private Ray cluster across the nodes, then run the driver on the head node (§5).
5. Watch W&B (project `skycap-overfit`) and the run log (§6).

## 1. What the run is

- **Training:**
  - Synchronous GRPO on a handful of MiMo SWE tasks, deliberately overfit to check that the RL stack learns.
  - Model: Qwen3.6-35B-A3B (MoE, BF16), trained with Megatron.
  - vLLM engines are colocated with the trainer on the same GPUs.
- **Rollouts:**
  - Each rollout is a mini-swe-agent 2.4.6 run inside a Daytona sandbox, built from the task's Docker image.
  - The agent calls the model through **SkyCap** (token mode). SkyCap renders each prompt, gets tokens from the SkyRL
    vLLM engines, and records the exact token graph of the trajectory, so training sees exactly what was sampled.
  - SkyCap was checked against stock Harbor + vLLM: the scores match (`PARITY_SWEBENCH.md` in the SkyRL branch).
- **Reward:** the task verifier's 0/1 result (hidden tests, restored from the base ref before running).

## 2. Artifacts

| What | Where |
|---|---|
| SkyRL + SkyCap code | https://github.com/CharlieFRuan/SkyRL/tree/skycap-mini-swe (head `87ec05cc`, on NovaSky-AI/SkyRL main `b8963321`; SkyCap is vendored in `skycap/`) |
| Recipe | `examples/train_integrations/harbor_skycap/run_overfit_qwen36_mini_swe.sh` (all knobs are env vars; read its header) |
| Agent config | `examples/train_integrations/harbor_skycap/mini_swe_agent_qwen36_train.yaml` |
| Sandbox env class | `mimo_swe/labeled_daytona.py` in rl-env-cleaner (`LabeledDaytonaEnv`: labels, TTL, agent-phase allowlist) |
| Multi-node glue and analysis | `skycap_overfit/` in rl-env-cleaner, branch `skycap-overfit` (table below) |
| Tasks + overfit subsets + per-task stats | HF dataset `CharlieFRuan/mimo-v2.6-harbor-tasks` (**private**; read its README) |
| Model | `Qwen/Qwen3.6-35B-A3B` on Hugging Face (BF16, about 67 GB) |
| Past runs | W&B `sky-posttraining-uc-berkeley/skycap-overfit` (§8 has run links) |
| Background | `PARITY_SWEBENCH.md` (SkyRL branch); `mimo_swe/HANDOFF.md`, `RESIDUE_AUDIT.md`, `sandbox_provision_pass.md` (rl-env-cleaner) |

**`skycap_overfit/` scripts**

| Script | What it does |
|---|---|
| `env_2node.sh` | Common env: `D` (work dir), caches, `.venv` on PATH, NCCL, `ulimit -n`, `PYTHONPATH` to `mimo_swe/`, Ray port 6390 |
| `setup_venv_multinode.sh` | `uv sync --extra megatron --extra harbor --extra skycap` in `$D/SkyRL-overfitting`, plus an import check |
| `ray_node.sh head <ip>` / `worker <ip> <head_ip>` / `stop` | Private Ray cluster from the `.venv`; `stop` also kills anything left on the GPUs |
| `run_overfit_multinode.sh <head_ip> <tasks_dir> <run_name> [overrides]` | Driver; runs the recipe with `NUM_NODES` (default 2), `USE_VENV=1` and the relay |
| `stop_runs.sh` | Stops the driver, the entrypoint, Ray and GPU processes on one node |
| `setup_skycap_relay.sh` | Installs Caddy on the relay VM and routes `/skycap/<node-ip>/<port>` to the nodes |
| `pick_fast_tasks.py` | Ranks tasks for overfitting: about half pass in eval and profiling, fast, 2 vCPU, no flags (`MIMO_DATA=<hf dir>`) |
| `collapse.py`, `masked_breakdown.py` | Per-run W&B signals: reward, entropy, tokens, context-limit exits; masked groups by cause |
| `step_rewards.py`, `task_stats.py` | Per-step and per-task reward and duration from a run's `trials/` dir |
| `sb_ps.py`, `sb_del.py` | Inspect or delete a wedged Daytona sandbox (`sb_del` only deletes sandboxes labelled owner=charlieruan) |

## 3. Prerequisites

- **GPUs:**
  - Runs used 2 nodes × 8 B200 (180 GB) per run; the first 16-task run used 1 node.
  - The recipe is Megatron TP 4 / EP 8, optimizer offloaded to CPU, 4 vLLM engines × TP 2 per node at
    `gpu_memory_utilization` 0.7, `max_model_len` 128000.
  - On 80 GB GPUs (H100) this won't fit as is. Expect to need more nodes or other parallelism.
- **Shared layout:**
  - The same work dir `D` on every node (default `/mnt/local_storage/$USER`), holding `SkyRL-overfitting/`,
    `mimo_swe/`, `hf/Qwen3.6-35B-A3B/`, the task dirs, `runs/` and `logs/`.
  - Checkpoints go to `$D/runs/<run>/ckpts` on each node's **local** disk. A full checkpoint is about 450 GB.
- **Tools:** `uv`, git, CUDA drivers that work with vLLM 0.30 (the version in SkyRL's lock), and outbound internet
  (Hugging Face, PyPI, GitHub, Daytona API, W&B).
- **Keys** in a keys file (`KEYS_FILE`, sourced by the recipe; never commit it):
  - `DAYTONA_API_KEY`: the Daytona org. On GCP, our share was about 700 sandboxes of 1 vCPU / 4 GiB / 10 GiB.
    - A 16 × 8 batch is 128 sandboxes at once, mostly 2 vCPU / 4 GiB.
    - Daytona caps a sandbox at 16 GB of memory and 20 GB of disk.
  - `WANDB_API_KEY`: the logger is wandb when it's set.
  - `HF_TOKEN`: the dataset is private.
- **Sandbox → model connectivity, the critical part:**
  - Daytona sandboxes run in Daytona's cloud and must reach the SkyCap servers on the training nodes over the internet.
  - The tasks' agent phase uses an **allowlist**, so only the endpoint host is reachable; the generator adds it
    automatically.
  - Options, in `skycap.exposure.*`:
    - **Relay (what we used):** `RelayPathExposure`, set by `run_overfit_multinode.sh` through `RELAY_HOST`. One public
      HTTPS host proxies `/skycap/<node-ip>/<port>/…` to `<node-ip>:<port>` over the private network. §4 has the setup.
    - **`external_host`:** if the nodes have public IPs with ports 11500–11549 open. Edit the overrides in the recipe:
      `skycap.exposure.type=external_host skycap.exposure.kwargs.host=<public ip> skycap.exposure.kwargs.port=11500`.
      One host only, so a single-node run.
    - **`cloudflare`** quick tunnels: `skycap.exposure.type=cloudflare`. These were less reliable for us.

## 4. One-time setup

```bash
# On every node (same D everywhere)
export D=/mnt/local_storage/$USER; mkdir -p $D/{logs,runs,tmp,hf}; cd $D
git clone -b skycap-mini-swe https://github.com/CharlieFRuan/SkyRL.git SkyRL-overfitting
git clone -b skycap-overfit https://github.com/CharlieFRuan/rl-env-cleaner.git
cp -r rl-env-cleaner/mimo_swe $D/mimo_swe        # PYTHONPATH for labeled_daytona.py
cp rl-env-cleaner/skycap_overfit/*.sh $D/         # env_2node.sh must sit next to ray_node.sh / run_overfit_multinode.sh
bash $D/setup_venv_multinode.sh                   # long: builds the venv; ends with SETUP_DONE

# Model, on every node (or a shared FS)
HF_TOKEN=... $D/SkyRL-overfitting/.venv/bin/hf download Qwen/Qwen3.6-35B-A3B --local-dir $D/hf/Qwen3.6-35B-A3B

# Tasks, on every node
HF_TOKEN=... $D/SkyRL-overfitting/.venv/bin/hf download CharlieFRuan/mimo-v2.6-harbor-tasks --repo-type dataset --local-dir $D/mimo-data
tar -xzf $D/mimo-data/mimo-code.tar.gz -C $D/mimo-data
for s in overfit4 overfit16fast; do mkdir -p $D/$s; for t in $(cat $D/mimo-data/subsets/$s.txt); do cp -r $D/mimo-data/mimo-code/$t $D/$s/; done; done
```

**Adapt to your cluster:**
- `env_2node.sh`:
  - `NCCL_IFACE`: the primary NIC name; see `ip -br addr`.
  - Set `KEEP_NCCL_ENV=1` if your IB NCCL works with vLLM's NCCL 2.29.
- `mimo_swe/labeled_daytona.py`: `OWNER = "charlieruan"` labels every sandbox. Change it if you share the Daytona org,
  and use the same value in `sb_del.py`.

**Relay VM**, if you use the relay:
- Any small VM with a public IP in the same VPC as the training nodes.
- Open ports 80 and 443 to the internet. Allow 11500–11549 from the VM to the nodes.
- Use `<public-ip-with-dashes>.sslip.io` as the host name, so Caddy can get a Let's Encrypt certificate without DNS
  setup.

```bash
NODE_IP_REGEX='10\.1\.2\.(?:11|12)' bash setup_skycap_relay.sh 1-2-3-4.sslip.io   # the training nodes' internal IPs
curl -sk https://1-2-3-4.sslip.io/skycap/10.1.2.11/11500/ -o /dev/null -w '%{http_code}\n'   # 502 until a run is up
```

Add **every** node of the run to the regex: SkyCap servers can land on any node.

## 5. Launching a run

```bash
# Ray (from the head node; use your own ssh). Redirect output and use < /dev/null, or ssh can hang on `ray start`.
ssh head   "bash $D/stop_runs.sh; bash $D/ray_node.sh stop; bash $D/ray_node.sh head HEAD_IP > $D/logs/ray_head.log 2>&1 < /dev/null"
ssh worker "bash $D/ray_node.sh stop; bash $D/ray_node.sh worker WORKER_IP HEAD_IP > $D/logs/ray_worker.log 2>&1 < /dev/null"
ssh head   "source $D/env_2node.sh; ray status --address=HEAD_IP:6390 | grep GPU"     # expect 16.0 GPU

# Driver, on the head. Resumes from the latest checkpoint in $D/runs/<run>/ckpts (RESUME_MODE=latest).
cd $D && RELAY_HOST=1-2-3-4.sslip.io KEYS_FILE=$HOME/keys.env \
  TRAIN_PATHS=final GROUP_SIZE=8 LR=5.0e-6 EPOCHS=35 \
  setsid nohup ./run_overfit_multinode.sh HEAD_IP $D/overfit16fast qwen36-overfit16fast-g8-finalpath-lr5e-6 \
  >> logs/overfit16fast.log 2>&1 < /dev/null &
```

- **Batch:** `train_batch_size` = `policy_mini_batch_size` = the number of tasks in `TASKS_DIR`. So one step is one epoch
  (all tasks × `GROUP_SIZE` trials), and `EPOCHS` is the number of steps.
- **Recipe knobs** (env vars):
  - `TRAIN_PATHS`: `all` or `final`.
  - `GROUP_SIZE`; `LR` (default 1e-6); `EPOCHS` (default 50).
  - `CKPT_INTERVAL`: default 10; the epoch-end checkpoint is off, keep 2.
  - `R3=1`: rollout routing replay.
  - `MAX_MODEL_LEN` (128000); `MODEL`; `NUM_ENGINES` (4 per node).
  - Extra Hydra overrides go after the run name, e.g. `generator.apply_overlong_filtering=false`.
- **Single node:** start only the Ray head and run the driver with `NUM_NODES=1` (4 engines). Put only that node in
  the relay regex.
- **Time per step** (2 nodes):
  - 4 tasks × 16: 15–23 min.
  - 16 fast tasks × 8: about 21 min.
  - The first 16-task set (slower tasks) on 1 node: about 41 min.
  - Each step's rollout phase waits for the slowest trial.
- **Stop a run:** `bash $D/stop_runs.sh` on the head, then `ray_node.sh stop` on every node.

## 6. Monitoring

- **W&B metrics:**
  - Health:
    - `reward/avg_raw_reward`: the mean trajectory reward.
    - `policy/policy_entropy`: a fall toward 0.05 meant collapse.
    - `generate/avg_num_tokens`.
  - SkyCap generation:
    - `generate/skycap/trajectories_context_length_exceeded`.
    - `generate/skycap/num_masked_instances`: groups dropped.
    - `generate/skycap/num_timeout_trajectories`.
  - Harbor failures: `generate/harbor/num_failed_attempts/<Error>`.
  - Logprob mismatch: `minibatch_rollout_logprobs_abs_diff_mean`, the trainer vs rollout gap. About 0.006 with R3 and
    0.011–0.017 without.
  - Quick summaries: `python collapse.py <run>…` and `python masked_breakdown.py <run>…`, with the repo `.venv` python
    and `WANDB_API_KEY` set.
- **Files:**
  - `$D/logs/<log>.log`: the driver log; grep `Finished: 'policy_train'` to count steps.
  - `$D/runs/<run>/trials/<trial>/`: each Harbor trial (`result.json`, `agent/` trajectory, verifier output).
    - **Each trial's `config.json` holds the per-run vLLM API key. Don't share these.**
  - `$D/runs/<run>/skycap/`: SkyCap records (token graphs).
  - `python step_rewards.py $D/runs/<run>/trials <tasks×group>` and `python task_stats.py $D/runs/<run>/trials`.
- **Wedged Daytona sandbox:**
  - Symptoms: the step stops advancing, and an unfinished trial's `trial.log` has been silent for more than 45 minutes.
  - Cause: the agent fails fast, then the verifier exec hangs.
  - Fix: find the sandbox with `mimo_swe/sandbox_janitor.py --run <run> -v`, then `python sb_del.py <sandbox id>`. The
    generator retries the trial.
  - The backstop is `SKYCAP_TRIAL_TIMEOUT_SEC=7200` per attempt.

## 7. Semantics worth knowing before changing anything

- **Limits** per trial:
  - Agent timeout: **1200 s**, from `harbor_trial_config.agent.override_timeout_sec` in
    `examples/train_integrations/harbor/harbor_trial_config/default.yaml`. It overrides the task's 3600 s.
  - Verifier timeout: from the task (1800 s).
  - mini-swe-agent: `step_limit` 250, 16,384 output tokens per turn, 600 s per bash command.
  - Context: 128,000 tokens.
- **Sampling:** temperature 1.0, top_p 1.0, top_k −1. Thinking is kept across turns (`preserve_thinking`,
  `thinking_retention=all`).
- **Algorithm:** GRPO with std normalization, token_mean loss, eps clip 0.2 / 0.28, no KL, per-token TIS cap 2.
  Weight decay 0.01, grad clip 1.0.
- **`skycap.train_paths`:**
  - `all` trains every root-to-leaf path of the trajectory graph.
  - `final` trains only the path to the last call. Branches the harness discarded aren't trained: mini-swe-agent drops
    malformed replies ("No tool calls found") from its history, so under `final` **they're never penalized**.
- **Masking** (`examples/train_integrations/harbor_skycap/harbor_generator.py` `_attempt`, `compose.py`):
  - Agent timeout, or an error after retries: the **whole group of that task** is masked (`num_masked_instances`). In
    every run so far, all masked groups came from agent timeouts.
  - Context limit:
    - Kept as `stop_reason=context_length`.
    - With `generator.apply_overlong_filtering=true` (the recipe default), its loss mask is zeroed, so **running into
      the context limit is never penalized**.
  - `NonZeroAgentExitCodeError` with a verifier result: trained with the verifier's reward.
- **Two verifier holes**, fixed in the dataset (originals in `verifier_fixes/`):
  - 001471: the verifier ignored failing asserts.
  - 001775: a stale GOPATH copy.
  - Reward hacking was checked by reading traces in the overfit16, overfit4 and overfit16fast runs; no deliberate hacking
    was found.

## 8. Results so far (GCP, 2 nodes per run)

| Run (W&B) | Steps | Steps 1–3 | Peak (step) | Last 5 | Entropy first → last | Tokens/traj | Ctx-limit exits, last 5 steps |
|---|---|---|---|---|---|---|---|
| [4 tasks ×16, final path, lr 1e-6](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/8arpmq6z) | 30 | 0.41 | 0.78 (29) | 0.73 | 0.23 → 0.24 | 69k → 66k | 0 |
| [4 ×16, all paths, lr 1e-6](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/bed481kr) | 30 | 0.47 | 0.80 (30) | 0.70 | 0.23 → 0.22 | 69k → 63k | 0 |
| [4 ×16, final, lr 5e-6](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/e939ah4g) | 34 | 0.40 | 0.80 (20) | 0.59 | 0.23 → 0.18 | 67k → 68k | 0–2 |
| [same, rerun](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/evstgyyg) | 35 | 0.46 | 0.80 (14) | 0.49 | 0.24 → 0.14 | 66k → 70k (peak 121k) | 0–3 |
| [same + R3 (stopped)](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/flwlwm8g) | 24 | 0.45 | 0.84 (13) | 0.49 | 0.24 → 0.04 | 69k → 122k | 6 → 18 |
| [16 fast ×8, final, lr 5e-6](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/02dwfjb3) | 30 | 0.59 | 0.93 (13) | 0.84 | 0.26 → 0.38 | 27k → 52k | 0–2 |
| [16 fast ×8, all paths, lr 5e-6](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/vci3ga5w) | 35 | 0.60 | 0.98 (21) | 0.87 | 0.25 → 0.13 | 26k → 100k | 27–34 of 128 |

The first 16-task set on 1 node at lr 1e-6 ([ohm8trpo](https://wandb.ai/sky-posttraining-uc-berkeley/skycap-overfit/runs/ohm8trpo))
rose only 0.21 → about 0.36–0.40 in 19 steps.

**Takeaways:**
1. The stack learns: every run climbs well above its start. The 16 fast tasks reach 0.93–0.98.
2. At lr 5e-6 the 4-task runs peak at about 0.80, then degrade, with or without R3.
   - Failure modes: broken tool calls, and/or looping into the context limit with entropy falling.
   - lr 1e-6 is stable but slow.
3. R3 halves the logprob gap but doesn't prevent the collapse, so the train/inference mismatch isn't the main cause.
4. A rerun matches its original for about 12 steps; the later failure mode differs.
5. With all paths, episodes still grow (26k → 100k tokens).
6. Likely contributors:
   - Unpenalized context-limit exits (overlong filtering).
   - Unpenalized malformed replies (final path).
   - Whole groups masked by a single timeout. Tasks 000949, 001123 and 001650 time out the most; 001650 is not actually
     fast and should be dropped.

**Suggested next experiments (none run yet):**
- `generator.apply_overlong_filtering=false`, so context-limit trajectories train with reward 0.
- Mask only the timed-out trial instead of its group, or train it with reward 0 (code change in `compose.py`).
- 4 tasks × 16 with all paths at lr 5e-6; an lr between 1e-6 and 5e-6 (e.g. 2e-6).
- Replace 001650 in `overfit16fast` (`pick_fast_tasks.py` lists alternatives).

## 9. Gotchas (all hit on GCP)

- **NCCL:**
  - The GCP gIB NCCL plugin broke vLLM's NCCL 2.29 init ("invalid usage"). `env_2node.sh` forces socket NCCL.
  - `ulimit -n` must be high (524288). With 1024, the first optimizer step failed with NCCL "Too many open files".
- **Ray:**
  - Its client port must be outside the worker port range (we use 9021).
  - Start Ray from the repo `.venv` (`USE_VENV=1`), so workers on every node use the same interpreter.
- **Killing processes:** never `pkill -f` or `pgrep -f` with a pattern that also appears in your own ssh command line; it
  kills your shell. Use `stop_runs.sh`, which keeps its patterns in the file.
- **FlashInfer cache:**
  - A run killed mid-download can corrupt it. Fix: `rm -rf ~/.cache/flashinfer/<ver>/<arch>/generated/trtllm_export`.
  - Serve vLLM only from SkyRL's locked env, which includes the flashinfer jit-cache wheels. Otherwise the MoE kernels
    JIT-compile for over 20 minutes.
- **Harbor:** stock `harbor run` (used only for no-SkyCap parity baselines) needs `OPENAI_API_KEY` in the host env for
  mini-swe-agent, not only `--ae`. Training doesn't: the SkyCap generator sets the agent's per-trajectory key itself.
- **Checkpoints:**
  - Multi-node checkpoints are split across the nodes' local disks. Resuming needs each node's `ckpts/` intact.
  - A resume on a different node pair was never tested.
- **Shell:** `${VAR:?message with an apostrophe}` breaks bash parsing.
