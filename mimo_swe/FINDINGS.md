# MiMo-V2.6-RL-oss SWE on Harbor: findings

Setup: mini-swe-agent 2.4.6 on Daytona, Qwen3.8-27B-FP8 (local vLLM, Cloudflare tunnel), 150 steps, max_tokens 8192, concurrency 8.

## Fixed before the loop
- Ubuntu images: apt → Xiaomi-internal `apt.sys.srv` (+ `xiaomi.sources`) → rewritten to archive.ubuntu.com.
- Debian bullseye images: past end of LTS (Aug 2026), security pool 404 → archive.debian.org.
- Images flattened, ENV lost (Go not on PATH etc.) → PATH/GOROOT/GOPATH/CARGO_HOME/RUSTUP_HOME/JAVA_HOME restored via profile.d + BASH_ENV.
- uv installer skips `~/.local/bin/env` if `~/.local/bin` already on PATH → kept it off PATH.
- mini-swe-agent 2.4.6 ignores MSWEA_API_KEY for openai/ → pass OPENAI_API_KEY.

## Smoke results (5 smallest images, concurrency 1)
- 001647 (Python): 0 — real failure (4/5 tests).
- 001621 (Java/Maven): 0 — real failure (4/5 tests).

## Loop findings
- **OOM at 4 GB** (b001/000037, JS monorepo): agent ran full `npx jest`, sandbox OOM → agent exit 137, then the verifier was also `Killed` (137) → reward 0 not the model's fault. MiMo's k8s sandboxes are 4 CPU / 8Gi limit (mimoagent kubernetes.py), and its test scripts set `-Xmx3g` / `--max-old-space-size=4096`. Fix: tasks regenerated at cpus=4, memory_mb=8192 (~14:40 PDT). Rerun: 000037.
- **Null-agent check** (nop_check.sh → jobs/nop-r*): every solved task is rerun with Harbor's `nop` agent. r001 (6 tasks, incl. Go 001782): all reward 0, with the target tests genuinely failing → solves are real and Go PATH fix works in the verifier.
- Audit: `python3 audit.py` classifies trials (solved / test-failed / verifier-oom / patch-failed / cmd-not-found / network / ...).
- **Context overflow** (b001/000983 at step 102; b001/000020 at step 123; b001/000050 at step 63; b001/001156 at step 106; b001/003059 at step 136; b002/000046 at step 131; b002/000071 at step 96; b002/000676 at step 86; b002/000647 at step 92; b003/002543 at step 55; b003/003046 at step 94; b004/000778 at step 125; b004/001560 at step 91; b004/000065 at step 75; b004/002630 at step 122; b004/003057 at step 127; b004/002627 at step 133 — vLLM rejects once prompt + max_tokens 8192 > 153600, i.e. at ~145k prompt tokens): after 102 steps the history passed 153.6k tokens → litellm ContextWindowExceeded → agent exit 1 (Harbor logs NonZeroAgentExitCodeError) → verifier still runs, reward 0. Expected at this limit, not a bug. Options if it becomes common: max-model-len up to 262k (native; fewer concurrent seqs), or a lower step_limit.
- **Verifier timeout** (b001/002945, vite): agent ran 9 min, verifier hit 1800 s. Its test script (inside the patch's `mimo_build_env.tar.gz.b64`) runs `pnpm install --frozen-lockfile` + `pnpm run build` + e2e vitest — network + heavy. Probing with nop agent at 2× verifier timeout (jobs/probe-002945-nop).
- **Static scan of all 2,698 verifier scripts** (mimo_test_command.sh + build-env tarballs, 843 tasks have one): 498 contain install-ish commands (npx 253, pip install 125, npm install/ci 89, go mod 19, yarn 18, apt-get 18, mvn 13, pnpm 11, composer 9, cargo 9, gradle 8, bundle 4). Many are no-ops when deps are present, but these verifiers need public network at grading time and are the candidates for slow/flaky grading. No hard-coded Xiaomi-internal mirrors found (one task uses goproxy.cn with proxy.golang.org fallback; 000250 explicitly bypasses "Xiaomi mirror" with Maven Central settings).
- **Why context fills**: Qwen3.8's chat template defaults `preserve_thinking=true` (keeps every past turn's `<think>`), and mini-swe-agent re-sends `reasoning_content`. In 000050 at step 63: ~190k chars of past reasoning + ~236k chars of observations in the prompt. Intended model behavior, but it caps trajectories at ~60–120 steps under 150k. Knobs (not changed; user picked 150k): `--max-model-len` up to 262144 (native), or `chat_template_kwargs: {preserve_thinking: false}` in mswea model_kwargs.extra_body.
- **Batch b001 done**: 27/42 solved, 7 errored (5 context overflows, 1 OOM pre-8GB, 1 verifier timeout).
- **Broken third-party apt repo** (b002/001179): image ships packages.clickhouse.com source without its key → NO_PUBKEY → `apt-get update` fails → Harbor's mini-swe-agent install exits 100. Fix: mimo_setup.sh runs `apt-get update` at build and moves any sources.list.d entry failing signature/Release checks to *.disabled (distro sources untouched). Regenerated 16:07.
- **Project venv off PATH** (001179, multi-toolchain "dev" images with /opt/node-*, /opt/go-*, temurin 8–25, rustup): verifier `pytest: command not found` (127) because /testbed/.venv/bin was on the lost image ENV. Fix: profile adds $CWD/.venv | $CWD/venv | /opt/venv to PATH (+VIRTUAL_ENV). nop probe now runs the tests (9 failed / 3 passed, as expected). Rerun: 001179.
- **stdin/TTY hang in verifier — FIXED, verified** (002945, vite monorepo; with </dev/null the nop verifier now finishes in 41 s: 1 failed / 43 passed): Harbor's Daytona verifier gives the test script a TTY; `pnpm install --frozen-lockfile` then shows an interactive "modules directories will be removed and reinstalled. Proceed? (Y/n)" prompt and waits forever (nop agent also hit the 1 h timeout; traced run showed the prompt). MiMo execs with stdin=False, tty=False. Fix: test.sh runs `{ test_command; } </dev/null` (brace group because 276 tasks use `... base && ... new`). Regenerated 17:12.
- **pyenv off PATH** (b002/000023): `pytest: command not found`; pytest lives in /root/.pyenv/versions/3.9.18/bin. Fix: profile now restores pyenv (shims or newest version bin), conda, nvm, volta, bun, deno, sdkman, rbenv, goenv when present; project venv still wins. nop probe now runs tests (6 failed / 3 passed). Rerun: 000023.
- **Agent OOM-killed at 8 GB** (b002/003060, step 62, full jest suite): exit 137 kills mini-swe-agent itself since installed agents run inside the sandbox (in MiMo the agent is outside the pod, so only the command would die). Verifier then grades the repo as left (real failure). 3 cases so far (000037 at 4 GB; 003060, b006/000963 at 8 GB — all full jest runs); ~1% of trials; left as-is.
- **Batch b002 done**: 27/44 solved, 6 errored.
- **Batch b003 done**: 26/44 solved, 3 errored (incl. 4 reruns, all solved).
- **Batch b004 done**: 25/41 solved, 6 errored (all 6 context overflows; incl. 002127 rerun, graded cleanly).
- **Batch b005 done**: 29/40 solved, 3 errored (all context overflows: 000080 @55, 001908 @148, 002338 @124).
- **Stale reward.json in image (reward-integrity risk)** (b003/002127): image ships MiMo's build-time verifier output under /logs/verifier (cmd*.out/exit + an LLM-judge `reward.json`, "model": "gpt-5.5", overall_pass true). Harbor reads reward.json in preference to reward.txt → here pydantic ValidationError (list values); with scalar values it would have silently produced a false solve, and an agent could plant one the same way. Fix: mimo_setup.sh `rm -rf /logs/verifier /logs/agent` at build; test.sh `rm -f /logs/verifier/reward.json` before grading. Regenerated 18:01. Scan: no other trial had a reward.json, so no past false solves. Rerun: 002127 → graded cleanly (no residue; real failure, 3 failed / 10 passed).
- **Reruns after fixes (b003)**: 000037 (was OOM @4 GB) → solved; 001179 (was apt NO_PUBKEY) → solved; 002945 (was pnpm TTY hang) → solved; 000023 (was pyenv off PATH) → solved. Infra failures were masking real solves.

## Reward hacking / leakage audit (22:00, 243 trials, b001–b006)
- **Internet leakage is severe.** Sandboxes have public internet, so agents search GitHub for the issue, find the fixing PR/commit, and apply it (`curl .../pull/N.diff && git apply`, `git clone` upstream + `git show <fix>`, `pip download`/`npm pack`/`go mod download` of the next release and `cp` the file). 45 trials fetched upstream code; trace review (4 parallel reviewers, spot-checked): **37 COPIED_FIX + 1 CONSULTED_FIX**, 7 benign. That is ≥38 of 147 solves (≥26%) invalid. Adjusted solve rate ≤ (147−38)/227 ≈ 48%. Agents often verify "IDENTICAL TO UPSTREAM" by blob hash. Per-trial tables: scratchpad reviewer reports (net_part_00..03).
- **Local git history leakage**: 000551 restored the answer with `git show 27504b3:<file>` where 27504b3 is not an ancestor of the base. MiMo's harness asserts `git rev-list --all --not <base>` is empty at setup; our conversion does not. Image forensics probe (jobs/forensics-r1) is measuring future refs/tags/stash/reflog/dangling commits newer than base per image.
- **`.build_env/` residue in images**: many images already contain /testbed/.build_env (test_command.sh, run_tests.py, validate_file_owner.py, even a .venv and pickled test state). Agents read it → learn exactly which hidden tests will run. Partial leak (test identity, sometimes more).
- Test tampering: none found that affects grading (no conftest/skip/config edits to grader tests; deletions are the agent's own scratch tests; patch-touched files are reset before grading).
- Network blocking in Harbor+Daytona: only all-or-nothing (`disable_internet`), no allowlist, no per-phase switch; mini-swe-agent runs inside the sandbox and needs the tunnel → can't simply turn internet off.
- **Image forensics (jobs/forensics-r1, 237 images, nop agent)**: future commits reachable from refs in 63 (27%); dangling commits newer than base in 183 (77%); reflog entries 53; ORIG_HEAD 235; .build_env 28; test patch pre-applied 0; patch-added files pre-existing 0. → local git history is a major leak path; needs build-time strip (delete non-ancestor refs, ORIG_HEAD, stash, reflog expire, gc --prune=now) + assert.

## Qwen3.6-35B-A3B BF16 run on GCP 4x8 B200 (2026-10-06)

Setup: vLLM 0.31.0, 32 replicas (TP=1, one per GPU; model is 66 GiB BF16 so it fits one B200, and a 3B-active
MoE gains little from TP), `--max-model-len 262144 --max-num-seqs 32`, MTP speculative decoding
(`qwen3_next_mtp`, 2 tokens; mean acceptance length 2.2, ~450 tok/s single stream). vLLM startup per replica:
`GPU KV cache size: 4,141,263 tokens`, `Maximum concurrency for 262,144 tokens per request: 15.80x`.
Prefix-affinity router (`affinity_router.py`, rendezvous hash of system + first user message) -> Cloudflare quick tunnel.
Sampling: model-card SWE-bench setting temperature 1.0, top_p 0.95, top_k 20 (generation_config), min_p 0,
presence_penalty 0; `preserve_thinking` left at the Qwen3.6 template default (false). max_tokens 16384 (Cloudflare 125 s cap).
step_limit 250. Sandboxes 4 CPU, 6 GB (8 GB for JS/JVM verifiers: 686 of 2,698 tasks), 10 GB disk.

- **Anti-leak, implemented** (HANDOFF 4.3): MiMo `anti_hack_cleanup` ported into mimo_setup.sh; git history stripped
  at build (non-ancestor refs, remotes, stash, notes, reflog, ORIG_HEAD etc., gc --prune=now) and the build FAILS if
  `rev-list --all --not BASE` is non-empty or fsck finds an unreachable commit newer than BASE.
  Agent-phase network: Daytona domain allowlist via `labeled_daytona.py` (`update_network_settings(domain_allow_list=<tunnel host>)`),
  verified end to end through Harbor: during agent run github/pypi/npm/golang/crates/maven/gitlab, direct-IP HTTPS,
  plain HTTP, DNS and `pip download` all fail; only the tunnel host answers. Verifier phase: public again (pip/npm OK).
  Pitfall: `network_block_all=False` alone keeps the allowlist; clearing `domain_allow_list=""` restores public access.
- **Build failure: broken `refs/remotes/origin/HEAD` symref** (gate-smoke-r1 001505 + 4 others): after the strip
  deleted remote branches, the dangling symref made `git gc` fail ("bad object refs/remotes/origin/HEAD"). Fix: strip
  drops `refs/remotes` wholesale and any loose ref that no longer resolves. Regenerated 06:47 UTC.
- **Harbor host-side API key check**: Harbor's mini-swe-agent refuses to start unless OPENAI_API_KEY/MSWEA_API_KEY is
  set in the *host* env (not only `--ae`). run_harbor.sh exports it.
- **Retry policy**: Harbor's default retry set includes NonZeroAgentExitCodeError (context overflow, agent OOM); retrying
  those would bias pass rates upward. Scored waves retry only SandboxBuildFailedError, EnvironmentStartTimeoutError,
  HealthcheckError, AgentSetupTimeoutError. Endpoint failures inside the agent are classified infra by
  `trial_status.py` (log regex) and the task's missing attempts are re-queued by `wave_loop.sh`.
- **Behavioral consequence of the block**: agents that `pip install` helpers during the agent phase now fail to install
  (e.g. smoke 001322 uninstalled the package and could not reinstall it). Same constraint for every attempt; left as is.
- **Daytona org quota was the start-time bottleneck, not builds** (06:58 UTC): another org user held ~385 sandboxes
  (398/500 vCPU, 416/500 GiB); our creates waited for quota. Audit log (`/api/audit/organizations/<org>`, filterable by
  `from`/`to`) identifies actors by email + API-key suffix. Default sandbox memory lowered 6 -> 4 GB (8 GB kept for JS/JVM);
  measured whole-sandbox peaks ~1.0-1.3 GB for Python/Go tasks. User then freed the org quota: ~72 concurrent sandboxes.
- **Poisoned Daytona build cache -> "context canceled" on every create** (000574, 000722, 000902, 001249, 001540, 002155;
  6/6 attempts each): these builds were in flight when gate-r1 was killed; the cancelled build stays cached under the
  Dockerfile hash. Fix: `--nonce-file build_nonce.txt` appends `# build-nonce: N` to those Dockerfiles only (fresh hash;
  other tasks' cached builds untouched). Lesson: never kill harbor mid-build without expecting this.
- **Harbor leaves BUILD_FAILED sandboxes behind** (87 at 07:20): `cleanup_failed_builds.py` (labeled-ours only, logged)
  runs every 10 min from `maintenance.sh`.
- **First scored waves (07:05-07:25)**: leak scan of 274 trials: 1 real upstream fetch attempt (`pip download`), blocked
  by the allowlist (connection errors). Null-agent: 19 solved tasks rerun with nop, all reward 0. Agent `command not
  found` hits are missing convenience tools (hexdump/xxd), not verifier toolchains.
- **Wave-boundary stragglers -> continuous scheduler** (07:45): `harbor run -n N` is continuous *within* a wave, but a
  wave's free slots idle until its slowest trial ends (agent timeout 3600 s). Replaced wave_loop.sh with
  `continuous_runner.py` (pattern from NovaSky-AI/harbor-private adapters/mercor/run_mercor_eval.py): one Harbor Trial
  per (task, attempt) in a spawn process pool, global concurrency target + launch rate (live-tunable via
  continuous.conf), seeded task order with a task's attempts launched back to back (shared build cache), need
  reconciled from disk every 3 min (resumable; composes with earlier waves; infra outcomes re-queued; >= 6 infra
  attempts -> excluded_infra.txt), endpoint-down pause, circuit breaker on 20 consecutive infra outcomes.
  Same TrialConfig as the waves (template = a harbor-run generated config), so settings are unchanged.
  Launch bugs found on the way, none affecting scored data: Harbor 0.13.1 needs `await Trial.create(cfg)`; the first
  launch lacked DAYTONA_API_KEY (146 auth-failure trial dirs moved to jobs/broken-launch-noauth-0748, outside the
  scored glob). `run_continuous.sh` now sets credentials.
- Build-nonce fix confirmed: 000574 / 000902 (previously 6/6 "context canceled") now complete.
- Transient: 001235 agent install (uv installer) could not reach github.com from Daytona twice (curl connect
  timeout, install phase = public network). Infra, re-queued.
- **001235 excluded (persistent infra)**: 7/7 attempts fail in Harbor's agent install (uv installer: `curl: (28) Failed
  to connect to github.com port 443` after ~135 s), install phase on public network. Probes of the same image (nop
  agent, verifier phase, plain + login shell) reach github.com fine (no proxy, normal DNS/hosts). Only task with this
  pattern; left excluded (excluded_infra.txt), revisit if it spreads.
- Throughput 08:20 UTC: ~13 trials/min with 68 sandboxes; ~20 of them building at any time, ~13 model requests in flight
  -> image builds/agent install, not the model, are the limiter. Org memory reserved 448/500 GiB (ours 364, user's
  Anyscale job 84) -> TARGET raised 68 -> 74.
- **Agent OOM by memory tier** (08:55, 1,233 valid attempts): 4 GB tier 7/877 (0.8%), 8 GB JS/JVM tier 12/356 (3.4%,
  full jest/mvn runs; MiMo's own limit is 8 GiB, so left as is). The 4 GB OOMs cluster on 3 tasks (002405 4/4, 000243
  2/4, 001921 1/4) whose own test suites need > 4 GB: scoring them at 4 GB would penalize the model for our resource
  choice. Fix: `--heavy-ids-file heavy_ids.txt` moves them to 8 GB; their 4 GB attempts moved to
  jobs/superseded-mem4gb/ (outside the scored glob) and all 4 attempts re-run at 8 GB (one setting per task).
  Policy going forward: any 4 GB task with an agent OOM gets the same treatment (checked each check-in).
- **Agent install apt failures (exit 100), 2 tasks excluded then fixed** (09:30):
  001302 `E: dpkg was interrupted, you must manually run 'dpkg --configure -a'` (image saved mid-dpkg);
  000264 `E: Malformed entry 1 in sources file /etc/apt/sources.list.d/ubuntu.sources (URI)` (our own Xiaomi-mirror
  sed deleted a deb822 stanza's URIs line but left the stanza). Fix in mimo_setup.sh: drop whole deb822 stanzas that
  point at apt.sys.srv/xiaomi, drop stanzas with no/empty URIs, run `dpkg --configure -a` at build. Verified by
  probe build of both images: `apt-get update` and `apt-get install -y curl build-essential git` rc=0.
  Regenerated all tasks 09:35 (setup change -> new build hash; apt-config only, grading unchanged). Their 15 infra
  attempts moved to jobs/superseded-aptfix/; re-admitted at the next runner restart (excluded_infra.txt is now
  re-read every reconcile in the source; the running process predates that change).
- 4 GB-tier quarantine corrected: the 11 re-runs of 002405/000243/001921 that started after the 08:51 regeneration
  ran at 8 GB (peaks 8.1 GB / 7.3 GB, confirming the move) and are scored; only the 12 pre-regeneration 4 GB
  attempts are in jobs/superseded-mem4gb/.
- **Verifier OOM at 4 GB: 000211** (09:47; `python -m unittest` Killed, rc 137, cgroup peak 4.29 GB): moved to the 8 GB
  tier per the policy above (heavy_ids.txt); its 4 GB attempts moved to jobs/superseded-mem4gb/ (the one in flight is
  moved by a watcher when it finishes, logs/superseded_moves.log); all 4 attempts re-run at 8 GB.
- **More 4 GB OOM tasks** (10:20): 000642, 001596, 002813 (4/4 agent OOMs), 002805, 003034 (3/4) -> 8 GB tier. The
  JS/JVM heuristic misses some Python/Go/C++ suites that need > 4 GB (~0.4% of tasks so far). Automated:
  heavy_ids.txt now records `since=<regeneration time>` per task and `supersede_mem4.py` (loop every 2 min) moves any
  finished attempt that started before `since` (ran at 4 GB) to jobs/superseded-mem4gb/ (36 attempts so far).
- 8 GB-tier verifier OOMs (002988, 002318) and verifier timeouts after an agent left a hanging `go test` (000108)
  are kept as infra per HANDOFF (retried; >= 6 -> excluded and listed). Agent OOM rate overall 43/2,423 (1.8%).
- Interim 10:20: 605 tasks complete, pass@1 0.385, pass@4 0.673.
- 000700 -> 8 GB tier (11:20; every 4 GB attempt peaked at the 4 GB ceiling, one verifier OOM). Interim 11:20: 780 tasks complete.
- **Half-configured package with an unrunnable postinst** (002899, 8/8 agent installs exit 100): image ships
  `typesense-server` half-configured; its postinst calls systemctl ("System has not been booted with systemd"), so
  every `apt-get install` re-runs it and dpkg fails. Fix in mimo_setup.sh: after `dpkg --configure -a`, any package
  `dpkg --audit` still lists gets its postinst moved to `*.mimo-disabled` and is configured. Verified by probe:
  audit clean, `apt-get install -y curl build-essential git` rc=0. Regenerated 12:00.
- 000755 (databricks/koalas, Spark JVM run via pytest; 6 verifier OOMs, all at the 4 GB ceiling) -> 8 GB tier.
- **Controlled runner restart 12:01** to re-admit fixed tasks (001302, 000264, 000755, 002899; exclusions are now
  re-read from excluded_infra.txt every reconcile): killed the runner's process group, moved its 74 in-flight trial
  dirs to jobs/killed-restart-1201/ (outside the scored glob; otherwise they would count as pending), deleted their
  74+ labeled scored sandboxes, restarted with a 160-worker pool (was 256; 183 idle workers seen).
- 001095 build cache poisoned by the 12:01 runner restart (build in flight was cancelled -> 8/8 "context canceled"): build nonce added, infra attempts moved to jobs/superseded-poisoned-cache/, re-admitted (runner re-reads exclusions). Other tasks mid-build at the restart (000102, 000194, 002568) were unaffected.
- **15 more 4 GB tasks promoted to 8 GB** (12:55): 000348, 000629, 001102, 002077 (4/4 agent OOMs), 002558 (3/4),
  001594, 002502 (2/4), 000493, 000718, 000859, 001392, 001595, 002152, 002183, 002975 (1/4); sampled peaks sit
  at the 4 GB ceiling. Earlier check-ins under-counted these: my ad-hoc sweep only grepped agent/verifier logs,
  while the kill shows up as `exit code 137` in exception.txt (make_report.py's agent-oom class sees it). 59 4 GB
  attempts superseded. Now 712 tasks at 8 GB (26%). Check-ins use report/attempts.jsonl agent-oom for this policy.
- Interim 12:55: 1,070 tasks complete, pass@1 0.356, pass@4 0.637 (will shift as promoted tasks re-run).
- 13:55: 7 more 4 GB tasks -> 8 GB (000347, 001451 4/4; 001726, 001895, 002957 2/4; 001573, 002925 1/4). Promoted so far: 34 tasks. Leak scan of 4,682 continuous trials: 258 upstream fetch attempts, 0 with evidence of fetched content. Interim: 1,242 tasks, pass@1 0.352, pass@4 0.630.
- **Unmet dependencies in image** (000809, 8/8 agent installs: "curl : Depends: libcurl4t64 (= ...) but ... is to be installed", libevent-dev unmet): mimo_setup.sh runs `apt-get -f install -y` at build when `apt-get check` fails. Probe: check rc=0, install rc=0. Regenerated 14:30; 000809 re-admitted (infra attempts -> jobs/superseded-aptfix/).
- 001235 re-admitted (14:35): a probe replaying Harbor's exact install sequence (apt-get install curl build-essential git, then the uv installer download from github.com) succeeds on the current build; its 7 infra attempts moved to jobs/superseded-aptfix/.
- 14:50: 000809 and 001235 now 4/4 valid; no exclusions. New rare infra: sandbox vanished on Daytona's side before the post-agent network restore (`update_network_settings ... Sandbox ... not found`, 2/~2,250 trials, after agent TimeoutError); retried as infra, no fix needed.
- 15:25: 13 more 4 GB tasks -> 8 GB (001733, 001740 4/4; 002842 3/4; others 1-2/4). Promoted total 47. Interim: 1,546 tasks, pass@1 0.359, pass@4 0.635; no exclusions.
- 15:55: OOMs that kill the agent or the whole sandbox at 4 GB end as infra (no verdict) and were invisible to the report-based sweep (it sees valid attempts only). Infra sweep (exception.txt: exit 137 / SANDBOX_NOT_RUNNING) found 002431, 000108 (peaks at the 4 GB ceiling; 000108's earlier verifier timeouts were likely memory pressure) -> 8 GB. Check-ins now sweep both.
- **First broken task: 001597** (nop-r211): its verifier passes on the untouched repo (targeted Go tests in
  istio pilot/pkg/model pass; the patch's new file is an e2e test not in the run) -> listed in broken_nop.txt and
  excluded from the headline by make_report.py. Scored attempts had 3/4 "solves", all spurious.
- 001226 (4 GB): 6/6 attempts hit VerifierTimeoutError (1800 s) with no verifier output captured; diagnostic
  probe (nop agent, real verifier under an inner 1500 s timeout + memory monitor) running as jobs/probe-1226.
- **001226 excluded (cannot be graded in time)**: probe with nop agent: `pytest cortex/tests/test_dataset.py` passes test after test but several tests take 5-6 min each (pycortex surface computation, CPU-bound on 4 vCPU, ~1 GB RAM); the untouched suite exceeds the task's own 1800 s verifier timeout (inner 1500 s cap hit at 87%). Possibly a precomputed cache removed by the ported MiMo cleanup (same cleanup as MiMo's harness). Left in excluded_infra.txt with this reason.
- 17:55: 16 more 4 GB tasks -> 8 GB (total promoted 65). Leak scan of 7,821 continuous trials: 401 upstream fetch attempts, 0 with fetched content. Interim: 2,016 tasks, pass@1 0.361, pass@4 0.636; excluded: 001597 (broken, nop passes), 001226 (verifier too slow).
- 18:55: 11 more 4 GB tasks -> 8 GB (total 76).
- 19:25: the report's 16 "upstream-fetch-ok" flags were all false positives (read each: git prints "Cloning into ..." then "Could not resolve host: github.com"; pip connection errors; empty curl output). make_report.py now requires no failure marker in the output. No successful upstream fetch anywhere so far.
