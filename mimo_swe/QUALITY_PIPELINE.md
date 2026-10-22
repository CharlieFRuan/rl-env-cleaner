# Quality pipeline for the MiMo-V2.6-RL-oss SWE tasks

This is an index. The detail is in the linked docs, all in `mimo_swe/` on branch `qwen36-b200-run`. The pipeline turns the
2,698 `code` tasks into Harbor tasks that are leak-resistant, gradable and right-sized. It also states which tasks to
drop. It applies to evals and to RL use.

## Stages

| # | Stage | What it does | Doc / code | Output |
|---|---|---|---|---|
| 1 | **Convert + environment fixes** | `code.parquet` → Harbor task dirs. `mimo_setup.sh` runs at image build: apt-mirror, dpkg and dependency repairs, toolchain env, base-commit record | `HANDOFF.md` §2; `mimo_to_harbor.py` (`SETUP_SH` steps 1–3) | `harbor_tasks/mimo-code/` |
| 2 | **Anti-leak at build** | Port of MiMo `anti_hack_cleanup` (residue, build-artifact and cache purges). Git history strip, then a check that **fails the build** if any non-ancestor or newer unreachable commit survives (the vals.ai "unreachable objects" loophole) | `results_qwen36/RUN_SUMMARY.md` "Anti-leak"; `SETUP_SH` steps 4–5 | Clean git history in every image |
| 3 | **Anti-leak at runtime** | Agent phase allowed only the model endpoint (Daytona domain allowlist); install and verifier phases have public network. Grading resets patch-touched files and runs with no stdin | `labeled_daytona.py`; `TEST_SH`; `RUN_SUMMARY.md` | No upstream fetches (141 attempts, 0 succeeded) |
| 4 | **Validation gates** | Image forensics with a null agent (git leftovers, test patch pre-applied, grader residue). Null-agent check: the tests must fail on the untouched repo | `HANDOFF.md` §7; `forensics_test.sh`, `nop_check.sh`; `FINDINGS.md` | `broken_nop.txt` |
| 5 | **Trajectory leak audit** | Scan agent trajectories for answer-seeking (git archaeology, upstream fetches, installed copies, caches, timestamps) and read the flagged ones | `FINDINGS.md`, `scan_traces.py`, `make_report.py` leak flags | 6 leak-flagged tasks (lower bound: only what agents hit) |
| 6 | **Image residue audit** | Probe every image for installed copies of the project, post-base releases, stale bytecode, build caches and second checkouts. Confirm leaks without a gold patch by **transplant** (does the copy alone solve the task?). Apply per-task fixes, then rerun agents to show fixes don't hurt solvability | **`RESIDUE_AUDIT.md`**; `residue_*.py`, `residue_probe.sh`; `mimo_to_harbor.py --residue-fixes-file` | `residue_fixes.tsv` (82 tasks fixed), `residue_excluded.tsv` |
| 7 | **Resource sizing** | Profile vCPU, memory and disk per task, then size: memory `ceil(peak)+1` GiB, 4 vCPU only for CPU-starved tasks, disk `max(10, peak+2)` | **`sandbox_provision_pass.md`**; `prof_sampler.sh`, `prof/` | `task_resources.tsv` (written into each `task.toml`) |
| 8 | **Scoring with exclusions** | pass@1 / pass@4 with bootstrap CIs on tasks with 4 valid attempts. Infra errors are re-queued, not counted. Leak-flagged tasks are reported both excluded and as failures | `make_report.py`, `trial_status.py`; `RUN_SUMMARY.md` | `results_qwen36/` |

Supporting infrastructure, not a quality step: `model_endpoint_relay.md` (relay VM replacing the Cloudflare tunnel).

## Final exclusion list: 40 unique tasks

| Source | Category | Tasks |
|---|---|---|
| `excluded_tasks.tsv` | `oom-16g`: still OOM at Daytona's 16 GiB maximum | 29 |
| | `leak-flagged`: trajectory audit (001427 002607 003058 000195 001397 002517) | 6 |
| | `broken-nop`: verifier passes on the untouched repo (001597 001225) | 2 |
| | `ungradable`: verifier can't finish in time (001226 slow; 000315 hangs) | 2 |
| `residue_excluded.tsv` | `residue-unfixable`: 002070 (Node-bundled `nopt`); 002607 is also leak-flagged | 1 new |

Use the union of both files. The other 5 leak-flagged tasks are among the 82 residue-fixed tasks. They stay excluded
from the reported Qwen3.6 numbers, which were measured before the fixes.

## Still open
- **Decide whether to exclude or accept** 002060 and 002061 (newer npm / undici bundled with Node; not transplant-confirmed), and the
  unmapped copies in 001920 and 001815 (`RESIDUE_AUDIT.md` "Leftovers").
- **Fix 10 tasks too big for Daytona:** they are sized above its 16 GB memory / 20 GB disk cap (8 at 17 GiB memory; 001108 40 GiB
  and 001540 22 GiB disk). Every create fails until they're capped or excluded (`RESIDUE_AUDIT.md` "Also found").
- **Normalize file timestamps:** not done. Files touched by the fix can look newer than their siblings. Only one agent used this, as a hint.
- **Re-measure Qwen3.6 on the fixed images:** the reported numbers predate the residue fixes and resizing. The fixed-image
  rerun estimates the effect at −0.026 pass@1, which is not significant.

## Regenerating tasks

```
python mimo_to_harbor.py code.parquet harbor_tasks/mimo-code \
  --nonce-file results_qwen36/build_nonce.txt \
  --resources-file task_resources.tsv \
  --residue-fixes-file residue_fixes.tsv
```

`harbor_tasks/` is generated, not versioned. The `.tsv` files are the source of truth (`sandbox_provision_pass.md`).
