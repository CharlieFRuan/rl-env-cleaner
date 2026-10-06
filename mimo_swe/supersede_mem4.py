"""Move attempts of tasks promoted to the 8 GB tier that started before the promotion (they ran at 4 GB)
out of the scored glob into jobs/superseded-mem4gb/. Only finished trials (result.json) are moved; run repeatedly."""
import glob, json, os, shutil, sys, time

HERE = "/home/charlieruan/mimo"
since = {}
for line in open(f"{HERE}/heavy_ids.txt"):
    if line.strip() and not line.startswith("#") and "since=" in line:
        t = line.split()[0]
        since[t] = line.split("since=")[1].split()[0]
os.makedirs(f"{HERE}/jobs/superseded-mem4gb", exist_ok=True)
moved = 0
for t, s in since.items():
    for d in glob.glob(f"{HERE}/jobs/wave-*/{t}__*"):
        r = os.path.join(d, "result.json")
        if not os.path.exists(r):
            continue
        started = json.load(open(r))["started_at"][:19]
        if started < s:
            shutil.move(d, f"{HERE}/jobs/superseded-mem4gb/")
            moved += 1
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} moved {d} (started {started} < since {s})", flush=True)
if "-v" in sys.argv:
    print("moved", moved)
