"""Collapse signals per run from W&B (last N steps). usage: collapse.py <run_name>... ; needs WANDB_API_KEY in env."""
import sys, wandb
api = wandb.Api()
K = {"reward": "reward/avg_raw_reward", "entropy": "policy/policy_entropy", "tok_k": "generate/avg_num_tokens",
     "ctx_exc": "generate/skycap/trajectories_context_length_exceeded", "failed": "generate/harbor/num_failed_attempts",
     "masked": "generate/skycap/num_masked_instances"}
for name in sys.argv[1:]:
    rs = api.runs("sky-posttraining-uc-berkeley/skycap-overfit", filters={"display_name": name})
    if not rs: print(name, "not found"); continue
    h = rs[0].history(samples=1000, pandas=False)
    print(f"== {name}")
    for label, k in K.items():
        v = [row[k] for row in h if row.get(k) is not None][-8:]
        print(f"  {label:8s}", " ".join(f"{x/1000:.0f}" if label == "tok_k" else (f"{x:.2f}" if isinstance(x, float) and x < 5 else f"{int(x)}") for x in v))
