"""Classify Harbor trials of the scored run and decide which tasks still need attempts.

A trial is VALID when the verifier produced a verdict and nothing infra-side broke the attempt:
  * infra (invalid, re-queued): no verdict (sandbox build/start failures, cancelled, verifier crash),
    or the agent died on the model endpoint (connection errors / 5xx / 429 / Cloudflare 52x),
    or the verifier itself was killed by the environment.
  * agent outcomes (valid, count as failed attempts): wrong answer, context overflow, step limit,
    agent timeout, agent OOM-killed, any other agent crash.

python trial_status.py need <order_file> <jobs_glob...>      -> "task need n_infra" lines (need = 4 - valid)
python trial_status.py summary <jobs_glob...>                -> counts by class
"""

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

N_ATTEMPTS = 4
ENDPOINT_RX = re.compile(
    r"APIConnectionError|InternalServerError|ServiceUnavailableError|BadGatewayError|RateLimitError"
    r"|Error code: 5\d\d|status code 5\d\d|Error code: 429|Connection (refused|reset|error)|error code: 52[0-9]"
    r"|trycloudflare|no healthy backend|upstream error|Timeout(Error)?: .*Request timed out|APITimeoutError"
)
CONTEXT_RX = re.compile(r"ContextWindowExceeded|maximum context length|context length|max_model_len|is longer than the model")


def read(p: str, n: int | None = None) -> str:
    try:
        with open(p, errors="replace") as f:
            s = f.read()
        return s[-n:] if n else s
    except OSError:
        return ""


def task_of(trial: str) -> str:
    return os.path.basename(trial).split("__")[0]


def agent_tail(trial: str) -> str:
    out = ""
    for f in ("agent/mini-swe-agent.txt", "agent/command-1/stdout.txt", "agent/command-0/stdout.txt"):
        out += read(os.path.join(trial, f), 20000)
    return out


def classify(trial: str) -> tuple[str, str]:
    """-> (validity, detail). validity in {valid, infra, running}."""
    reward_f = os.path.join(trial, "verifier", "reward.txt")
    exc = read(os.path.join(trial, "exception.txt"))
    exc_last = exc.strip().splitlines()[-1] if exc.strip() else ""
    has_result = os.path.exists(os.path.join(trial, "result.json"))
    if not os.path.exists(reward_f):
        if not has_result:
            return "running", ""
        return "infra", "no-verdict: " + exc_last[:160]
    tail = agent_tail(trial)
    exc_name = re.search(r"(\w+(Error|Exception))", exc_last)
    exc_name = exc_name.group(1) if exc_name else ""
    if "VerifierTimeoutError" in exc or "Verifier" in exc_name:
        return "infra", "verifier-error: " + exc_last[:160]
    vout = read(os.path.join(trial, "verifier", "test-stdout.txt"), 4000)
    if re.search(r"^Killed$|verifier_returncode=137", vout, re.M):
        return "infra", "verifier-oom"
    if exc_name and exc_name not in ("AgentTimeoutError",) and ENDPOINT_RX.search(tail) and not CONTEXT_RX.search(tail[-3000:]):
        return "infra", "endpoint-error: " + (ENDPOINT_RX.search(tail).group(0))
    return "valid", exc_name


def trials(globs: list[str]) -> list[str]:
    out = []
    for g in globs:
        out += [t for t in glob.glob(os.path.join(g, "format-code-task-*__*")) if os.path.isdir(t)]
    return sorted(out)


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "need":
        order = [l.strip() for l in open(sys.argv[2]) if l.strip()]
        valid = Counter()
        infra = Counter()
        for t in trials(sys.argv[3:]):
            v, _ = classify(t)
            if v == "valid":
                valid[task_of(t)] += 1
            elif v == "infra":
                infra[task_of(t)] += 1
        for task in order:
            need = N_ATTEMPTS - min(N_ATTEMPTS, valid[task])
            if need > 0:
                print(task, need, infra[task])
    elif cmd == "summary":
        c = Counter()
        details = defaultdict(Counter)
        for t in trials(sys.argv[2:]):
            v, d = classify(t)
            c[v] += 1
            details[v][d.split(":")[0]] += 1
        print(dict(c))
        for v, dc in details.items():
            print(v, dict(dc.most_common(12)))


if __name__ == "__main__":
    main()
