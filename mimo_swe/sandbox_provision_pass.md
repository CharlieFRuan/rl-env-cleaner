# Sandbox provisioning pass (vCPU / memory / disk per task)

Goal: per-task Daytona sandbox resources for the 2,698 MiMo-V2.6-RL-oss SWE tasks: small enough not to waste quota, big
enough not to change outcomes. This is pure profiling, not an eval; rewards are only a sanity check. It ran 2026-10-08 to 10-09
with Qwen3.6-35B-A3B + mini-swe-agent, 4 attempts per task.

Outputs (this directory):
- `task_resources.tsv`: the final spec, `task  vcpus  memory_gib  storage_gib` for 2,659 tasks.
- `excluded_tasks.tsv`: the 39 excluded tasks, each with a category and reason.
- `prof/results/`: per-trial and per-task tables for every wave (`analysis` = wave 1, `analysis_cpu4`, `analysis_mem16`).

## Method

### What we measure, and how

`prof_sampler.sh` runs in every sandbox. `labeled_daytona.py` starts it right after the sandbox starts (when
`MIMO_PROFILE=1`), so it covers agent install, the agent run and the verifier. Every 1 s it reads the sandbox's own cgroup v2
files and writes a time series plus running peaks. At the end the verifier (`TEST_SH` in `mimo_to_harbor.py`) prints the
peaks and the kernel's lifetime counters into `verifier/test-stdout.txt`. `prof/analyze_prof.py` turns those into
`trials.csv` (one row per trial) and `tasks.csv` (max over a task's attempts).

| Quantity | Source | Notes |
|---|---|---|
| Memory, total peak | `memory.peak` | Exact, kernel-tracked; includes page cache. This is what we size from. |
| Memory, non-reclaimable peak | sampled `memory.stat` `anon + kernel + shmem` | Excludes page cache, which the kernel can drop. Swap is 0, so shmem can't be reclaimed. Sampled, so spikes under 1 s can be missed. |
| OOM | `memory.events` `oom_kill` | The limit `memory.max` is enforced: processes are OOM-killed at it. |
| CPU | `cpu.stat` (`usage_usec`, `nr_periods`, `nr_throttled`, `system_usec`), `cpu.pressure` | Allocation is `cpu.max`, e.g. `200000 100000` = 2 vCPU. |
| Disk | `df /` | Overlay upper layer = bytes written on top of the image. |
| Processes / threads | `pids.peak` | |
| Phase times | Harbor `result.json` | Environment setup, agent setup, agent, verifier. |
| Tool / LLM call time | mini-swe-agent per-message `extra.timestamp` | Tool = tool msg − its assistant msg; LLM = assistant msg − previous tool msg. |

Things that don't work and why:
- **`nproc` reports 64 (the host), whatever the allocation.** Daytona limits CPU with a CFS quota, not a cpuset, so `nproc`
  says nothing about the vCPU limit. `cpu.max` is the real limit. `nproc` also explains several bad cases: tools that size
  thread or worker pools from it start ~64 workers on a 2-core quota.
- **`memory.peak` can't be reset** (Permission denied), so it's a whole-trial number, not per phase.
- **Daytona's own metrics are too coarse:** the Analytics API gives 1-minute aggregates, and the per-sandbox telemetry
  endpoint is disabled on our org. Hence our own sampler.
- **`/proc/uptime` is virtualized** (container uptime) and `date +%s` is too coarse, so the sampler times intervals with
  `date +%s%N`. CFS can overshoot the quota by about 10% within a 1 s window, so peak cores can read slightly above the limit.

### Waves

Policy for any task set:

1. **Wave 1, all tasks.**
   - vCPU: go **small**, from prior knowledge (we used 2), to see whether small is enough.
   - Memory and disk: go **big**, from prior knowledge (we used 8 GiB and 20 GiB), so the peak isn't truncated.
   - 4 attempts per task, since the peak varies with what the agent does.
2. **Wave 2, reruns of the flagged tasks.** Memory and CPU are rerun as separate sets. A task in both sets runs in both.
   - **Memory:** tasks that hit an OOM kill on any attempt are rerun at **double** the memory (16 GiB). All other tasks
     keep their wave-1 measurement.
   - **CPU:** tasks flagged as CPU-starved (rule below) are rerun at **double** the vCPU (4). Tasks that weren't flagged
     stay at the small wave-1 allocation.
3. **Wave 3, optional:** tasks that are flagged for both, at both increases together. We didn't run it: the 24 overlap
   tasks got 4 vCPU from the CPU rerun and memory from the memory rerun.
4. **Exclude what still fails at the maximum.** A task is excluded if, at the maximum allocation (4 vCPU, or 16 GiB):
   - its verifier still times out (the verifier exceeds the task's own 1,800 s limit), and more CPU didn't help; or
   - it still hits an OOM kill on any attempt.

### Sizing rules

**Memory: `ceil_gib(memory.peak) + 1`**, where `memory.peak` is the max over the task's attempts, taken from wave 1 or, for
tasks that OOMed there, from the 16 GiB rerun. `ceil_gib` rounds up to whole GiB (400 MB → 1), so the minimum is 2 GiB. The
+1 GiB is margin.

We size from total `memory.peak` rather than the non-reclaimable part, to be lenient. It's exact (no sampling gap) and it
already contains the task's page cache. The cost is modest: 0.9 GiB per task on average more than sizing from
non-reclaimable memory. A leave-one-out test checks each rule: set the limit from 3 attempts, then ask whether the 4th
attempt's non-reclaimable memory (the part that actually causes OOMs) would have exceeded it. This covers the 2,580 tasks
with no OOM at 8 GiB.

| Rule | Held-out attempt over the limit (OOM risk) | Mean allocation |
|---|---|---|
| ceil(non-reclaimable) + 1 | 0.65% | 2.74 GiB |
| ceil(non-reclaimable) + 2 | 0.35% | 3.72 GiB |
| **ceil(total `memory.peak`) + 1 (chosen)** | **0.37%** | **3.60 GiB** |
| ceil(total `memory.peak`) + 2 | 0.22% | 4.55 GiB |

Most tasks are very stable. The median spread between a task's highest and lowest attempt is 0.09 GiB (p90 1.1, p99 5.3),
so the misses come from a small tail of variable tasks. Caveat: sizing from total memory slightly over-sizes tasks whose page
cache grew into the room wave 1 gave them.

**vCPU: what counts as CPU-starved.** A task is flagged for the 4-vCPU rerun if, on any wave-1 attempt:

- its verifier timed out; **or**
- its verifier took more than 600 s; **or**
- it was throttled in more than 50% of CFS periods (`nr_throttled / nr_periods`, a 100 ms window each) **and**
  (verifier more than 300 s **or** mean tool call more than 10 s).

The intent is that the task hit its quota most of the time, and there's enough wall time for that to matter. Signals that
tell the cases apart:

| Pattern | Average cores (`usage_usec` / wall) | Throttled | Kernel share of CPU | Processes | Fix |
|---|---|---|---|---|---|
| **CPU-bound** | near the quota | high | low | modest | more vCPU |
| **Oversubscribed** (pools sized to `nproc` = 64) | near the quota | high | high (> 0.4) | hundreds to thousands | thread caps, not more vCPU |
| **Hung / waiting** | ~0.1 | ~0 | n/a | n/a | neither; diagnose or exclude |

Known limitations:
- Throttled counts a period even if the quota ran out only briefly in it, so bursty work also looks throttled.
- Timed-out attempts have no profile, because the counters print at the end of the verifier. So a timeout task's CPU
  numbers come from its other attempts.
- **The rerun is the real test:** a flag only means "worth trying 4".

**Disk:** 20 GiB for every task. It's rarely an issue (see below), so it isn't sized per task.

## Wave 1 results (2 vCPU / 8 GiB / 20 GiB, 4 attempts, all 2,698 tasks)

10,835 trials, of which 46 were infra failures (re-queued; none involved the model endpoint). Values are per task, the max
over its attempts, across the 2,697 tasks with profile data.

| Resource (limit) | p50 | p90 | p99 | Flagged |
|---|---|---|---|---|
| Memory, non-reclaimable (8 GiB) | 0.70 GiB | 4.30 GiB | 8.0 GiB | **117 tasks with ≥ 1 OOM-killed attempt** (25 on all 4); 114 reached the limit |
| Memory, total `memory.peak` (8 GiB) | 1.44 GiB | 5.58 GiB | 8.0 GiB | 176 reached the limit (the extra ones are page cache filling the room) |
| Average cores (2 vCPU) | 0.50 | 1.28 | 1.74 | 443 tasks throttled in more than half the periods |
| Verifier time | 9 s | 58 s | 468 s | 22 over 600 s; 6 hit the 1,800 s timeout |
| Disk (20 GiB) | 0.67 GiB | 1.59 GiB | 4.93 GiB | 26 over 5 GiB, 10 over 10 GiB; 001108 reached 20.0 GiB |

105 tasks met the CPU rule. Disk: 4 trials report about 540 GB because their `/` was a 766 GB host filesystem rather than the
quota volume, so their disk number is unmeasured.

## Wave 2 results

### CPU rerun: 105 flagged tasks at 4 vCPU / 8 GiB (`jobs/prof-cpu4`)

On the 104 tasks that aren't excluded, mean per attempt:

| | 2 vCPU | 4 vCPU | Ratio |
|---|---|---|---|
| Verifier time | 152 s | 81 s | **0.54×** |
| Agent tool time | 573 s | 408 s | 0.71× |
| Trial wall time (agent + verifier) | 894 s | 700 s | **0.78×** |
| Verifier timeouts | 10 | 4 | |
| Mean reward | 0.402 | 0.413 | |

We decided to give **4 vCPU to the whole flagged set** rather than picking tasks one by one. Each attempt runs a different
agent patch, so its verifier and tool time vary a lot, and 4 attempts per side can't sort tasks reliably. Checked with
shuffled splits (each task's 8 attempts randomly reassigned between the two configs, 200 times):

| Per-task rule | Tasks picked | Expected by chance (mean / p95) |
|---|---|---|
| (verifier + tool) ≥ 300 s and ≤ 0.7× at 4 vCPU | 34 | 17 / 23 |
| verifier ≥ 120 s and ≤ 0.7× at 4 vCPU | 16 | 6.4 / 9 |

The set-level effect is real and the per-task pick is about half noise. The flagged set is only about 4% of tasks, so 4 vCPU
for all of it costs about +4% vCPU in total.

### Memory rerun: 117 OOM tasks at 2 vCPU / 16 GiB (`jobs/prof-mem16`)

- **88 tasks fit at 16 GiB**, sized from their 16 GiB `memory.peak`. Some had OOMed at 8 GiB on only one attempt and peak
  below 8 here.
- **29 tasks still hit an OOM kill at 16 GiB on at least one attempt**, 5 of them on all attempts. These are excluded.
- **Not confirmed: that they oversubscribe because they see 64 CPUs.** Their peak process counts are only a bit above those
  of tasks that fit (median 730 vs 582), so they may simply need more than 16 GiB, or leak.

### 001226 diagnosis

001226's verifier timed out at 2 vCPU (6/6) and at 4 vCPU (4/4).
- **Setup:** a 2-vCPU probe from the task image, with no agent, ran the verifier's command.
- **What it runs:** all 24 tests in `cortex/tests/test_dataset.py`, not only the target test.
- **Result:** the tests alone took 17.4 min, using 34 CPU-min with only 11 s of kernel time, so both cores were saturated on
  real work.
- **Hot process:** `inkscape`. pycortex's `quickshow` renders SVG to PNG with it.
- **Conclusion:** thread caps can't help. Excluded rather than given a one-off longer timeout.

## Decision

| | Rule | Result |
|---|---|---|
| vCPU | 4 if CPU-flagged in wave 1, else 2 | 2,562 tasks at 2, **97 at 4** (5,512 vCPU vs 5,318 at a flat 2) |
| Memory | `ceil_gib(memory.peak) + 1` | 297 tasks at 2 GiB, 1,847 at 3–4, 360 at 5–8, 119 at 9–12, 36 at 13–17. Mean **3.9 GiB**: 10,277 GiB vs 21,272 at a flat 8 (−52%) |
| Disk | 20 GiB for all | |
| Excluded | 39 tasks (`excluded_tasks.tsv`) | 29 `oom-16g`; `ungradable` 001226 (verifier too slow even at 4 vCPU) and 000315 (verifier times out at 2 and 4 vCPU while using ~0.07 cores, i.e. it hangs); plus 2 `broken-nop` and 6 `leak-flagged` carried over from the eval |

Of the 105 CPU-flagged tasks, 8 are now excluded, which leaves the 97.

## Operational notes

- **Concurrency is capped by the runner's worker pool.** `continuous_runner.py`'s `ProcessPoolExecutor` caps real concurrency
  (`MIMO_POOL_WORKERS`, default 160). TARGET above it only queues futures, and our first "500" ran at about 160.
  Use 600 for 500 concurrent.
- **Daytona sandbox setup:** p50 32 s at 50 concurrent, 87 s at 200, about 90 s at 500 (p90 140–180 s). It was the only
  phase that slowed with load.
- **Throughput at 500 concurrent:** about 60 trials/min, so the whole wave took about 5 hours (most of it after the pool fix).
- **Scripts in `prof/`:**
  - `run_prof.sh` (wave 1) and `run_rerun.sh cpu4|mem16` (wave 2): the runners.
  - `analyze_prof.py [job_dir] [out_dir]`: the analysis.
  - `monitor.sh`: the periodic health check.
  - `cleanup_loop.sh`: removes failed builds and leaked sandboxes.
  - `one_trial.sh`: a single trial.
  - Live knobs are in `*.conf`, re-read every 30 s.
