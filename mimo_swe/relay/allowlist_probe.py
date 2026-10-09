"""Probe what Daytona's domain allowlist permits: wildcard-DNS hostnames, non-standard ports, http vs https."""
import asyncio
from daytona import AsyncDaytona, CreateSandboxFromImageParams, Image

ALLOW = "1-1-1-1.sslip.io,portquiz.net"
TESTS = [
    ("allowed sslip https", "curl -sk -m 15 -o /dev/null -w '%{http_code}' https://1-1-1-1.sslip.io/"),
    ("allowed sslip http", "curl -s -m 15 -o /dev/null -w '%{http_code}' http://1-1-1-1.sslip.io/"),
    ("allowed portquiz :80", "curl -s -m 15 -o /dev/null -w '%{http_code}' http://portquiz.net/"),
    ("allowed portquiz :8443", "curl -s -m 15 -o /dev/null -w '%{http_code}' http://portquiz.net:8443/"),
    ("allowed portquiz :27000", "curl -s -m 15 -o /dev/null -w '%{http_code}' http://portquiz.net:27000/"),
    ("NOT allowed github", "curl -s -m 15 -o /dev/null -w '%{http_code}' https://github.com/"),
    ("NOT allowed 2-2-2-2? (8-8-8-8.sslip.io)", "curl -sk -m 15 -o /dev/null -w '%{http_code}' https://8-8-8-8.sslip.io/"),
    ("direct IP 1.1.1.1", "curl -sk -m 15 -o /dev/null -w '%{http_code}' https://1.1.1.1/"),
]

async def main():
    d = AsyncDaytona()
    sb = await d.create(CreateSandboxFromImageParams(
        image=Image.base("curlimages/curl:8.10.1").entrypoint(["sleep", "3600"]) if False else "python:3.12-slim",
        labels={"owner": "charlieruan", "run": "allowlist-probe", "purpose": "mimo-harbor-eval"}, ephemeral=True))
    try:
        await sb.process.exec("apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq curl >/dev/null 2>&1; echo ok", timeout=300)
        await sb.update_network_settings(domain_allow_list=ALLOW)
        await asyncio.sleep(3)
        for name, cmd in TESTS:
            r = await sb.process.exec(cmd + " ; echo \" rc=$?\"", timeout=60)
            print(f"{name:40s} -> {r.result.strip()}")
    finally:
        await d.delete(sb)
        await d.close()

asyncio.run(main())
