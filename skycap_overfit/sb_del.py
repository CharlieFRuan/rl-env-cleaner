import asyncio, sys
from daytona import AsyncDaytona
async def main():
    d = AsyncDaytona(); sb = await d.get(sys.argv[1]); assert (sb.labels or {}).get("owner") == "charlieruan", sb.labels
    await d.delete(sb, timeout=120); print("deleted", sys.argv[1]); await d.close()
asyncio.run(main())
