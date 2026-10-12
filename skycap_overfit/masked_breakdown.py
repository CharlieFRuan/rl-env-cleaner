"""Per run: masked instances and their cause (timeouts vs errors vs failed-attempt types) summed over steps."""
import sys, wandb
api = wandb.Api()
for name in sys.argv[1:]:
    rs = api.runs("sky-posttraining-uc-berkeley/skycap-overfit", filters={"display_name": name})
    if not rs: print(name, "not found"); continue
    h = rs[0].history(samples=1000, pandas=False)
    keys = sorted({k for row in h for k in row if k.startswith("generate/harbor/num_failed_attempts/") or k in (
        "generate/skycap/num_masked_instances", "generate/skycap/num_timeout_trajectories", "generate/skycap/num_error_trajectories")})
    print(f"== {name} ({sum(1 for r in h if r.get('generate/skycap/num_masked_instances') is not None)} steps)")
    for k in keys:
        v = [int(r[k]) for r in h if r.get(k) is not None]
        print(f"  {k.split('/')[-1]:32s} total={sum(v):4d} steps>0={sum(x>0 for x in v):3d}  {' '.join(map(str, v))}")
