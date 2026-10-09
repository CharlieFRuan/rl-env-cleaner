"""For installed copies of the project that are not in BASE's history (residue_classify WEAK/FUTURE with future files):
look up the copy's release date (npm registry / PyPI / Go module proxy) and compare it with the BASE commit time.
A release published after BASE may contain the fix (e.g. a backport on a maintenance branch).
python residue_release_dates.py <copies.tsv> <jobs_glob...> > release_dates.tsv
"""
import csv
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "residue-audit"}), timeout=20) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"_error": repr(e)[:80]}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def release_time(kind, name, ver):
    if kind == "npm":
        j = fetch("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@"))
        t = j.get("time", {}).get(ver)
        return ts(t) if t else None
    if kind == "py":
        j = fetch(f"https://pypi.org/pypi/{name}/{ver}/json")
        u = [x["upload_time_iso_8601"] for x in j.get("urls", [])]
        return min(ts(x) for x in u) if u else None
    if kind == "go":
        esc = re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), name)
        j = fetch(f"https://proxy.golang.org/{esc}/@v/{ver}.info")
        return ts(j["Time"]) if "Time" in j else None
    return None


def main():
    rows = [r for r in csv.reader(open(sys.argv[1]), delimiter="\t") if r[1] in ("WEAK", "FUTURE") and r[0] != "task" and int(r[7]) > 0]
    info = {}
    for g in sys.argv[2:]:
        for tr in glob.glob(os.path.join(g, "format-code-task-*__*")):
            f = os.path.join(tr, "verifier/test-stdout.txt")
            if not os.path.exists(f):
                continue
            t = os.path.basename(tr).split("__")[0]
            d = info.setdefault(t, {"dist": {}, "npm": {}, "base_t": None})
            for l in open(f, errors="replace"):
                m = re.match(r"RESIDUE py_dist (\S+) version=(\S*) tops=(\S*)", l)
                if m:
                    name = re.sub(r"-[0-9].*", "", os.path.basename(m.group(1)))
                    for top in m.group(3).split(","):
                        d["dist"][os.path.join(os.path.dirname(m.group(1)), top)] = (name, m.group(2))
                m = re.match(r"RESIDUE npm_copy (\S+) version=(\S*)", l)
                if m:
                    p = m.group(1)
                    d["npm"][p] = (re.sub(r".*/node_modules/", "", p), m.group(2))
                m = re.search(r"base_commit_time=(\d+)", l)
                if m:
                    d["base_t"] = int(m.group(1))
    print("task\tclass\tdir\tkind\tname\tversion\trelease\tbase\tverdict")
    for r in rows:
        t, cls, d = r[0], r[1], r[2]
        i = info.get(t, {"dist": {}, "npm": {}, "base_t": None})
        kind = name = ver = None
        if d in i["dist"]:
            kind, (name, ver) = "py", i["dist"][d]
        elif d in i["npm"]:
            kind, (name, ver) = "npm", i["npm"][d]
        elif "/pkg/mod/" in d and "@" in d:
            kind = "go"
            name, ver = d.split("/pkg/mod/")[1].rsplit("@", 1)
            name = re.sub(r"!([a-z])", lambda m: m.group(1).upper(), name)
        rt = release_time(kind, name, ver) if kind and ver else None
        bt = i["base_t"]
        verdict = "unknown" if not (rt and bt) else ("RELEASED_AFTER_BASE" if rt > bt else "released_before_base")
        f = lambda x: time.strftime("%Y-%m-%d", time.gmtime(x)) if x else "?"
        print("\t".join(map(str, [t, cls, d, kind, name, ver, f(rt), f(bt), verdict])))


if __name__ == "__main__":
    main()
