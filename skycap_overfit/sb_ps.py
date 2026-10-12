import asyncio, sys
from daytona import AsyncDaytona
async def main():
    d = AsyncDaytona(); sb = await d.get(sys.argv[1])
    r = await sb.process.exec("ps -eo pid,etime,pcpu,args --sort=-pcpu | head -15 | cut -c1-200; echo; ls -la /logs/verifier 2>&1 | head; tail -5 /logs/verifier/test-stdout.txt 2>&1", timeout=60)
    print(r.result); await d.close()
asyncio.run(main())
