# Cyber continuation: access and dataset inventory, 2026-10-23

Branch: `cyber-b200-run`, based on `origin/qwen36-b200-run` (`b372d71`).
Worktree: `/home/charlieruan/mimo/rl-env-cleaner-cyber`.
The original `skycap-overfit` checkout and its modified janitor log were left intact.

## Scope corrected directly by the user

The user clarified in this session: **eight attempts per training task per model;
three attempts per benchmark task per model**. The handoff's combined three-pass
plan was incorrect and is superseded.

Training: all 1,000 cyber tasks, Qwen3.6-35B-A3B followed by GLM-5.3-Flash FP8,
with mini-swe-agent: 8,000 attempts per model, 16,000 total before infra retries.
Benchmark: separate MiMo Cyber Bench (mini), three attempts per model. Its location
is still unresolved; the user has been asked for a link or local path while public
discovery continues. Do not label the training set as benchmark results.

The user requested information gathering and early access questions before
overnight execution, then explicitly authorized full control. Session execution
settings still need to expose the necessary host capabilities.

## Verified locally

- Cyber parquet: 1,000 rows and 1,000 distinct IDs; every image maps to Docker Hub.
- All descriptions parse into sanitizer, bug type, function, and file.
- 176 project path prefixes; 693 unique descriptions. The other 307 rows repeat
  descriptions across distinct IDs. Keep these tasks until image/binary comparison
  establishes whether any are duplicates.
- GH_TOKEN, DAYTONA_API_KEY, WANDB_API_KEY, HF_API_KEY are present in
  `~/charlie_keys.env`. Values were never printed. Their live validity is unverified
  in this session.
- GitHub connector can enumerate the repository branches. It reports main,
  qwen36-b200-run, skycap-overfit, and jupiter.
- Connector Git tree creation returned HTTP 403, `Resource not accessible by
  integration`. Progress is committed on the local branch. Push with the existing
  host GitHub authentication once shell networking is available; do not assume
  the read-capable connector has write permission.
- Qwen config exists at `/mnt/local_storage/charlieruan/hf/Qwen3.6-35B-A3B`.
- GLM download has completed since the handoff: all 62 indexed safetensors shards
  exist at `/mnt/local_storage/charlieruan/hf/GLM-5.3-Flash`, totaling
  328,337,455,672 bytes. Shard headers and declared tensor data extents agree with
  file sizes. This is a structural check, not a full content checksum.
- GLM architecture is `Glm5NextForConditionalGeneration`; configuration says
  `quant_method=fp8`, `fmt=e4m3`; tensor headers include F8_E4M3, BF16 and F32.
  The local vLLM model registry includes this architecture. Actual serving remains
  untested. Do not substitute the Inkling2 or RL-trained checkpoints.

## Current execution blocker

The shell can read the host files but runs in a restricted execution context:

| Probe | Observed result |
|---|---|
| `nvidia-smi` | Cannot communicate with NVIDIA driver; no `/dev/nvidia*` visible |
| SSH to node 4 and node 5, bypassing system SSH config with `-F /dev/null` | `socket: Operation not permitted` |
| HTTPS credential checks using requests | DNS resolution fails for API hosts |
| snap-packaged `gcloud` | `snap-confine` missing permitted capability |

These results do not establish that the host GPU driver, cluster, credentials,
or gcloud login are broken. They establish that this execution context cannot
perform those live checks. No model servers, Ray processes, sandboxes, downloads,
or evaluation trials have been launched in this continuation.

The user authorized full control and was given the documented Full access
permissions-picker instructions for desktop/IDE or CLI `/permissions`. Recheck
capabilities after the user applies the change. No additional credential appears
necessary yet. The agent has not changed the session's own permissions.

## Grading setup missing from the original suggested steps

Local MiMo implementation:

- `MiMo-Agent/src/mimoagent/environments/datasets/arvo.py`
- `MiMo-Agent/src/mimoagent/environments/datasets/resources/server_arvo.py`

ARVO setup injects `/root/server.py` and `/root/expected_func.json`, then starts
a grading server on loopback port 8666. Pulling an image and invoking submit.sh
alone is insufficient unless the image happens to have an equivalent running
service. The host harness must reproduce this setup.

The reference server copies the protected `/root/binary` into a verification
directory and executes as the `verify` user. It stores the authoritative result
at `/root/last_result.json` and the input at `/root/last_poc`. MiMo's agent is
restricted; preserve this separation when adapting Harbor/Daytona. A root agent
could overwrite the grading artifacts and invalidate the experiment.

Reward uses the final submission and requires both crash and match. Match checks
the first application function, sanitizer, and error type. Although the expected
file is parsed and stored, **the reference match expression does not compare the
file path**. Preserve reference semantics explicitly rather than silently adding
an extra file-match condition. A missing grading server must be an infrastructure
failure, not a scored wrong answer. A working server with no agent submission is
a valid zero.

## Next live checks after access is available

1. Validate the four keys without printing them, GPU availability, and SSH to nodes
   5–7. Inspect active workloads before allocating GPUs or restarting Ray.
2. Prove one existing ARVO image in Daytona, including grader injection, a
   restricted agent user, and authoritative host-side reward collection.
3. Audit null/garbage submissions and accessible solution artifacts. Probe grader
   write isolation and network blocking. Keep audit submissions separate from
   scored attempts; clear their state before each trial.
4. Serve and health-check Qwen; run five tasks once with mini-swe-agent. Validate
   the evidence and error accounting before scaling.
5. Serve and health-check the downloaded GLM FP8 checkpoint. Run the same pilot.
6. Resume the complete task/attempt matrix with infra retries, per-task quality
   exclusions, resource measurements, and difficulty reports.

## Reproduce the offline inventory

```bash
cd /home/charlieruan/mimo/rl-env-cleaner-cyber
~/.local/share/uv/tools/harbor/bin/python mimo_cyber/inventory.py \
  --output-dir mimo_cyber
```

The generated manifest retains the full task instruction (not only the terse
parquet prompt) and maps each grading target to its existing public image.
`inventory.json` records source hashes and dataset-level counts. This inventory
does not establish image availability, clean environments, or solvability.

The report PDF is saved locally at `~/mimo/cyber/report.pdf` (SHA256
`fb81e6e083801b3358f084ed6be953dc23b0d2e434690f4541d5eae03e01e7af`).
Neither local PDF parsing tools nor a working direct PDF fetch were available
in this restricted session; report content has not yet been read here.
