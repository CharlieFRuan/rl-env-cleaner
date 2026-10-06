"""Classify every finished trial in jobs/smoke-b*: solved, real test failure, or a likely infra problem.

python3 audit.py            # summary + suspicious trials
python3 audit.py --all      # every trial
"""

import glob
import json
import os
import re
import sys

ROOT = os.environ.get("MIMO_JOBS", "/home/charlieruan/mimo/jobs")
GLOB = os.environ.get("MIMO_TRIAL_GLOB", "*/format-code-task-*")
SUSPECT = [  # (label, regex on verifier stdout) — infra-shaped failures, checked in order
    ("verifier-oom", r"^Killed$|verifier_returncode=137"),
    ("patch-failed", r"apply_test_patch_failed|error: patch failed|^error: \S+: patch does not apply|already exists in working directory"),
    ("cmd-not-found", r"command not found|: not found$|No such file or directory.*(go|python|node|java|mvn|cargo|npm|npx|pytest|gradle)"),
    ("network", r"(?-i:Could not resolve|Temporary failure in name resolution|ECONNREFUSED|ENOTFOUND|getaddrinfo|Connection refused)"),
    ("verifier-timeout", r"verifier_returncode=124|timed out"),
    ("no-tests-ran", r"no tests ran|Ran 0 tests|No tests found|0 passed|collected 0 items"),
]


def classify(trial: str) -> tuple[str, str]:
    exc = os.path.join(trial, "exception.txt")
    reward_f = os.path.join(trial, "verifier", "reward.txt")
    out_f = os.path.join(trial, "verifier", "test-stdout.txt")
    err_f = os.path.join(trial, "verifier", "error.txt")
    if os.path.exists(exc):
        txt = open(exc, errors="replace").read()
        m = re.search(r"(\w+(?:Error|Exception|Timeout)\w*)", txt.split("\n")[-1] or txt) or re.search(r"(\w+(?:Error|Exception))", txt)
        code = re.search(r"exit (\d+)", txt)
        detail = (m.group(1) if m else "exception") + (f" exit {code.group(1)}" if code else "")
        if not os.path.exists(reward_f):
            return "trial-exception", detail
    if not os.path.exists(reward_f):
        return "running", ""
    if open(reward_f).read().strip() == "1":
        return "solved", ""
    if os.path.exists(err_f):
        return "patch-failed", open(err_f).read().strip()
    out = open(out_f, errors="replace").read() if os.path.exists(out_f) else ""
    for label, rx in SUSPECT:
        m = re.search(rx, out, re.M | re.I)
        if m and label == "no-tests-ran" and re.search(r"\b[1-9]\d* (passed|tests? passed)|Ran [1-9]", out):
            continue  # a progress line said 0, the summary didn't
        if m:
            return label, m.group(0)[:120]
    rc = re.search(r"verifier_returncode=(\d+)", out)
    return "test-failed", f"rc={rc.group(1) if rc else '?'}"


def main() -> None:
    rows = []
    for trial in sorted(glob.glob(f"{ROOT}/{GLOB}")):
        cls, detail = classify(trial)
        rows.append((trial.split("/")[-2], trial.split("/")[-1].split("__")[0], cls, detail))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    graded = sum(v for k, v in counts.items() if k not in ("running", "trial-exception"))
    print(json.dumps(counts), f"| solved {counts.get('solved', 0)}/{graded} graded")
    for job, task, cls, detail in rows:
        if "--all" in sys.argv or cls not in ("solved", "test-failed", "running"):
            print(f"{job} {task} {cls}: {detail}")


if __name__ == "__main__":
    main()
