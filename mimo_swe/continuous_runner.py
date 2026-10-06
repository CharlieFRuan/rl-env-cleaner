"""Continuous (straggler-free) scheduler for the scored run: one Harbor Trial per (task, attempt).

Instead of `harbor run` waves (a wave's free slots idle until its slowest trial finishes), every slot is
refilled as soon as any trial finishes. Pattern adapted from NovaSky-AI/harbor-private
adapters/mercor/run_mercor_eval.py (process-pool trial workers + launch-rate limiter).

Bookkeeping is reconciled from disk, so it composes with older `harbor run` waves and is resumable:
  need[task] = 4 - valid attempts (any jobs/wave-*) - attempts still pending on disk or in flight here.
A trial that ends as infra (trial_status.classify) simply leaves the need unmet, so the next
reconcile re-queues it; tasks with >= MAX_INFRA infra attempts are excluded (excluded_infra.txt).

Live knobs (re-read every 30 s) in $HERE/continuous.conf:   TARGET=72  RATE=1.0  RECONCILE_SEC=180
TARGET counts all running trials, including leftovers of older harbor-run waves.
Stop launching: touch $HERE/STOP (in-flight trials finish). Endpoint down -> launches pause.

Run (harbor's python; PYTHONPATH must include this dir for labeled_daytona):
  PYTHONPATH=<this dir> ~/.local/share/uv/tools/harbor/bin/python continuous_runner.py
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import glob
import json
import multiprocessing
import os
import random
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trial_status import N_ATTEMPTS, classify, task_of  # noqa: E402

HERE = Path("/home/charlieruan/mimo")
SRC = Path(__file__).resolve().parent
TASKS = HERE / "harbor_tasks/mimo-code"
ORDER = HERE / "task_order_seed20261006.txt"
JOB = "wave-continuous"
TRIALS_DIR = HERE / "jobs" / JOB
TEMPLATE = HERE / "trial_template.json"  # a TrialConfig written by `harbor run` for a scored trial
CONF = HERE / "continuous.conf"
MAX_INFRA = 6


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def read_conf() -> dict:
    conf = {"TARGET": 72, "RATE": 1.0, "RECONCILE_SEC": 180}
    if CONF.exists():
        for line in CONF.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = float(v.strip()) if "." in v else int(v.strip())
    return conf


def trial_config(task: str) -> dict:
    """Template TrialConfig, re-pointed at this task and at the current tunnel URL."""
    cfg = json.loads(TEMPLATE.read_text())
    url = (HERE / "tunnel_url").read_text().strip()
    host = url.removeprefix("https://")
    key = (HERE / "vllm_api_key").read_text().strip()
    cfg["task"] = {"path": str(TASKS / task), "source": JOB}
    cfg["trial_name"] = f"{task}__" + "".join(random.choices(string.ascii_letters + string.digits, k=7))
    cfg["trials_dir"] = str(TRIALS_DIR)
    cfg["job_id"] = str(uuid.uuid4())
    cfg["agent"]["extra_allowed_hosts"] = [host]
    cfg["agent"]["env"].update({"OPENAI_API_KEY": key, "OPENAI_API_BASE": f"{url}/v1"})
    return cfg


def worker_run_trial(cfg: dict) -> str:
    """Pool worker (fresh spawned process): run one trial; return its trial dir. Harbor-level retries are off;
    infra failures are re-queued by the scheduler instead."""
    import asyncio as _asyncio

    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    os.environ["MSWEA_API_KEY"] = os.environ["OPENAI_API_KEY"] = cfg["agent"]["env"]["OPENAI_API_KEY"]
    os.environ["OPENAI_API_BASE"] = cfg["agent"]["env"]["OPENAI_API_BASE"]

    async def _run():
        trial = await Trial.create(TrialConfig.model_validate(cfg))
        return await trial.run()

    try:
        _asyncio.run(_run())
    except Exception as e:  # noqa: BLE001 - recorded; the trial dir (if any) is classified as infra
        print(f"worker exception {cfg['trial_name']}: {e!r}", flush=True)
    return str(TRIALS_DIR / cfg["trial_name"])


class Scheduler:
    def __init__(self) -> None:
        self.order = [l.strip() for l in ORDER.read_text().splitlines() if l.strip()]
        self.rank = {t: i for i, t in enumerate(self.order)}
        self.queue: list[str] = []  # task ids, one entry per attempt, kept in seeded order
        self.inflight: dict[asyncio.Future, str] = {}
        self.cache: dict[str, str] = {}  # finished trial dir -> validity (immutable once result.json exists)
        self.excluded = set(l.split()[0] for l in (HERE / "excluded_infra.txt").read_text().splitlines() if l.strip()) \
            if (HERE / "excluded_infra.txt").exists() else set()
        self.endpoint_ok = True
        self.last_health = 0.0
        self.launched = 0
        self.done = collections.Counter()
        self.no_dir_streak = 0  # circuit breaker: trials dying before creating a trial dir
        self.pause_until = 0.0

    def scan(self) -> tuple[collections.Counter, collections.Counter, collections.Counter, int]:
        """-> valid, infra, pending (no result.json, outside our in-flight set), other_running."""
        valid, infra, pending = collections.Counter(), collections.Counter(), collections.Counter()
        ours = {os.path.basename(p) for p in self.inflight_dirs()}
        other_running = 0
        for t in glob.glob(str(HERE / "jobs/wave-*/format-code-task-*__*")):
            v = self.cache.get(t)
            if v is None:
                v, _ = classify(t)
                if v != "running":
                    self.cache[t] = v
            task = task_of(t)
            if v == "valid":
                valid[task] += 1
            elif v == "infra":
                infra[task] += 1
            elif os.path.basename(t) not in ours and time.time() - os.path.getmtime(t) < 4 * 3600:
                pending[task] += 1
                if JOB not in t:
                    other_running += 1
        return valid, infra, pending, other_running

    def inflight_dirs(self):
        return [str(TRIALS_DIR / n) for n in self.inflight_names.values()] if hasattr(self, "inflight_names") else []

    def reconcile(self) -> int:
        # excluded_infra.txt is the source of truth (a fixed task is re-admitted by deleting its line
        # and moving its infra trial dirs out of jobs/wave-*)
        f = HERE / "excluded_infra.txt"
        self.excluded = {l.split()[0] for l in f.read_text().splitlines() if l.strip()} if f.exists() else set()
        valid, infra, pending, other_running = self.scan()
        inflight = collections.Counter(self.inflight.values())
        new_excl = [t for t in self.order if infra[t] >= MAX_INFRA and valid[t] < N_ATTEMPTS and t not in self.excluded]
        if new_excl:
            self.excluded.update(new_excl)
            with open(HERE / "excluded_infra.txt", "a") as f:
                for t in new_excl:
                    f.write(f"{t}  # {infra[t]} infra attempts\n")
            log(f"excluded (persistent infra): {new_excl}")
        queue = []
        for t in self.order:
            if t in self.excluded:
                continue
            n = N_ATTEMPTS - valid[t] - pending[t] - inflight[t]
            queue += [t] * max(0, n)
        self.queue = queue
        complete = sum(1 for t in self.order if valid[t] >= N_ATTEMPTS)
        log(f"reconcile: complete_tasks={complete} queued_attempts={len(queue)} inflight={len(self.inflight)} "
            f"other_running={other_running} excluded={len(self.excluded)} launched={self.launched} done={dict(self.done)}")
        return other_running

    def check_endpoint(self) -> None:
        if time.time() - self.last_health < 120:
            return
        self.last_health = time.time()
        ok = subprocess.run([str(SRC / "endpoint_health.sh")], capture_output=True).returncode == 0
        if ok != self.endpoint_ok:
            log(f"endpoint {'UP' if ok else 'DOWN: pausing launches'}")
        self.endpoint_ok = ok

    async def run(self) -> None:
        TRIALS_DIR.mkdir(parents=True, exist_ok=True)
        self.inflight_names: dict[asyncio.Future, str] = {}
        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=160, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=1)
        conf, last_conf, last_rec, other_running = read_conf(), 0.0, 0.0, 0
        while True:
            now = time.time()
            if now - last_conf > 30:
                conf, last_conf = read_conf(), now
            if now - last_rec > conf["RECONCILE_SEC"]:
                other_running, last_rec = self.reconcile(), now
            self.check_endpoint()
            stop = (HERE / "STOP").exists()
            limit = max(0, int(conf["TARGET"]) - other_running)
            if not stop and self.endpoint_ok and self.queue and len(self.inflight) < limit and now >= self.pause_until:
                task = self.queue.pop(0)
                cfg = trial_config(task)
                fut = loop.run_in_executor(pool, worker_run_trial, cfg)
                self.inflight[fut] = task
                self.inflight_names[fut] = cfg["trial_name"]
                self.launched += 1
                await asyncio.sleep(1.0 / max(0.1, float(conf["RATE"])))
                continue
            if stop and not self.inflight:
                log("STOP and nothing in flight: exiting")
                break
            if not self.queue and not self.inflight and not other_running and now - last_rec < 5:
                log("all tasks have 4 valid attempts (or are excluded): done")
                break
            if self.inflight:
                done, _ = await asyncio.wait(list(self.inflight), timeout=5, return_when=asyncio.FIRST_COMPLETED)
                for fut in done:
                    task = self.inflight.pop(fut)
                    name = self.inflight_names.pop(fut)
                    d = fut.result()
                    v, detail = classify(d) if os.path.isdir(d) else ("infra", "no trial dir")
                    # circuit breaker: a run of infra outcomes (bad credentials, Daytona outage, ...) pauses launching
                    self.no_dir_streak = self.no_dir_streak + 1 if v == "infra" else 0
                    if self.no_dir_streak >= 20:
                        log(f"CIRCUIT BREAKER: 20 infra outcomes in a row (last: {detail[:80]}); pausing launches 300 s")
                        self.pause_until, self.no_dir_streak = time.time() + 300, 0
                    self.done[v] += 1
                    log(f"DONE {name} {v} {detail[:100]}")
            else:
                await asyncio.sleep(5)
                if not self.queue:
                    last_rec = 0.0  # nothing to launch: reconcile now
        pool.shutdown(wait=True)


if __name__ == "__main__":
    asyncio.run(Scheduler().run())
