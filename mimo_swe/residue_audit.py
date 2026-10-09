"""Image residue audit (no inference): build probe copies of tasks whose verifier is residue_probe.sh.

  residue_audit.py make <out_dir> <task_id...|@file>   -> <out_dir>/<task>: environment/ copied byte-for-byte
       (same build hash, so cached snapshots are reused), task.toml/instruction.md copied, tests/test.patch kept
       (the probe reads it for the novel-identifier scan), tests/test.sh = residue_probe.sh with __CWD__ filled in.
  residue_audit.py transplant <out_dir> <jobs_glob...> -> for every task with "future_pair" lines: a task copy whose
       verifier first copies those newer installed-copy files over the repo, then runs the task's real test.sh.
       Reward 1 = the installed copy holds a working fix (a confirmed leak), with no gold patch needed.
  residue_audit.py parse <jobs_glob...>                 -> TSV of RESIDUE lines per task (residue_lines.tsv on stdout)
Run the probe dirs with: AGENT=nop N_CONCURRENT=100 MIMO_RUN_ID=residue-audit run_harbor.sh <out_dir> <job>
"""
import glob
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path("/home/charlieruan/mimo")
TASKS = Path(os.environ.get("RESIDUE_TASKS", HERE / "harbor_tasks/mimo-code"))
PROBE = Path(__file__).resolve().parent / "residue_probe.sh"


def make(out: Path, ids: list[str]) -> None:
    probe = PROBE.read_text()
    for t in ids:
        src, dst = TASKS / t, out / t
        cwd = re.search(r"^WORKDIR (\S+)", (src / "environment/Dockerfile").read_text(), re.M).group(1)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src / "environment", dst / "environment")
        for f in ("task.toml", "instruction.md"):
            shutil.copy2(src / f, dst / f)
        (dst / "tests").mkdir()
        shutil.copy2(src / "tests/test.patch", dst / "tests/test.patch")
        (dst / "tests/test.sh").write_text(probe.replace("__CWD__", cwd))
        os.chmod(dst / "tests/test.sh", 0o755)


def transplant(out: Path, globs: list[str]) -> None:
    pairs: dict[str, dict[str, str]] = {}
    for g in globs:
        for tr in sorted(glob.glob(os.path.join(g, "format-code-task-*__*"))):
            f = os.path.join(tr, "verifier/test-stdout.txt")
            if not os.path.exists(f):
                continue
            task = os.path.basename(tr).split("__")[0]
            for line in open(f, errors="replace"):
                if line.startswith("RESIDUE future_pair "):
                    _, _, bp, cp = line.rstrip("\n").split(" ", 3)
                    pairs.setdefault(task, {}).setdefault(bp, cp)  # first copy wins per repo path
    for t, pp in sorted(pairs.items()):
        src, dst = TASKS / t, out / t
        cwd = re.search(r"^WORKDIR (\S+)", (src / "environment/Dockerfile").read_text(), re.M).group(1)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        orig = (src / "tests/test.sh").read_text()
        lines = "\n".join(f"{bp}\t{cp}" for bp, cp in pp.items())
        pre = (f"#!/bin/bash\n# TRANSPLANT: copy {len(pp)} newer installed-copy files over the repo, then the real verifier.\n"
               f"n=0; while IFS=$'\\t' read -r bp cp; do [ -f \"$cp\" ] && mkdir -p \"$(dirname {cwd}/$bp)\" && cp \"$cp\" \"{cwd}/$bp\" && n=$((n+1)); "
               f"done <<'EOF_PAIRS'\n{lines}\nEOF_PAIRS\necho \"transplanted=$n\"\n")
        (dst / "tests/test.sh").write_text(pre + orig.split("\n", 1)[1])
        os.chmod(dst / "tests/test.sh", 0o755)
    print(f"{len(pairs)} transplant tasks")


def parse(globs: list[str]) -> None:
    print("task\ttrial\tkind\trest")
    for g in globs:
        for tr in sorted(glob.glob(os.path.join(g, "format-code-task-*__*"))):
            f = os.path.join(tr, "verifier/test-stdout.txt")
            if not os.path.exists(f):
                continue
            task = os.path.basename(tr).split("__")[0]
            for line in open(f, errors="replace"):
                if line.startswith("RESIDUE "):
                    parts = line.rstrip("\n").split(" ", 2)
                    print(f"{task}\t{os.path.basename(tr)}\t{parts[1]}\t{parts[2] if len(parts) > 2 else ''}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "make":
        args = sys.argv[3:]
        ids = [l.split()[0] for l in open(args[0][1:]) if l.strip()] if args and args[0].startswith("@") else args
        make(Path(sys.argv[2]), ids)
    elif cmd == "transplant":
        transplant(Path(sys.argv[2]), sys.argv[3:])
    elif cmd == "parse":
        parse(sys.argv[2:])
