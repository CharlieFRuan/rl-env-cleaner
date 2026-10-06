# Handoff: MiMo-V2.6-RL-oss SWE tasks on Harbor + mini-swe-agent: pass@1 / pass@4 for Qwen3.6-35B-A3B

You are taking over an evaluation pipeline that was prototyped on another cluster. Read this whole document before acting. **Correctness beats coverage.** A large number built on leaky or broken environments is worthless. The previous run turned out to be heavily contaminated by answer leakage, so the anti-leak work in §4 is mandatory before any scored run.

Supporting files (converter, run scripts, audit tools, full findings log) are in the private repo **`https://github.com/CharlieFRuan/rl-env-cleaner`**, folder **`mimo_swe/`**. Clone it into a working dir. File list in §11.

---

## 1. Deliverable

- **Model:** `Qwen/Qwen3.6-35B-A3B`, **BF16** (no FP8 weights or KV cache), served by vLLM on this cluster (4 nodes × 8 B200).
- **Agent / harness:** stock **Harbor** CLI + stock **mini-swe-agent 2.4.6** (Harbor's built-in `mini-swe-agent` agent), sandboxes on **Daytona**.
- **Data:** the SWE (`code`) split of `XiaomiMiMo/MiMo-V2.6-RL-oss` (2,698 tasks). Ignore the other splits (cyber, general, webdev, music).
- **Output:** a report with **pass@1 and pass@4**, using n = 4 attempts per task (`harbor run -k 4`):
  - pass@1 = mean over tasks of c/4, where c = number of solved attempts for the task;
  - pass@4 = fraction of tasks with c ≥ 1;
  - report the number of tasks, 95% bootstrap CIs, and infra-error / excluded counts.

  Only count tasks whose 4 attempts all finished with a valid verdict (see §8 for retries). Also write per-attempt results as JSONL: task, attempt, reward, error class, steps, tokens, and leak flags.
- **Constraints:** at most **60 concurrent Daytona sandboxes**. Get through as many tasks as possible overnight, but correctness first.

---

## 2. Data → Harbor tasks (already solved; reuse the converter)

Source: `https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss`, file `code.parquet` (13 MB, top level). You only need that one file; the whole repo is ~8 GB. Unauthenticated HF downloads get rate-limited, so set `HF_TOKEN` if you have one.

Each row's `extra_info.instance_json` (a JSON string) has:
- `docker_image` (tag name; the image is `xiaomimimo/mimo-v2.6-rl-oss:<tag>` on Docker Hub, 0.3–13.7 GB compressed, median ~3 GB);
- `cwd` (`/testbed` or `/workspace/repo`);
- `problem_statement`;
- `test_patch` (hidden tests **plus** the verifier script `mimo_test_command.sh`, sometimes plus a base64 `mimo_build_env.tar.gz.b64`);
- `test_command`;
- `verifier_timeout_sec`.

Convert:
```bash
uv run --with pandas --with pyarrow python mimo_to_harbor.py code.parquet harbor_tasks/mimo-code   # ~seconds; writes 2,698 task dirs
#   --ids a,b,c   --limit N   --agent-timeout 3600   --cpus 4 --memory-mb 8192 --storage-mb 10240
```
Per task it writes:
- `instruction.md`: the problem statement plus "The repository is at `<cwd>`".
- `task.toml`: resources and timeouts.
- `environment/Dockerfile`: `FROM` the image, then runs `mimo_setup.sh` at build.
- `environment/mimo_setup.sh`.
- `tests/test.patch`: verbatim.
- `tests/test.sh`: the verifier.

**Grading copies MiMo's own `OpenSourceCodeEnvironment`** (MiMo-Agent, `environments/datasets/opensource_code.py`):
1. Reset every file `test.patch` touches to the base commit (recorded at build time in `/etc/mimo_base_ref`), deleting files that didn't exist in the base.
2. `git apply` the patch.
3. Run `{ test_command; } </dev/null`.
4. Reward is 1 if it exits 0, otherwise 0.

There is **no reference solution** in the dataset, so Harbor's `oracle` agent can't validate tasks. Use the null-agent check in §7 instead.

### Fixes already in the converter (each one was a real failure that hid real results)

| Problem | Symptom | Fix (in `mimo_setup.sh` / `test.sh`) |
|---|---|---|
| Images point apt at Xiaomi's internal `apt.sys.srv` (+ `xiaomi.sources`) | agent install fails, exit 100 | rewrite to archive.ubuntu.com / deb.debian.org, drop xiaomi.sources |
| Debian bullseye past end of LTS (Aug 2026) | 404 from security pool | EOL Debian → archive.debian.org + `Check-Valid-Until false` |
| Broken third-party apt repos (e.g. ClickHouse, NO_PUBKEY) | `apt-get update` fails → exit 100 | build-time `apt-get update`; failing `sources.list.d` entries renamed `*.disabled` |
| **Images were flattened, so their ENV is lost** | Go not on PATH; `pytest: command not found` (project `.venv`, pyenv) | `/etc/profile.d/00-mimo-toolchains.sh` (+ `BASH_ENV`) restores go/cargo/java/venv/pyenv/conda/nvm/sdkman/… |
| uv installer skips `~/.local/bin/env` if `~/.local/bin` is already on PATH | Harbor's mini-swe-agent install fails | keep `~/.local/bin` off the image PATH |
| Harbor's Daytona verifier gives the script a TTY | `pnpm install` waits forever on an interactive prompt (>1 h) | test command runs with `</dev/null`, like MiMo (`tty=False, stdin=False`) |
| `test_command` sometimes is `A && B` | redirect would only cover B | brace group `{ …; } </dev/null` |
| **Stale `/logs/verifier/reward.json` baked into an image** (MiMo's own LLM-judge output) | Harbor prefers `reward.json` over `reward.txt`, so it would read a false reward | build: `rm -rf /logs/verifier /logs/agent`; test.sh: `rm -f /logs/verifier/reward.json` |
| 4 GB memory too small | the verifier itself was OOM-killed (exit 137) | 8 GB (= MiMo's pod memory limit) |
| mini-swe-agent 2.4.6 ignores `MSWEA_API_KEY` for `openai/` models | "Missing credentials" | pass `--ae OPENAI_API_KEY=… --ae OPENAI_API_BASE=…` |

---

## 3. What the previous run found (Qwen3.8-27B-FP8, ~260 trials): read `FINDINGS.md`

- Raw solve rate was ~64%, but **at least 26% of solves were answer leakage via the internet** (details in §4). Treat the raw number as invalid.
- **No test tampering** that affected grading. Agents did delete their own scratch tests, but patch-touched files are reset before grading.
- **Context overflow in ~9% of trials** (at 153k context, after 55–148 steps). Cause: Qwen's chat template defaults to `preserve_thinking=true` (keeps every past `<think>`), and mini-swe-agent re-sends `reasoning_content` each turn. With B200 memory you can afford the model's native **262,144** context; use it. vLLM rejects a request once prompt + `max_tokens` exceeds `max-model-len`.
- **The agent can be OOM-killed** (~1%, full Jest/monorepo test runs). Harbor's installed agents run inside the sandbox, so the OOM killer can take the agent process with the command. Count these as failed attempts, not infra errors.
- Many "suspicious" verifier outputs were false positives of regex audits. Always read the actual output before concluding.

---

## 4. MANDATORY before any scored run: answer-leak prevention (match MiMo's own defenses)

### 4.1 Evidence
Sandboxes had open internet. Agents search GitHub for the issue, find the PR or commit that fixed it, and apply it. Patterns seen:
- `curl …/pull/N.diff && git apply`;
- `git clone` upstream, then `git show <fix>`;
- `curl api.github.com/…/commits/<sha>`;
- `pip download pkg==next`, `npm pack pkg@next`, `go mod download mod@next`, then `cp` the fixed file.

Of 45 trials that fetched upstream code, a per-trajectory review found **37 copied the fix and 1 consulted it**. Agents often confirmed "IDENTICAL TO UPSTREAM" by blob hash. One more trial restored the answer from **local git history** (`git show <future-sha>:file`). Image forensics (§4.3 item 2) shows future commits are present in most images.

### 4.2 What MiMo itself does (verify in `github.com/XiaomiMiMo/verl`, submodule `third_party/mimoagent-osr` = `github.com/XiaomiMiMo/MiMo-Agent`)
- **The agent runs outside the sandbox.** Commands go through Kubernetes exec (`recipes/code/mimoagent_runner.py`, `environments/kubernetes.py`). Pod limits are 4 CPU / 8 Gi (requests 0.5 / 1 Gi), from `config/agent/code/mini-*.yaml`.
- **Network is not blocked by default.** There's an optional `answer_leak_blocklist` (DNS blackhole via `/etc/hosts`, applied at the end of setup; `kubernetes.py:83`, `datasets/base.py:194-208`). The released SWE configs do not set it.
- **`anti_hack_cleanup: true`** in the SWE configs runs `datasets/base.py` `_purge_build_residue` / `_purge_build_artifacts` / `_purge_global_caches`. These remove `/tmp` verifier logs, `test_files.json`, jest/pytest caches, `/tests`, `/logs`, compiled build outputs that decompile to the golden fix, and project caches. It deliberately **keeps `.build_env/test_command.sh`**, so agents may see which tests run; that's by design.
- **Git leak prevention:** setup fails if `git rev-list --all --not <base>` is non-empty (`opensource_code.py::_assert_history_truncated`); there's an optional strip mode (`base.py::_prevent_git_hack`, `_strip_future_commits`).

### 4.3 What to implement (stock Harbor + stock mini-swe-agent; only task files + one optional env subclass)

1. **Port MiMo's anti-hack cleanup into `mimo_setup.sh`** (build time):
   - copy the `_RESIDUE_SCRUB_GLOBAL` commands and the build-artifact / global-cache purges from MiMo-Agent `datasets/base.py` (read the code; keep `.build_env/test_command.sh` as MiMo does);
   - run it with `cwd` = the repo.
2. **Strip and assert git history** at build:
   - delete every ref (branches, tags, remotes) that isn't an ancestor of the base;
   - `git stash clear`;
   - `git reflog expire --expire=now --all`;
   - `git gc --prune=now`;
   - then **fail the build** if `git rev-list --all --not $BASE` is non-empty, or if `git fsck --unreachable` shows commits newer than the base.

   **Measured on 237 images** with `forensics_test.sh`:
   - **63 (27%)** have commits newer than the base reachable from refs (branches/tags);
   - **183 (77%)** have dangling commits newer than the base;
   - 53 have reflog entries;
   - 235 have `ORIG_HEAD`;
   - 28 have `.build_env`;
   - 0 have the test patch pre-applied.

   So local history is a major leak path, not just a theoretical one. Note that MiMo's own `rev-list --all` assertion would not catch the dangling commits; `git gc --prune=now` after expiring the reflog and deleting `ORIG_HEAD` does.
3. **Block the agent's internet except the model endpoint.** Preferred: a Daytona domain allowlist during the agent phase only. Facts:
   - Harbor already supports per-phase network policy: `task.toml` `[agent] network_mode = "allowlist"`, `allowed_hosts = [...]`, plus `--allow-agent-host <host>` for a run-specific host. It wraps **only `agent.run()`**, so Harbor's agent *install* (needs PyPI/GitHub) happens before the restriction, and the verifier runs afterwards under the baseline policy (public, needed by ~18% of verifiers that install packages).
   - Stock Harbor's Daytona env reports `network_allowlist=False`, `dynamic_network_policy=False`. **But the pinned Daytona SDK (0.220.0) supports `domain_allow_list`, `network_allow_list`, `labels`, `ttl_minutes`, and `Sandbox.update_network_settings(...)`.**
   - So write a thin subclass of `harbor.environments.daytona.DaytonaEnvironment`, loaded with `harbor run --environment-import-path mymod:LabeledDaytonaEnv` (Harbor stays unmodified). It should:
     - declare `network_allowlist=True, dynamic_network_policy=True`;
     - implement `_apply_network_policy` via `sandbox.update_network_settings(domain_allow_list=…)` (or block-all with an exception for the endpoint);
     - add `labels` (see §6) and a `ttl_minutes` / auto-delete safety net in the sandbox create params.
   - **Verify empirically.** During the agent phase, from inside a sandbox:
     - `curl https://github.com`, `pypi.org`, `registry.npmjs.org`, `proxy.golang.org` must fail;
     - the model endpoint must work.

     During the verifier phase, `pip`/`npm` must work again.
   - Fallback if the allowlist can't be made to work: a MiMo-style `/etc/hosts` blackhole applied after agent install (e.g. Harbor `[environment] healthcheck` starting a watcher that writes the blocklist once mini-swe-agent is installed), with `test.sh` removing it before grading. That's DNS-only and the agent is root, so it's weaker. Block GitHub (`github.com`, `api.github.com`, `raw.githubusercontent.com`, `codeload.github.com`, `objects.githubusercontent.com`, `patch-diff.githubusercontent.com`), `gitlab.com`, `bitbucket.org`, `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `registry.yarnpkg.com`, `proxy.golang.org`, `sum.golang.org`, `goproxy.io`, `goproxy.cn`, `crates.io`, `static.crates.io`, `repo.maven.apache.org`, `cdn.jsdelivr.net`, `unpkg.com`, `rubygems.org`, `packagist.org`.

   Document whichever you implement.
4. **Re-validate after the changes** (§7). The converter must regenerate all tasks after every fix.

---

## 5. Model serving (vLLM, BF16)

- **Qwen3.6-35B-A3B facts** (`config.json`):
  - `Qwen3_5MoeForConditionalGeneration`, 35.95B params (BF16 ≈ 72 GB), 256 experts / 8 active (~3B active);
  - 40 layers, of which **10 are full attention** (`full_attention_interval=4`; the other 30 are linear attention with a per-sequence recurrent state);
  - 2 KV heads, head_dim 256;
  - native context 262,144;
  - 1 MTP layer (speculative decoding possible).
- **KV-cache math (upper bound on concurrency).** Full-attention KV per token = 10 layers × 2 (K,V) × 2 heads × 256 × 2 bytes = **20 KiB/token per replica** (+~2 KiB for the MTP layer). With TP=4, the 2 KV heads get replicated (TP > num_kv_heads), so each GPU holds ~10 KiB/token. A B200 has ~180 GB; weights are ~18 GB/GPU at TP=4, leaving roughly 130–140 GB/GPU for KV, i.e. ~13M tokens per TP=4 replica. That's ≈50 full 262k-token sequences per replica, ≈400 across DP=8. The linear-attention state blocks share the same pool, and vLLM caps `max_num_seqs` by them. In the prototype vLLM refused to start with the default `max_num_seqs` (1024 > available "Mamba cache blocks"), so **set `--max-num-seqs` explicitly** (e.g. 64–128 per replica). **Report vLLM's own startup lines** `GPU KV cache size: N tokens` and `Maximum concurrency for <len> tokens per request: Xx` as the authoritative numbers. With 60 agents, KV is not the bottleneck.
- **Topology.** The user suggested TP=4 × DP=8 (2 replicas per node, GPUs 0–3 and 4–7). For a 3B-active MoE, smaller TP (TP=1 or 2, more replicas) usually gives better throughput; the model fits on one B200. Pick one, but justify it and keep it BF16.
- **Put a router in front** (e.g. `vllm-router`, or nginx). Prefer **session/prefix-affinity routing**: agent trajectories resend a growing prefix, and prefix-cache hit rate was ~90% on a single server. Round-robin across 8 replicas would destroy that.
- **Serve flags.** Check the model card. Use `--served-model-name qwen3.6-35b-a3b --api-key <secret> --max-model-len 262144 --enable-prefix-caching --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --language-model-only`, with MTP speculative decoding if supported: `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'` gave ~2.7 accepted tokens and ~125 tok/s single-stream for the 27B dense model. Verify a tool-call round trip before running.
- **Endpoint exposure.** Daytona sandboxes must reach it over the internet. If this cluster has no inbound access, use a Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:<port>`). Its limits:
  - ~200 in-flight requests, which is fine for 60;
  - **125 s response timeout**, so choose `max_tokens` such that one reply finishes in < ~100 s at the per-stream decode speed you measure under load;
  - the URL changes if cloudflared restarts.

  Always set `--api-key`. If nodes have public inbound access, expose directly instead.

---

## 6. Daytona: concurrency, resources, labels, cleanup

- **Org quota (total, shared with others):** 500 vCPU, 500 GiB memory, 2000 GB storage. **Max 60 concurrent sandboxes** for this run. Daytona reserves the full requested resources, unlike MiMo's Kubernetes requests (0.5/1Gi) vs limits (4/8Gi).
- **Memory is the binding constraint:** 60 × 8 GB = 480 GiB, too close to the quota. Options:
  - measure peak usage first (add `cat /sys/fs/cgroup/memory.peak; df -h /` to `test.sh`), then size from data;
  - likely **2 CPU**;
  - **4–6 GB default**, and 8 GB only for tasks whose test command uses node/jest/vitest/mvn/gradle (the converter already parses the test scripts; `--cpus/--memory-mb/--storage-mb` flags exist);
  - disk 10 GB worked for every image tried, including the 13.7 GB one; measure it.

  Keep the evidence: 4 GB OOM-killed a JS verifier.
- **Labels (required).** Other people use this Daytona org. Tag every sandbox you create, e.g. `labels={"owner": "<user>", "run": "<run_id>", "purpose": "mimo-harbor-eval"}`. Stock Harbor passes no labels, so do it in the env subclass from §4.3. Also set `ttl_minutes` / `auto_delete_interval` as a safety net.
- **Cleanup.** When a batch ends, and periodically:
  - list sandboxes **filtered by your labels** (Daytona SDK `daytona.list(labels=…)`);
  - delete leaked ones: those not belonging to a running trial, or older than agent + verifier timeout + margin.

  **Never delete unlabeled sandboxes or sandboxes with other labels.** Log every deletion.
- Track the live sandbox count with the same API, and alert if it exceeds 60.

---

## 7. Validation gates (in order) before the overnight run

1. Converter regenerated with the §4 changes. Bash/sh syntax checks pass.
2. **Network policy works** (the §4.3 empirical test) on 2–3 tasks.
3. **Image forensics** (`forensics_test.sh`, run as the verifier with `-a nop`) on a sample of ≥100 tasks. Must show:
   - `future_reachable=0`, `stash=0`, no `dangling_newer_than_base`, `test_patch_already_applied=0`, `patch_new_files_present=0`, no `/logs` leftovers.
4. **Null-agent check:** `harbor run -a nop` on a sample, and on every task that gets solved later (`nop_check.sh` loops this). Every reward must be 0. A task that passes untouched is broken and is excluded.
5. **Smoke:** 20–40 tasks × 1 attempt with the real model. Then:
   - run `audit.py` (classifies verifier outcomes) and `scan_traces.py` (flags git archaeology, upstream fetches, grader-path access, skip markers, env errors);
   - read the flagged trajectories yourself, or fan out to subagents.

   Expect zero successful upstream fetches.
6. Only then start the scored run.

---

## 8. The overnight run

- **Throughput estimate.** The prototype averaged ~16 min wall per trial (8 concurrent, H100 27B dense; LLM-bound in part). At 60 concurrent with B200s, expect very roughly 250–400 trials/hour, so a 10 h night gives ~2.5–4k trials, i.e. **~600–1,000 tasks × 4 attempts**. The full 2,698 × 4 = 10,792 trials won't finish overnight.
- **Plan:**
  - fix a **random task order with a recorded seed**;
  - run tasks in waves of ~100–200 with `-k 4`, so each task's 4 attempts finish together;
  - report on completed tasks;
  - keep the loop resumable: skip tasks with 4 valid attempts.
- **Agent config** (`mswea_qwen38.yaml` is the template; layered on mini-swe-agent's builtin config via Harbor `-c mini -c custom.yaml`):
  - `step_limit` (prototype 150; MiMo's harness used 500; pick and document — with 262k context, 250 is reasonable);
  - `environment.timeout: 600`;
  - `model_kwargs`: the model-card thinking-mode sampling, `max_tokens` per §5, `drop_params: true`.
- **Command shape:** `harbor run -y -p <task_dir> -o <jobs> --job-name <wave> -e daytona -n 60 -k 4 -a mini-swe-agent -m openai/<served-name> --ak version=2.4.6 --ak config_file=<yaml> --ae OPENAI_API_KEY=<vllm key> --ae OPENAI_API_BASE=<endpoint>/v1 [--environment-import-path …] [--allow-agent-host <endpoint host>]`.
- **Error policy.**
  - **Infra errors:** sandbox create/build failures, apt/install failures, model endpoint 5xx/connection errors, verifier killed by OOM/timeouts caused by the environment. Retry (Harbor `-r/--retry-include`) or re-queue; exclude persistently failing tasks and list them.
  - **Agent outcomes:** context overflow, step limit, the agent OOM-killing itself, wrong answer. Count as failed attempts.
- **Never** let the endpoint die silently. Health-check the router/tunnel every few minutes and pause the queue if it's down. Otherwise every trial fails with a connection error.

---

## 9. Self-monitoring while it runs (fix what you can, otherwise log it)

- Every ~30 min:
  - run `audit.py` and `scan_traces.py` on new trials;
  - run the null-agent check on new solves;
  - check the live labeled-sandbox count;
  - check vLLM health (KV usage, preemptions, throughput) and the endpoint.
- Watch for new failure classes, for example:
  - new apt/repo breakage;
  - missing toolchains (`command not found` in verifier output);
  - verifiers hanging on prompts;
  - `reward.json` residue;
  - `.build_env` content beyond `test_command.sh`;
  - agents reading `/logs`;
  - git archaeology hitting non-ancestor commits;
  - any network fetch succeeding during the agent phase.
- **Fix in the converter** where possible, regenerate, and re-queue affected tasks. Each fix gets a line in `FINDINGS.md`: symptom, evidence path, fix, re-run result.
- If a fix would change evaluation semantics (agent config, network policy, resources), log it and keep the run internally consistent: don't mix settings within the reported numbers.

---

## 10. Final report contents
- pass@1 and pass@4 with CIs, plus n_tasks; the per-attempt JSONL path.
- Exact config: vLLM flags, TP/DP, `max-model-len`, sampling, `max_tokens`, `step_limit`, sandbox resources, network policy, Harbor commit (`laude-institute/harbor@3de07a0e01f3368921766437fc7afece3ddec23d` was used in the prototype; record yours), mini-swe-agent version.
- Excluded tasks and why: infra, broken (null-agent passes), leak-flagged.
- Distribution of failure classes: wrong answer, context overflow, step limit, agent OOM.
- Updated `FINDINGS.md`.

---

## 11. Files in `mimo_swe/`
| File | What |
|---|---|
| `mimo_to_harbor.py` | parquet → Harbor task dirs (all fixes in §2; §4 work still to add) |
| `FINDINGS.md` | full log of issues found, evidence and fixes in the prototype |
| `run_harbor.sh` | Harbor invocation used (mini-swe-agent 2.4.6, Daytona, env vars) |
| `mswea_qwen38.yaml` | mini-swe-agent config overrides (template; adjust for Qwen3.6) |
| `serve_qwen38.sh` | prototype vLLM command (single H100, FP8; reference only, this run is BF16) |
| `smoke_loop.sh` | batch loop (random not-yet-run tasks, STOP file) |
| `nop_check.sh` | null-agent rerun of every solved task |
| `audit.py` | classify verifier outcomes (solved / test-failed / oom / patch-failed / cmd-not-found / …) |
| `scan_traces.py` | scan trajectories for git archaeology, upstream fetches, grader-path access, test tampering, env errors |
| `forensics_test.sh` | verifier replacement that reports git leftovers and test-patch presence in the untouched image |

Prototype gotchas:
- Harbor installed as a uv tool: `uv tool install "harbor[daytona] @ git+https://github.com/laude-institute/harbor@<rev>"`.
- `pkill -f <pattern>` can kill your own shell if the pattern appears in your command line; use `pkill -f "harbor ru[n]"`.
- Editing a bash script while it's running corrupts it.
- Daytona builds each task's Dockerfile on first use, which is slow; regenerating `environment/` changes the hash and forces a rebuild.
