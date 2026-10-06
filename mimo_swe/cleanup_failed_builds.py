"""Delete OUR labeled sandboxes stuck in BUILD_FAILED, or in ERROR for > 10 min (Daytona-side create/daemon
failures; Harbor leaves both behind); log each deletion."""
import asyncio, datetime as D, json
from daytona import AsyncDaytona

LOG = "/home/charlieruan/mimo/janitor_deletions.log"


async def main():
    d = AsyncDaytona()
    n = 0
    with open(LOG, "a") as log:
        async for s in d.list():
            lab = s.labels or {}
            if lab.get("owner") != "charlieruan" or lab.get("purpose") != "mimo-harbor-eval":
                continue
            st = str(s.state)
            age_min = (D.datetime.now(D.timezone.utc) - D.datetime.fromisoformat(str(s.created_at).replace("Z", "+00:00"))).total_seconds() / 60
            if not ("BUILD_FAILED" in st or ("ERROR" in st and age_min > 10)):
                continue
            try:
                await d.delete(s)
                n += 1
                log.write(json.dumps({"ts": D.datetime.utcnow().isoformat(), "id": s.id, "run": lab.get("run"),
                                      "state": st, "reason": s.error_reason}) + "\n")
            except Exception as e:  # noqa: BLE001
                print("delete failed", s.id, e)
    print(f"deleted {n} failed-build/error sandboxes")
    await d.close()


asyncio.run(main())
