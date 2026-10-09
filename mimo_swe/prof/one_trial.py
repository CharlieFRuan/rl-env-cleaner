"""Run ONE profiling trial of a given task through the same code path as continuous_runner (for validating stats).
usage: run_prof.sh-style env + python one_trial.py <task-id>"""
import sys, os
sys.path.insert(0, "/home/charlieruan/mimo/rl-env-cleaner/mimo_swe")
import continuous_runner as cr
task = sys.argv[1]
cfg = cr.trial_config(task)
if os.environ.get("MIMO_OVERRIDE_CPUS"):
    cfg["environment"]["override_cpus"] = int(os.environ["MIMO_OVERRIDE_CPUS"])
print("cpus:", cfg["environment"]["override_cpus"], "mem_mb:", cfg["environment"]["override_memory_mb"], "storage_mb:", cfg["environment"]["override_storage_mb"])
cfg["trials_dir"] = str(cr.HERE / "jobs" / os.environ.get("MIMO_JOB", "prof-test"))
os.makedirs(cfg["trials_dir"], exist_ok=True)
print("trial dir:", cr.worker_run_trial(cfg).replace(str(cr.TRIALS_DIR), cfg["trials_dir"]))
