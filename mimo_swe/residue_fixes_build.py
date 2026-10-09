"""Build residue_fixes.tsv (input of mimo_to_harbor.py --residue-fixes-file) from the residue audit outputs.

python residue_fixes_build.py <copies.tsv> <release_dates.tsv> <pass-probe jobs_glob> <triage_all.tsv> > residue_fixes.tsv
Rules (RESIDUE_AUDIT.md):
  * sync every installed copy of the project with post-BASE files: residue_classify FUTURE (a genuine copy), or a
    published release of the project dated after BASE (release_dates RELEASED_AFTER_BASE / unknown); except
    toolchain bundles (Node's own npm/corepack), which can't be rewritten without breaking the toolchain;
    package-manager cache entries of such releases are deleted instead.
  * pycache for every task with stale or orphan repo bytecode.
  * MANUAL: hand-verified cases the generic detectors can't express (renamed dists, caches, build outputs, checkouts).
"""
import csv
import glob
import os
import re
import sys

TOOLCHAIN = re.compile(r"^/opt/node-|^/usr/(local/)?lib/node_modules/|/corepack/")
PKG_CACHE = re.compile(r"^(/usr/local/share/\.cache/yarn/v6/[^/]+)/|^(/root/\.cache/yarn/v6/[^/]+)/")
MANUAL = [
    # hidden test (and compiled fix?) in a Go build cache outside MiMo's scrub list; GOCACHE rebuilds offline
    ("format-code-task-000556", "rm", "/opt/stage4a-go-build-cache"),
    # Deno's compile cache holds the hidden test string.test.ts
    ("format-code-task-000699", "rm", "/root/.cache/deno/gen"),
    # compiled hidden test in the untracked build output (the verifier rebuilds from .build_env)
    ("format-code-task-000046", "rm", "/testbed/lib/test"),
    # prebuilt bundle and newer published futil both contain the new tree functions
    ("format-code-task-002679", "rm", "/testbed/lib/futil-js.js"),
    ("format-code-task-002679", "rm", "/testbed/lib/futil-js.js.map"),
    ("format-code-task-002679", "rm", "/testbed/node_modules/futil"),
    # second checkout of the project next to the repo (verifier uses /workspace/repo only)
    ("format-code-task-002517", "rm", "/testbed"),
    ("format-code-task-002790", "rm", "/testbed"),
    # installed copies under a different distribution name than the repo's (not matched automatically)
    ("format-code-task-003058", "sync", "/testbed/.venv/lib/python3.12/site-packages/OFS", "src/OFS/"),
    ("format-code-task-000195", "sync", "/usr/local/lib/python3.12/dist-packages/paddle", "python/paddle/"),
    ("format-code-task-000255", "sync", "/usr/local/lib/python3.12/dist-packages/theano", "theano/"),
]
# name matches that are not the project under test (vendored third-party libs, generic top-level dirs)
# 002607: compiled scipy built from the fixed source; base Python files on that build do not import -> exclude the task.
SKIP = re.compile(r"^format-code-task-002607\t|^format-code-task-000437\t|^format-code-task-002142\t.*/(devtools|examples|docs)$")


def main():
    copies_tsv, dates_tsv, jobs_glob, triage_tsv = sys.argv[1:5]
    dates = {(r["task"], r["dir"]): r["verdict"] for r in csv.DictReader(open(dates_tsv), delimiter="\t")}
    # prefix + kind per copy from the probe output
    meta, pairs = {}, {}
    for tr in glob.glob(os.path.join(jobs_glob, "format-code-task-*__*")):
        f = os.path.join(tr, "verifier/test-stdout.txt")
        if os.path.exists(f):
            t = os.path.basename(tr).split("__")[0]
            for l in open(f, errors="replace"):
                m = re.match(r"RESIDUE copy_vs_base kind=(\S+) dir=(\S+) .* prefix=(\S*)", l)
                if m:
                    meta[(t, m.group(2))] = (m.group(1), m.group(3))
                m = re.match(r"RESIDUE future_pair (\S+) (\S+)", l)
                if m:
                    pairs.setdefault(t, []).append((m.group(1), m.group(2)))
    out = []
    for r in csv.DictReader(open(copies_tsv), delimiter="\t"):
        t, d, cls = r["task"], r["dir"], r["class"]
        if int(r["future"]) == 0:
            continue
        v = dates.get((t, d), "unknown")
        if not (cls == "FUTURE" or v in ("RELEASED_AFTER_BASE", "unknown")):
            continue
        if SKIP.search(f"{t}\t{d}"):
            print(f"# skip not-the-project {t} {d}", file=sys.stderr)
            continue
        if TOOLCHAIN.search(d):
            print(f"# skip toolchain bundle {t} {d}", file=sys.stderr)
            continue
        m = PKG_CACHE.match(d)
        if m:
            out.append((t, "rm", m.group(1) or m.group(2)))
            continue
        kind, prefix = meta.get((t, d), ("?", "?"))
        if kind == "pyfile":
            bp = next((bp for bp, cp in pairs.get(t, []) if cp == d), None)
            if bp:
                out.append((t, "syncfile", d, bp))
            else:
                print(f"# skip, no pair {t} {d}", file=sys.stderr)
            continue
        if prefix == "?":  # no byte-identical file to learn the layout from: derive it from a matched file pair
            base_rel = next(((bp, cp[len(d) + 1:]) for bp, cp in pairs.get(t, []) if cp.startswith(d + "/")), None)
            if base_rel is None:
                print(f"# skip, layout unknown {t} {d}", file=sys.stderr)
                continue
            bp, rel = base_rel
            rel = (os.path.basename(d) + "/" + rel) if kind == "py" else rel
            if not bp.endswith(rel):
                print(f"# skip, layout mismatch {t} {d} {bp} {rel}", file=sys.stderr)
                continue
            prefix = bp[: len(bp) - len(rel)]
            # a package's files sit at the repo root or in a directory named after the package; anything else is a
            # suffix-match accident (packages/web/index.js for @midwayjs/mock)
            if kind != "py" and prefix and not prefix.rstrip("/").endswith(os.path.basename(d)):
                print(f"# skip, implausible prefix {t} {d} {prefix}", file=sys.stderr)
                continue
        if kind == "py":
            out.append((t, "sync", d, prefix + os.path.basename(d) + "/"))
        else:
            out.append((t, "sync", d, prefix))
    for r in csv.DictReader(open(triage_tsv), delimiter="\t"):
        if int(r["pyc_orphan"]) > 0 or int(r["pyc_stale"]) > 0:
            out.append((r["task"], "pycache"))
    out += MANUAL
    seen = set()
    print("# task\taction\targs  (generated by residue_fixes_build.py; see RESIDUE_AUDIT.md)")
    for row in sorted(out):
        if row not in seen:
            seen.add(row)
            print("\t".join(row))


if __name__ == "__main__":
    main()
