"""List / clean up OUR Daytona sandboxes only (labels owner=charlieruan, purpose=mimo-harbor-eval).

  python sandbox_janitor.py                         # count + per-state summary of our sandboxes
  python sandbox_janitor.py --run mimo-qwen36 -v    # restrict to one run label, list each sandbox
  python sandbox_janitor.py --delete-older-than-min 300 [--run R]   # delete leaked ones (logged)

Never touches sandboxes without both of our labels. Age-based: a sandbox older than
agent timeout + verifier timeout + build + margin cannot belong to a live trial.
Exit code 2 if the count exceeds --alert-above (default 60).
"""

import argparse
import asyncio
import datetime as dt
import json
import os

from daytona import AsyncDaytona, ListSandboxesQuery

LABELS = {"owner": "charlieruan", "purpose": "mimo-harbor-eval"}
LOG = os.environ.get("JANITOR_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "janitor_deletions.log"))


def _age_min(sb) -> float:
    created = getattr(sb, "created_at", None)
    if not created:
        return -1.0
    t = dt.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None, help="also require label run=<RUN>")
    ap.add_argument("--delete-older-than-min", type=float, default=None)
    ap.add_argument("--alert-above", type=int, default=60)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    labels = dict(LABELS, **({"run": args.run} if args.run else {}))

    states: dict[str, int] = {}
    n = deleted = 0
    async with AsyncDaytona() as d:
        async for sb in d.list(ListSandboxesQuery(labels=labels)):
            sl = sb.labels or {}
            if any(sl.get(k) != v for k, v in labels.items()):  # belt and braces: server filter + local check
                continue
            n += 1
            state = str(getattr(sb, "state", "?")).split(".")[-1]
            states[state] = states.get(state, 0) + 1
            age = _age_min(sb)
            if args.verbose:
                print(f"{sb.id} state={state} age_min={age:.0f} run={sl.get('run')}")
            if args.delete_older_than_min is not None and age > args.delete_older_than_min:
                try:
                    await sb.delete()
                    deleted += 1
                    msg = "deleted"
                except Exception as e:
                    msg = f"delete_failed: {e}"
                with open(LOG, "a") as f:
                    f.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "id": sb.id, "state": state,
                                        "age_min": round(age, 1), "labels": sl, "result": msg}) + "\n")
                print(f"{msg} {sb.id} age_min={age:.0f}")
    print(f"our_sandboxes={n} by_state={states} deleted={deleted}")
    if n > args.alert_above:
        print(f"ALERT: {n} sandboxes > {args.alert_above}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
