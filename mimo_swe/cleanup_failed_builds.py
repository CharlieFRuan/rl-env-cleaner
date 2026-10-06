"""Delete OUR labeled sandboxes stuck in BUILD_FAILED (Harbor leaves them behind); log each deletion."""
import asyncio, datetime as D, json
from daytona import AsyncDaytona

LOG = "/home/charlieruan/mimo/janitor_deletions.log"


async def main():
    d = AsyncDaytona()
    n = 0
    with open(LOG, "a") as log:
        async for s in d.list():
            lab = s.labels or {}
            if lab.get("owner") != "charlieruan" or lab.get("purpose") != "mimo-harbor-eval" or "BUILD_FAILED" not in str(s.state):
                continue
            try:
                await d.delete(s)
                n += 1
                log.write(json.dumps({"ts": D.datetime.utcnow().isoformat(), "id": s.id, "run": lab.get("run"),
                                      "state": "BUILD_FAILED", "reason": s.error_reason}) + "\n")
            except Exception as e:  # noqa: BLE001
                print("delete failed", s.id, e)
    print(f"deleted {n} failed-build sandboxes")
    await d.close()


asyncio.run(main())
