"""Scored-run report for the MiMo SWE eval: per-attempt JSONL + pass@1 / pass@4 with bootstrap CIs.

python make_report.py [--jobs "jobs/wave-*"] [--out report] [--min-attempts 4]

Validity comes from trial_status.classify (valid / infra / running). Only tasks with >= N valid attempts
count (first N by start time). Excluded from the headline: excluded_infra.txt (persistent infra),
broken_nop.txt (null agent scores 1), leak_flagged.txt (trace review; also reported counted-as-failed).
"""

import argparse
import glob
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trial_status import CONTEXT_RX, agent_tail, classify, read, task_of  # noqa: E402

HERE = "/home/charlieruan/mimo"
UPSTREAM_RX = re.compile(
    r"(curl|wget|git\s+clone|git\s+fetch|git\s+ls-remote|pip3?\s+download|pip3?\s+install\s+\S+==|npm\s+(pack|view)|"
    r"go\s+(get|mod\s+download)\s+\S+@|cargo\s+(search|download))[^\n]*"
    r"(github|gitlab|bitbucket|pypi|pythonhosted|npmjs|yarnpkg|golang|goproxy|crates\.io|maven|jsdelivr|unpkg|rubygems|packagist)",
    re.I,
)
NET_OK_RX = re.compile(r"HTTP/\S+ 200|Cloning into|Receiving objects|Saved to|Successfully downloaded|\bdiff --git\b", re.I)
GIT_ARCH_RX = re.compile(r"\bgit\s+(show|log|reflog|checkout|cat-file|rev-list|fsck|stash\s+show|branch\s+-a|tag)\b")


def load_list(path: str) -> dict[str, str]:
    out = {}
    if os.path.exists(path):
        for line in open(path):
            item, _, why = line.partition("#")
            if item.strip():
                out[item.strip()] = why.strip()
    return out


def commands(traj: dict):
    """(command, output) pairs from a mini-swe-agent trajectory."""
    pending = []
    for m in traj.get("messages", []):
        if m.get("role") == "assistant":
            pending = []
            for tc in m.get("tool_calls") or []:
                try:
                    pending.append(json.loads(tc["function"]["arguments"]).get("command", ""))
                except Exception:  # noqa: BLE001
                    pending.append(str(tc))
        elif m.get("role") == "tool" and pending:
            yield pending.pop(0), str(m.get("content", ""))


def auto_leak_flags(traj: dict) -> list[str]:
    flags = set()
    for cmd, out in commands(traj):
        if UPSTREAM_RX.search(cmd):
            flags.add("upstream-fetch-ok" if NET_OK_RX.search(out) else "upstream-fetch-attempt")
        if GIT_ARCH_RX.search(cmd) and re.search(r"\b[0-9a-f]{7,40}\b", cmd):
            flags.add("git-archaeology")
    return sorted(flags)


def error_class(trial: str, reward, exit_status: str, exc: str) -> str:
    if reward == 1:
        return "solved"
    tail = agent_tail(trial)
    if CONTEXT_RX.search(tail[-6000:]) or "ContextWindowExceeded" in exit_status:
        return "context-overflow"
    if "Limit" in exit_status:
        return "step-limit"
    if "AgentTimeoutError" in exc:
        return "agent-timeout"
    if re.search(r"exit code 137|exit 137|^Killed$|returncode.{0,4}137", exc + "\n" + tail[-4000:], re.M):
        return "agent-oom"
    if exc or (exit_status and exit_status != "Submitted"):
        return "agent-crash"
    return "wrong-answer"


def trial_row(trial: str, flagged: dict) -> dict:
    validity, detail = classify(trial)
    res = {}
    try:
        res = json.load(open(os.path.join(trial, "result.json")))
    except Exception:  # noqa: BLE001
        pass
    traj = {}
    try:
        traj = json.load(open(os.path.join(trial, "agent", "mini-swe-agent.trajectory.json")))
    except Exception:  # noqa: BLE001
        pass
    info = traj.get("info", {})
    exit_status = info.get("exit_status") or next(
        (m.get("extra", {}).get("exit_status") for m in traj.get("messages", []) if m.get("role") == "exit"), "") or ""
    rf = os.path.join(trial, "verifier", "reward.txt")
    reward = int(read(rf).strip() == "1") if os.path.exists(rf) else None
    exc = read(os.path.join(trial, "exception.txt"))
    ar = res.get("agent_result") or {}
    steps = info.get("model_stats", {}).get("api_calls")
    if steps is None:
        steps = sum(1 for m in traj.get("messages", []) if m.get("role") == "assistant")
    name, task = os.path.basename(trial), task_of(trial)
    leak = auto_leak_flags(traj)
    for key in (task, name):
        if key in flagged:
            leak.append("review:" + (flagged[key] or "flagged"))
    return dict(
        task=task, trial=name, job=os.path.basename(os.path.dirname(trial)), dir=trial,
        validity=validity, detail=detail, reward=reward,
        error_class=error_class(trial, reward, exit_status, exc) if validity == "valid" else validity + ":" + detail.split(":")[0],
        steps=steps, input_tokens=ar.get("n_input_tokens"), cache_tokens=ar.get("n_cache_tokens"),
        output_tokens=ar.get("n_output_tokens"), exit_status=exit_status, leak_flags=leak,
        review_flagged=any(f.startswith("review:") for f in leak),
        started_at=res.get("started_at"), finished_at=res.get("finished_at"),
    )


def bootstrap(cs: list[int], n_att: int, iters: int = 10000, seed: int = 0):
    rng = random.Random(seed)
    p1, p4 = [], []
    for _ in range(iters):
        s = [cs[rng.randrange(len(cs))] for _ in cs]
        p1.append(sum(c / n_att for c in s) / len(s))
        p4.append(sum(c >= 1 for c in s) / len(s))
    q = lambda xs: (sorted(xs)[int(0.025 * iters)], sorted(xs)[int(0.975 * iters) - 1])  # noqa: E731
    return q(p1), q(p4)


def headline(cs: dict[str, int], n_att: int) -> dict:
    vals = list(cs.values())
    if not vals:
        return dict(n_tasks=0)
    (l1, h1), (l4, h4) = bootstrap(vals, n_att)
    return dict(n_tasks=len(vals), pass_at_1=sum(c / n_att for c in vals) / len(vals), pass_at_1_ci95=[l1, h1],
                pass_at_k=sum(c >= 1 for c in vals) / len(vals), pass_at_k_ci95=[l4, h4], k=n_att)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=f"{HERE}/jobs/wave-*")
    ap.add_argument("--out", default=f"{HERE}/report")
    ap.add_argument("--min-attempts", type=int, default=4)
    a = ap.parse_args()
    n = a.min_attempts
    os.makedirs(a.out, exist_ok=True)

    flagged = load_list(f"{HERE}/leak_flagged.txt")
    excl = {"infra-persistent": load_list(f"{HERE}/excluded_infra.txt"), "broken-nop": load_list(f"{HERE}/broken_nop.txt")}
    trials = sorted(t for t in glob.glob(os.path.join(a.jobs, "format-code-task-*__*")) if os.path.isdir(t))
    rows = [trial_row(t, flagged) for t in trials]

    by_task = defaultdict(list)
    for r in rows:
        if r["validity"] == "valid":
            by_task[r["task"]].append(r)
    for rs in by_task.values():
        rs.sort(key=lambda r: r["started_at"] or "")
        for i, r in enumerate(rs):
            r["attempt"] = i
    with open(os.path.join(a.out, "attempts.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    complete = {t: rs[:n] for t, rs in by_task.items() if len(rs) >= n}
    excluded = defaultdict(list)
    counted = {}
    for t, rs in complete.items():
        reason = next((k for k, v in excl.items() if t in v), None)
        if reason is None and any(r["review_flagged"] for r in rs):
            reason = "leak-flagged"
        if reason:
            excluded[reason].append(t)
        else:
            counted[t] = sum(r["reward"] == 1 for r in rs)
    for k, v in excl.items():  # excluded tasks that never completed still get listed
        for t in v:
            if t not in complete and t not in excluded[k]:
                excluded[k].append(t)
    leak_as_fail = dict(counted)
    for t in excluded.get("leak-flagged", []):
        leak_as_fail[t] = sum(r["reward"] == 1 and not r["review_flagged"] for r in complete[t])

    used = [r for rs in complete.values() for r in rs if r["task"] in counted]
    summary = dict(
        headline=headline(counted, n),
        with_leak_flagged_as_failures=headline(leak_as_fail, n),
        n_trials_total=len(rows), validity=dict(Counter(r["validity"] for r in rows)),
        infra_details=dict(Counter(r["error_class"] for r in rows if r["validity"] == "infra").most_common()),
        tasks_with_valid_attempts=len(by_task), tasks_complete=len(complete),
        excluded={k: sorted(v) for k, v in excluded.items()},
        failure_classes=dict(Counter(r["error_class"] for r in used).most_common()),
        auto_leak_flags=dict(Counter(f for r in rows for f in r["leak_flags"] if not f.startswith("review:"))),
        mean_steps=sum(r["steps"] or 0 for r in used) / max(1, len(used)),
        mean_input_tokens=sum(r["input_tokens"] or 0 for r in used) / max(1, len(used)),
        mean_output_tokens=sum(r["output_tokens"] or 0 for r in used) / max(1, len(used)),
    )
    cfg_path = f"{HERE}/report_config.json"
    summary["config"] = json.load(open(cfg_path)) if os.path.exists(cfg_path) else None
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=2)

    h, hl = summary["headline"], summary["with_leak_flagged_as_failures"]
    fmt = lambda x: f"{x:.4f}"  # noqa: E731
    md = ["# MiMo-V2.6-RL-oss SWE: Qwen3.6-35B-A3B (mini-swe-agent 2.4.6, Harbor, Daytona)", ""]
    if h.get("n_tasks"):
        md += [f"- **Tasks counted** (all {n} attempts valid, not excluded): **{h['n_tasks']}**",
               f"- **pass@1** = {fmt(h['pass_at_1'])} (95% bootstrap CI {fmt(h['pass_at_1_ci95'][0])}–{fmt(h['pass_at_1_ci95'][1])})",
               f"- **pass@{n}** = {fmt(h['pass_at_k'])} (95% CI {fmt(h['pass_at_k_ci95'][0])}–{fmt(h['pass_at_k_ci95'][1])})"]
        if hl["n_tasks"] != h["n_tasks"]:
            md.append(f"- With leak-flagged tasks counted (flagged solves as failures): n={hl['n_tasks']}, "
                      f"pass@1={fmt(hl['pass_at_1'])}, pass@{n}={fmt(hl['pass_at_k'])}")
    else:
        md.append("- No task has all attempts complete yet.")
    md += ["", "## Coverage and exclusions", f"- Trials: {len(rows)} total; validity {summary['validity']}",
           f"- Tasks with ≥1 valid attempt: {len(by_task)}; complete: {len(complete)}"]
    md += [f"- Excluded ({k}): {len(v)}" + (f" — {', '.join(sorted(v)[:30])}" if v else "") for k, v in excluded.items()]
    md += ["", "## Infra errors (not counted, re-queued)"] + [f"- {k}: {v}" for k, v in summary["infra_details"].items()]
    md += ["", "## Attempt outcomes (counted tasks)"] + [f"- {k}: {v}" for k, v in summary["failure_classes"].items()]
    md += [f"- mean steps {summary['mean_steps']:.1f}, mean input tokens {summary['mean_input_tokens']:.0f}, "
           f"mean output tokens {summary['mean_output_tokens']:.0f}"]
    md += ["", "## Automatic leak scan (all trials)"] + [f"- {k}: {v}" for k, v in summary["auto_leak_flags"].items()]
    md += ["", "## Config", "```json", json.dumps(summary["config"], indent=2), "```", "",
           f"Per-attempt results: `{os.path.join(a.out, 'attempts.jsonl')}`"]
    open(os.path.join(a.out, "report.md"), "w").write("\n".join(md) + "\n")

    if h.get("n_tasks"):
        print(f"n_tasks={h['n_tasks']} pass@1={fmt(h['pass_at_1'])} pass@{n}={fmt(h['pass_at_k'])} "
              f"trials={len(rows)} excluded={ {k: len(v) for k, v in excluded.items()} } -> {a.out}")
    else:
        print(f"n_tasks=0 trials={len(rows)} validity={summary['validity']} -> {a.out}")


if __name__ == "__main__":
    main()
