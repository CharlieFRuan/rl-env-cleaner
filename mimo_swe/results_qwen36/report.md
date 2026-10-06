# MiMo-V2.6-RL-oss SWE: Qwen3.6-35B-A3B (mini-swe-agent 2.4.6, Harbor, Daytona)

- **Tasks counted** (all 4 attempts valid, not excluded): **2695**
- **pass@1** = 0.3621 (95% bootstrap CI 0.3483–0.3751)
- **pass@4** = 0.6338 (95% CI 0.6152–0.6519)

## Coverage and exclusions
- Trials: 10852 total; validity {'valid': 10796, 'infra': 56}
- Tasks with ≥1 valid attempt: 2697; complete: 2697
- Excluded (broken-nop): 2 — format-code-task-001225, format-code-task-001597
- Excluded (infra-persistent): 1 — format-code-task-001226

## Infra errors (not counted, re-queued)
- infra:no-verdict: 51
- infra:verifier-oom: 5

## Attempt outcomes (counted tasks)
- wrong-answer: 6722
- solved: 3903
- agent-oom: 103
- step-limit: 38
- agent-timeout: 8
- agent-crash: 4
- context-overflow: 2
- mean steps 75.6, mean input tokens 2915651, mean output tokens 21187

## Automatic leak scan (all trials)
- git-archaeology: 494
- upstream-fetch-attempt: 141

## Config
```json
{
  "model": "Qwen/Qwen3.6-35B-A3B (BF16)",
  "served_name": "qwen3.6-35b-a3b",
  "vllm": "0.31.0; 32 replicas TP=1 DP=32 (4 nodes x 8 B200); --max-model-len 262144 --max-num-seqs 32 --enable-prefix-caching --language-model-only --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --speculative-config {method: qwen3_next_mtp, num_speculative_tokens: 2}; KV cache 4,141,263 tokens/replica, 15.80x at 262k",
  "router": "prefix-affinity (rendezvous hash of system+first user msg) -> Cloudflare quick tunnel",
  "sampling": "temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence_penalty 0, max_tokens 16384, preserve_thinking template default (false)",
  "agent": "Harbor built-in mini-swe-agent, mini-swe-agent 2.4.6, step_limit 250, environment.timeout 600, agent timeout 3600 s",
  "harbor": "laude-institute/harbor@3de07a0e01f3368921766437fc7afece3ddec23d (0.13.1), Daytona SDK 0.220.0",
  "sandbox": "4 CPU, 10240 MB disk; memory 4096 MB default, 8192 MB for 770 tasks: JS/JVM verifiers (heuristic, 686) + 85 tasks promoted after an OOM at 4 GB (all their attempts re-run at 8 GB; 4 GB attempts superseded, see heavy_ids.txt); labels owner/run/purpose; ttl 360 min",
  "concurrency": "68-74 Daytona sandboxes + 4 null-agent; continuous trial-level scheduler (continuous_runner.py) after 07:50 UTC, harbor-run waves before",
  "network": "agent phase: Daytona domain allowlist = tunnel host only; agent install + verifier phases public",
  "anti_leak": "MiMo anti_hack_cleanup ported; git history stripped + build-time assert (no non-ancestor or newer dangling commits)",
  "task_order": "random.Random(20261006).shuffle(sorted task ids)",
  "n_attempts": 4,
  "retries": "Harbor -r 2 only for SandboxBuildFailedError/EnvironmentStartTimeoutError/HealthcheckError/AgentSetupTimeoutError; endpoint errors re-queued; tasks with >=6 infra attempts excluded",
  "run_window_utc": "2026-10-06 07:05 - 22:45",
  "excluded": "001597, 001225 (pass with nop agent); 001226 (verifier exceeds its own 1800 s timeout untouched)"
}
```

Per-attempt results: `/home/charlieruan/mimo/report/attempts.jsonl`
