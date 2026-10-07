# MiMo-V2.6 SWE eval of Qwen3.6-35B-A3B: run summary

2026-10-06, GCP `camp-blue-431854084`, nodes `b200-resv-ray-4..7` (4 × 8 B200). Scored run 07:05–22:45 UTC.
Spec: `~/HANDOFF.md`. Code and results: `CharlieFRuan/rl-env-cleaner`, branch **`qwen36-b200-run`**, folder `mimo_swe/`.

## Result

| | value | 95% bootstrap CI |
|---|---|---|
| **pass@1** (mean c/4) | **0.362** | 0.348–0.375 |
| **pass@4** (c ≥ 1) | **0.634** | 0.615–0.652 |
| tasks counted | 2,695 of 2,698 | |

- **Excluded tasks:**
  - Broken: **001597** and **001225** pass the verifier with the null agent (no changes to the repo).
  - Un-gradable: **001226**'s verifier exceeds its own 1,800 s timeout even untouched.
- **Trials:** 10,852 in total.
  - 10,796 valid. Each task's first 4 valid attempts by start time are counted.
  - 56 infra failures, not counted and re-queued.
- **Attempt outcomes (counted tasks):**

  | Outcome | Attempts |
  |---|---|
  | Wrong answer | 6,722 |
  | Solved | 3,903 |
  | Agent OOM | 103 |
  | Step limit | 38 |
  | Agent timeout | 8 |
  | Agent crash | 4 |
  | Context overflow | 2 |

- **Averages per attempt:** 75.6 steps, 2.9 M input tokens, 21 k output tokens.
- **Per-attempt JSONL:** `mimo_swe/results_qwen36/attempts.jsonl`. Each line has task, attempt, reward, error class, steps, tokens and leak flags.

## Setup

**Serving.**
- vLLM 0.31.0 with Qwen3.6-35B-A3B in BF16. **32 independent replicas, tensor-parallel 1, one per GPU.**
  - The model is 66 GiB, so it fits on one B200. With a 3B-active MoE, tensor parallelism buys little.
  - Flags: `--max-model-len 262144 --max-num-seqs 32`, prefix caching on, and MTP speculative decoding (`qwen3_next_mtp`, 2 tokens).
  - Each replica reports `GPU KV cache size: 4,141,263 tokens` and 15.8× concurrency at 262 k.
  - Measured speed: about 450 tok/s single-stream, mean MTP acceptance length 2.2.
- **Prefix-affinity router.**
  - It hashes the system message plus the first user message, and picks a replica by rendezvous hashing.
  - So every turn of a trajectory, and all 4 attempts of a task, land on the same replica.
  - Measured prefix-cache hit rate: **93.5%** over 847 k requests and 32.7 B prompt tokens.
  - There's no cross-instance KV sharing; none is needed.
- **Exposure.** Daytona reaches the router through a **Cloudflare quick tunnel**. Its 125 s response cap is why `max_tokens` is 16,384.

**Agent and harness.**
- Stock Harbor (`laude-institute/harbor@3de07a0e`, v0.13.1) running its built-in mini-swe-agent 2.4.6.
- Sampling is the model card's SWE-bench setting: temperature 1.0, top_p 0.95, top_k 20.
- `step_limit` 250, `environment.timeout` 600, agent timeout 3,600 s.
- `preserve_thinking` stays at the Qwen3.6 template default (false). That avoided the prototype's context-overflow problem: only 2 overflows in this run.

**Sandboxes (Daytona).**
- 4 vCPU and 10 GB disk.
- Memory: 4 GB default, 8 GB for JS/JVM verifiers, which the converter detects heuristically.
  - 85 more tasks were promoted to 8 GB after an OOM at 4 GB.
  - All 4 attempts of a promoted task re-run at 8 GB, and its 4 GB attempts are superseded. Each task is scored under one setting.
- Sandboxes carry labels (`owner/run/purpose`) and a 6 h TTL.
- Concurrency was 68–74, using the full org quota after you approved it.

## Anti-leak measures (HANDOFF §4)

`mimo_setup.sh` is **ours, not MiMo's.** It's the `SETUP_SH` string in `mimo_swe/mimo_to_harbor.py` (from line 71). The converter writes it into every task as `environment/mimo_setup.sh`, and the generated Dockerfile (`DOCKERFILE`, line 62) runs it at image build:

```
RUN sh /tmp/mimo_setup.sh <cwd> <KEEP_BUILD_ENV>
```

The prototype wrote steps 1–3 (apt and toolchain fixes, base-commit record). Steps 4–5 below were added in this run. MiMo's own code is Python (`MiMo-Agent/src/mimoagent/environments/datasets/base.py`, `opensource_code.py`); step 4 is a shell port of it.

1. **MiMo `anti_hack_cleanup` port.** `SETUP_SH` step 4 (`mimo_to_harbor.py` lines 182–219):
   - 4a `_purge_build_residue` (`_RESIDUE_SCRUB_GLOBAL`): `/tmp` logs and patches, `test_files.json`, jest/pytest/go-build caches, `/tests`, `/logs`, `/var/log/*`, and `.build_env/task_description.md`.
   - 4b `_purge_build_artifacts`: `git clean -fdx` keeping dependency dirs (MiMo's keep lists). `.build_env/` is excluded from the clean; only `test_command.sh` is kept, unless the verifier reads the image's own `.build_env`, in which case `KEEP_BUILD_ENV=1` and it's kept whole.
   - 4c `_purge_global_caches` (`_GLOBAL_CACHE_SCRUB`): Maven SNAPSHOT jars and Julia, Gradle and Bazel caches.
   - The source references (`base.py:607`, `:630`, `:709`, `:826`) are in comments at the top of step 4.
2. **Git history strip and assert.** `SETUP_SH` step 5 (lines 220–289):
   - `strip_git()` (line 224) removes non-ancestor refs, remotes (including broken symrefs), stash, notes, replace refs, worktrees, reflog, `ORIG_HEAD`/`FETCH_HEAD`, `.keep` files and alternates, then runs `gc --prune=now`.
   - `assert_git_clean()` (line 262) requires HEAD == base, empty `rev-list --all --not BASE`, and no unreachable commit newer than base.
   - Called at line 278; **`exit 1` fails the image build** on a leak.
   - Base commit recorded in step 3 (line 171) to `/etc/mimo_base_ref` (root-only).
3. **Grader residue.** Build: `rm -rf /logs/verifier /logs/agent` (line 169). Verifier: `TEST_SH` (line 291) deletes any `reward.json` before grading (line 295), so a stale or agent-planted one can't count.
4. **Agent-phase network allowlist.**
   - **Task side:** `TASK_TOML` `[agent] network_mode = "allowlist"` (line 45).
   - **Run side:** `--allow-agent-host <tunnel host>` (`run_harbor.sh`), and `agent.extra_allowed_hosts` in the continuous runner's `trial_config()`.
   - **Enforcement:** `labeled_daytona.py` `LabeledDaytonaEnv`.
     - `capabilities` (line 41) turns on `network_allowlist` / `dynamic_network_policy`.
     - `_apply_network_policy` (line 56) calls `sandbox.update_network_settings(domain_allow_list=...)` for the agent phase, and clears it for public.
   - Agent install and verifier run with public network; only `agent.run()` is restricted.
   - Verified end to end before the run: github, pypi, npm, golang, crates, maven, direct-IP, plain HTTP and DNS all fail; only the tunnel works.
5. **Grading isolation.** `TEST_SH` resets every patch-touched file to the base commit before applying the hidden tests (lines 301–313). It then applies the patch (line 315) and runs the test command with no stdin (line 321), as MiMo's `OpenSourceCodeEnvironment` does.
6. **Checks during the run:**
   - `nop_check.sh`: null-agent rerun of every solved task, writing `broken_nop.txt`.
   - `scan_traces.py`: trajectory leak and environment-error scan.
   - `make_report.py`: leak flags in `attempts.jsonl`.
7. **Outcome:** 141 upstream-fetch attempts in the trajectories, and **0 succeeded**. I read every trajectory the scans flagged as a possible success. The null-agent check covered **all 1,710 solved tasks**.

## Timeline (UTC)
- **06:15–06:45:** started the Ray cluster, installed vLLM, downloaded the model. Two forks ported the anti-leak cleanup and built the Daytona allowlist env.
- **06:45–07:20:** brought up the 32 replicas, router and tunnel; ran the gates (smoke, forensics, network).
  - Fixed a broken `origin/HEAD` symref that made gc fail at build.
  - Found the host-side API key requirement.
  - Found the org quota held by another user's ~385 sandboxes.
- **07:05:** scored waves started.
- **07:50:** switched to a continuous trial-level scheduler. `harbor run` waves had idle slots at each wave boundary.
- **08:00–21:30:** monitored every 30 min.
  - Fixed apt and dpkg breakage in images, and poisoned Daytona build caches.
  - Promoted tasks to 8 GB when they hit OOM.
  - Confirmed the 2 broken tasks and investigated the slow verifier.
- **22:45:** finished.

## Issues found and fixed

Full evidence for each item is in `FINDINGS.md`. Line numbers refer to `mimo_swe/` on branch `qwen36-b200-run`.

| Problem | Fix | Where in the code |
|---|---|---|
| Dangling `refs/remotes/origin/HEAD` after the strip made `git gc` fail | Drop `refs/remotes`, unresolvable loose refs and remote packed-refs | `mimo_to_harbor.py` `SETUP_SH` `strip_git()`, lines 241–247 |
| Daytona cached a *cancelled* build ("context canceled" on every create) after harbor was killed mid-build | Nonce comment, so those tasks get a fresh Dockerfile hash | Data file `build_nonce.txt` (`results_qwen36/`), read via `--nonce-file` (`mimo_to_harbor.py` line 388; Dockerfile append line 434) |
| deb822 `.sources` stanza left without `URIs` by our Xiaomi-mirror rewrite | Drop whole stanzas pointing at the mirror; drop stanzas with empty `URIs` | `SETUP_SH` step 1, lines 80–84 and 114–118 |
| Image saved mid-dpkg | `dpkg --configure -a` at build | `SETUP_SH` step 1, lines 103–104 |
| Half-configured package whose postinst calls systemctl (typesense) | Rename its postinst to `*.mimo-disabled`, then configure | `SETUP_SH` step 1, lines 105–112 |
| Image with unmet dependencies | `apt-get -f install` when `apt-get check` fails | `SETUP_SH` step 1, lines 126–132 |
| Harbor's default retry set would retry agent outcomes and bias results | Retry infra exception types only; endpoint errors re-queued by the scheduler | Waves: `wave_loop.sh` lines 35–36 (`--retry-include`). Continuous run: no Harbor retries; `trial_status.py` `classify()` (line 50, endpoint regex line 22) + `continuous_runner.py` `reconcile()` (line 143) |
| `harbor run` wave-boundary stragglers | One trial per (task, attempt) with continuous refill | `continuous_runner.py` (whole file; circuit breaker around line 219), launched by `run_continuous.sh` |
| Harbor leaves BUILD_FAILED and ERROR sandboxes behind | Delete ours (labels checked) every 10 min, each logged | `cleanup_failed_builds.py` line 19, run by `maintenance.sh` |
| OOM at 4 GB, including OOMs that kill the whole sandbox and leave no verdict | Promote the task to 8 GB; supersede its 4 GB attempts | Default tiers: `HEAVY_RE` / `memory_for()` (`mimo_to_harbor.py` 348–370). Promotions: `heavy_ids.txt` via `--heavy-ids-file` (390, 408–427). Supersede: `supersede_mem4.py` |
| Host-side API key check in Harbor | Export the key on the host as well as `--ae` | `run_harbor.sh` (`export MSWEA_API_KEY OPENAI_API_KEY`), `continuous_runner.py` `worker_run_trial()` |
| Resource sizing had no data | Log memory/disk and CPU stats at verify time | `TEST_SH` lines 324–326 (`mimo_mem_peak_bytes`, `mimo_cpu_stat`) |

## Open items for you
- **Forensics outputs unreviewed.** I didn't read `jobs/gate-forensics-r2/*/verifier/test-stdout.txt`, because Claude Code auto mode blocked it. The build-time git assert enforces the main property those outputs measure.
- **Still running on nodes 4–7:** the 32 vLLM replicas, the router and the tunnel. A `STOP` file in `~/mimo` has halted the helper loops.
- **vCPU sizing for the next run.** CPU logging from the last ~1,600 attempts is in the verifier output (`mimo_cpu_stat`).
  - Average use is about 0.9 cores, but about half the sandboxes hit the 4-core cap during bursts.
  - Suggestion: 2 vCPU by default and 4 for heavy tasks, which saves about 35%.

## Key entry points (in `mimo_swe/` on the branch; working copies under `~/mimo`)

| Script | What it does |
|---|---|
| `mimo_to_harbor.py` | **Converter.** `code.parquet` → 2,698 Harbor task dirs. `SETUP_SH` is the build script (apt fixes, toolchain env, MiMo cleanup, git strip + assert); `TEST_SH` is the verifier (reset patch-touched files to base, apply hidden tests, run `{ test_command; } </dev/null`, log mem/CPU). Flags: `--nonce-file`, `--heavy-ids-file`. |
| `labeled_daytona.py` | Harbor `DaytonaEnvironment` subclass: labels, TTL, and the per-phase network allowlist (`update_network_settings`). |
| `run_harbor.sh` | One `harbor run` invocation, also used for nop/probe runs (`AGENT=nop`). The scored waves before 07:50 used it. |
| `continuous_runner.py` (+ `run_continuous.sh`) | **The scored-run scheduler.** One Trial per (task, attempt) in a process pool, using the trial template `~/mimo/trial_template.json`. It reconciles need from disk every 3 min, so it can resume. Knobs live in `~/mimo/continuous.conf` (TARGET, RATE); a `STOP` file stops launches. |
| `trial_status.py` | Classifies trials as valid / infra / running. It is the single source of truth for validity, used by the scheduler and the report. |
| `affinity_router.py` | Prefix-affinity HTTP router in front of the 32 replicas. |
| `serve_replica.sh` | (`~/mimo`) the vLLM command for one replica. |
| `endpoint_health.sh` | Checks tunnel → router → vLLM, and restarts the router or tunnel if down. |
| `nop_check.sh` | Null-agent rerun of every solved task; writes `broken_nop.txt`. |
| `supersede_mem4.py` | Moves 4 GB attempts of tasks promoted to 8 GB out of the scored set. |
| `cleanup_failed_builds.py`, `sandbox_janitor.py`, `maintenance.sh` | Sandbox hygiene (our labels only, every deletion logged). |
| `make_report.py` | Produces `report.md`, `summary.json` and `attempts.jsonl` (pass@k with bootstrap CIs, exclusions, failure classes, leak flags). |
| `audit.py`, `scan_traces.py` | Verifier-outcome audit and trajectory leak / env-error scan. |
| `FINDINGS.md` | Full log of issues: symptom, evidence path, fix, rerun result. |
| `results_qwen36/` | Final report, `summary.json`, `attempts.jsonl`, `report_config.json`, `heavy_ids.txt`, `build_nonce.txt`, exclusion lists, seeded task order. |

**Suggested reading order:**
1. `results_qwen36/report.md`
2. `FINDINGS.md`, the "Qwen3.6" section onward
3. `mimo_to_harbor.py`: `SETUP_SH` and `TEST_SH`
4. `labeled_daytona.py`
5. `continuous_runner.py` together with `trial_status.py`

**To reproduce:**
1. Run the converter, then `serve_replica.sh` on each GPU.
2. Start `affinity_router.py`, then `cloudflared` (whose URL goes into `tunnel_url`).
3. Launch `run_continuous.sh`.
4. Alongside it, run `nop_check.sh`, `maintenance.sh` and the `supersede_mem4.py` loop.
