"""Per-trial resource table for the profiling pass (jobs/prof-r1) -> prof/analysis/trials.csv, tasks.csv.

Per trial: phase durations (result.json), cgroup lifetime counters printed by TEST_SH (memory.peak, oom_kill,
cpu.stat, PSI), the sampler's running peaks (non-reclaimable memory = anon+kernel+shmem, cores, disk, pids), and
LLM / tool time from mini-swe-agent's per-message timestamps. Per task: max (or sum, for counts) over attempts.
"""
import csv, glob, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

JOB = sys.argv[1] if len(sys.argv) > 1 else "/home/charlieruan/mimo/jobs/prof-r1"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/home/charlieruan/mimo/prof/analysis"
sys.path.insert(0, "/home/charlieruan/mimo/rl-env-cleaner/mimo_swe")
from trial_status import classify  # noqa: E402


def ts(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() if x else None


def dur(r, k):
    p = r.get(k) or {}
    a, b = ts(p.get("started_at")), ts(p.get("finished_at"))
    return round(b - a, 1) if a and b else None


def kv(s):
    return {k: int(v) for k, v in re.findall(r"(\w+)[= ](\d+)", s)}


def one(d):
    try:
        r = json.load(open(d + "/result.json"))
    except Exception:
        return None
    v, detail = classify(d)
    e = (r.get("exception_info") or {}).get("exception_type") or ""
    row = {"task": os.path.basename(d).split("__")[0], "trial": os.path.basename(d), "status": v, "exception": e,
           "verifier_timeout": int(e == "VerifierTimeoutError"),
           "reward": ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
           "env_setup_s": dur(r, "environment_setup"), "agent_setup_s": dur(r, "agent_setup"),
           "agent_s": dur(r, "agent_execution"), "verifier_s": dur(r, "verifier")}
    try:
        out = open(d + "/verifier/test-stdout.txt", errors="ignore").read()
    except Exception:
        out = ""
    m = re.search(r"mimo_prof_peaks (.*)", out)
    pk = kv(m.group(1)) if m else {}
    m = re.search(r"mimo_prof_counters (.*)", out)
    c = kv(m.group(1)) if m else {}
    m = re.search(r"mimo_cpu_stat=(.*?)\s+mimo_cpu_max", out)
    cs = kv(m.group(1)) if m else {}
    m = re.search(r"mimo_uptime_s=([\d.]+)", out)
    up = float(m.group(1)) if m else None
    m = re.search(r"cpu=\[some .*?total=(\d+)", out)
    row.update({
        "profiled": int(bool(pk and c)),
        "mem_peak_gib": round(c["mem_peak"] / 2**30, 3) if "mem_peak" in c else None,  # incl. page cache
        "nonfile_peak_gib": round(pk["peak_nonfile"] / 2**30, 3) if "peak_nonfile" in pk else None,
        "oom_kill": c.get("oom_kill"), "pids_peak": c.get("pids_peak"),
        "peak_cores": round(pk["peak_cores_milli"] / 1000, 2) if "peak_cores_milli" in pk else None,
        "avg_cores": round(cs["usage_usec"] / 1e6 / up, 2) if cs.get("usage_usec") and up else None,
        "cpu_s": round(cs["usage_usec"] / 1e6) if "usage_usec" in cs else None,
        "sys_frac": round(cs["system_usec"] / cs["usage_usec"], 2) if cs.get("usage_usec") else None,
        "throttled_frac": round(cs["nr_throttled"] / cs["nr_periods"], 2) if cs.get("nr_periods") else None,
        "cpu_stall_s": round(int(m.group(1)) / 1e6) if m else None,
        "disk_peak_gib": round(pk["peak_disk_used_kb"] / 2**20, 2) if "peak_disk_used_kb" in pk else None,
    })
    llm = tool = 0.0
    n_tool = 0
    try:
        msgs = json.load(open(d + "/agent/mini-swe-agent.trajectory.json"))["messages"]
        last_a = last_t = None
        for x in msgs:
            t = (x.get("extra") or {}).get("timestamp")
            if t is None:
                continue
            if x["role"] == "assistant":
                if last_t:
                    llm += t - last_t
                last_a = t
            elif x["role"] == "tool" and last_a:
                tool += t - last_a; n_tool += 1; last_t = t
    except Exception:
        pass
    row.update({"llm_s": round(llm), "tool_s": round(tool), "n_tool": n_tool,
                "tool_mean_s": round(tool / n_tool, 2) if n_tool else None})
    return row


if __name__ == "__main__":
    dirs = [d for d in glob.glob(JOB + "/format-code-task-*__*") if os.path.exists(d + "/result.json")]
    with ProcessPoolExecutor(64) as ex:
        rows = [x for x in ex.map(one, dirs, chunksize=32) if x]
    os.makedirs(OUT, exist_ok=True)
    with open(OUT + "/trials.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by.setdefault(r["task"], []).append(r)
    mx = lambda rs, k: max((r[k] for r in rs if r[k] is not None), default=None)
    tasks = []
    for t, rs in sorted(by.items()):
        tasks.append({"task": t, "attempts": len(rs), "valid": sum(r["status"] == "valid" for r in rs),
                      "profiled": sum(r["profiled"] for r in rs),
                      "verifier_timeouts": sum(r["verifier_timeout"] for r in rs),
                      "oom_attempts": sum(1 for r in rs if (r["oom_kill"] or 0) > 0),
                      **{f"max_{k}": mx(rs, k) for k in ["mem_peak_gib", "nonfile_peak_gib", "peak_cores", "avg_cores",
                                                         "throttled_frac", "sys_frac", "cpu_stall_s", "disk_peak_gib",
                                                         "pids_peak", "verifier_s", "agent_s", "tool_mean_s", "env_setup_s"]}})
    with open(OUT + "/tasks.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(tasks[0])); w.writeheader(); w.writerows(tasks)
    print(len(rows), "trials,", len(tasks), "tasks ->", OUT)
