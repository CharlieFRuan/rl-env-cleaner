"""Rank tasks by residue-probe findings (residue_probe.sh output in jobs/<job>/<trial>/verifier/test-stdout.txt).

python residue_triage.py <jobs_glob...> > residue_triage.tsv
Signals (per task, newest trial):
  copy_differ     self-copy (site-packages / node_modules / go mod / cargo / vendor) whose source differs from BASE
  copy_same       self-copy identical to BASE in every matched file (benign: same version)
  rare_max        most distinct rare hidden-test identifiers found in one untracked file (>= 2: strong)
  second_checkout another directory holding the project (top-level overlap >= 50%)
  pyc_orphan / pyc_stale   repo bytecode without source / not matching source
  mtime_late      tracked files > 1 day newer than the median
score = 3*[rare_max>=3] + 2*[rare_max==2] + 2*[copy_differ] + 2*[second_checkout] + [pyc_orphan] + [pyc_stale]
"""
import glob
import os
import re
import sys
from collections import defaultdict

SKIP_RARE = re.compile(r"/usr/share/doc/|/usr/share/man/|\.git/|/proc/")


def task_lines(globs):
    latest = {}
    for g in globs:
        for tr in glob.glob(os.path.join(g, "format-code-task-*__*")):
            f = os.path.join(tr, "verifier/test-stdout.txt")
            if os.path.exists(f):
                t = os.path.basename(tr).split("__")[0]
                if t not in latest or os.path.getmtime(f) > os.path.getmtime(latest[t]):
                    latest[t] = f
    for t, f in sorted(latest.items()):
        yield t, [l.rstrip("\n")[8:] for l in open(f, errors="replace") if l.startswith("RESIDUE ")]


def main():
    print("task\tscore\tdone\tcopy_differ\tcopy_same\trare_max\trare_file\tsecond_checkout\tpyc_orphan\tpyc_stale\tmtime_late\tnotes")
    for t, lines in task_lines(sys.argv[1:]):
        s = defaultdict(int)
        notes, rare_file = [], ""
        for l in lines:
            k, _, rest = l.partition(" ")
            if k == "copy_vs_base":
                m = dict(re.findall(r"(\w+)=(\S+)", rest))
                if int(m["differ"]) > 0:
                    s["copy_differ"] += 1
                    notes.append(f"differ:{m['dir']}({m['differ']}/{m['matched']})")
                elif int(m["matched"]) > 0:
                    s["copy_same"] += 1
            elif k == "rare_hit":
                n = int(re.search(r"distinct=(\d+)", rest).group(1))
                f = rest.split(" ")[-1]
                if not SKIP_RARE.search(f) and n > s["rare_max"]:
                    s["rare_max"], rare_file = n, f
            elif k == "second_checkout":
                a, b = map(int, re.search(r"overlap=(\d+)/(\d+)", rest).groups())
                if b and a / b >= 0.5:
                    s["second_checkout"] += 1
                    notes.append("checkout:" + rest.split(" ")[0])
            elif k == "pyc_summary":
                m = dict(re.findall(r"(\w+)=(\d+)", rest))
                s["pyc_orphan"], s["pyc_stale"] = int(m["orphan"]), int(m["stale"])
            elif k == "mtime":
                s["mtime_late"] = int(re.search(r"newer_than_median_plus_1d=(\d+)", rest).group(1))
            elif k in ("npm_link_outside", "pth_points_elsewhere", "cwd_missing"):
                notes.append(f"{k}:{rest[:80]}")
            elif k == "done":
                s["done"] = 1
        score = (3 if s["rare_max"] >= 3 else 2 if s["rare_max"] == 2 else 0) + 2 * bool(s["copy_differ"]) \
            + 2 * bool(s["second_checkout"]) + bool(s["pyc_orphan"]) + bool(s["pyc_stale"])
        print("\t".join(map(str, [t, score, s["done"], s["copy_differ"], s["copy_same"], s["rare_max"], rare_file,
                                  s["second_checkout"], s["pyc_orphan"], s["pyc_stale"], s["mtime_late"], " ".join(notes)[:400]])))


if __name__ == "__main__":
    main()
