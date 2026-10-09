# Real concurrency: trial dirs without result.json touched in the last 4 h, plus live worker processes.
cd /home/charlieruan/mimo
echo "live trial dirs: $(find jobs/prof-r1 -maxdepth 1 -mindepth 1 -type d -mmin -240 | while read d; do [ -f $d/result.json ] || echo; done | wc -l)  old-runner orphans: $(ps -eo ppid,pgid,cmd | awk '$1==1 && $2==90188' | grep -c '[s]pawn_main')  new-runner workers: $(ps --ppid $(pgrep -f '[c]ontinuous_runner.py' | tail -1) --no-headers 2>/dev/null | grep -vc resource_tracker)"
o=$(ps -eo ppid,pgid,cmd | awk '$1==1 && $2==90188' | grep -c "[s]pawn_main"); echo "ACTION: set prof.conf TARGET=$((500 - o)) (500 minus old-runner orphans; 500 once orphans reach 0)"
