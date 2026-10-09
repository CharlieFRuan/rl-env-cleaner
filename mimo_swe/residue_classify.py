"""Pass-2 classification of self-copies (residue_probe.sh copy_vs_base + copy_history lines).

python residue_classify.py <jobs_glob...> > copies.tsv
A copy is GENUINE when most of its matched files are byte-identical to BASE or to an older BASE revision
(same + in_history >= 50% of matched, matched >= 3): it really is this project. Among genuine copies:
  FUTURE  some differing file's content never occurs in BASE's history of that path -> written after BASE
          (newer release / post-fix build) or by a fork: the leak candidates
  OLDER   every differing file is an older revision of the repo file
  SAME    identical to BASE
Non-genuine matches (name collisions, e.g. index.js) are reported as WEAK.
"""
import glob
import os
import re
import sys


def main():
    print("task\tclass\tdir\tmatched\tsame\tdiffer\tin_hist\tfuture\tcommits\tfuture_files")
    for g in sys.argv[1:]:
        for tr in sorted(glob.glob(os.path.join(g, "format-code-task-*__*"))):
            f = os.path.join(tr, "verifier/test-stdout.txt")
            if not os.path.exists(f):
                continue
            task = os.path.basename(tr).split("__")[0]
            copies, hist = {}, {}
            for l in open(f, errors="replace"):
                if l.startswith("RESIDUE copy_vs_base "):
                    m = dict(re.findall(r"(\w+)=(\S+)", l))
                    copies[m["dir"]] = m
                elif l.startswith("RESIDUE copy_history "):
                    m = dict(re.findall(r"(\w+)=(\S+)", l))
                    m["files"] = " ".join(l.split("commits=")[1].split()[1:])
                    hist[m["dir"]] = m
            for d, c in copies.items():
                matched, same, differ = int(c["matched"]), int(c["same"]), int(c["differ"])
                h = hist.get(d, {"in_history": "0", "not_in_history": "0", "commits": "?", "files": ""})
                ih, fut = int(h["in_history"]), int(h["not_in_history"])
                if matched < 3 or (same + ih) < 0.5 * matched:
                    cls = "WEAK"
                elif differ == 0:
                    cls = "SAME"
                elif fut > 0:
                    cls = "FUTURE"
                else:
                    cls = "OLDER"
                print("\t".join(map(str, [task, cls, d, matched, same, differ, ih, fut, h["commits"], h["files"][:200]])))


if __name__ == "__main__":
    main()
