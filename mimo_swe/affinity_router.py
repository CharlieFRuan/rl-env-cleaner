"""Prefix-affinity HTTP router in front of N vLLM replicas.

Agent trajectories resend a growing prefix every turn, so each conversation should stick to one
replica's prefix cache. The routing key is a hash of the system + first user message (identical for
every turn of a trajectory, and for all attempts of the same task, which share that prefix too).
Rendezvous hashing over the healthy replicas keeps the mapping stable when a replica drops out.

Usage: python affinity_router.py --backends backends.txt --port 8000
backends.txt: one base URL per line (http://10.180.15.246:8100). Auth headers are passed through.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time

import aiohttp
from aiohttp import web

log = logging.getLogger("router")
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}


class Router:
    def __init__(self, backends: list[str]):
        self.backends = backends
        self.healthy = set(backends)
        self.inflight = {b: 0 for b in backends}
        self.session: aiohttp.ClientSession | None = None

    async def start(self, app):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
            connector=aiohttp.TCPConnector(limit=0),
        )
        app["health_task"] = asyncio.create_task(self.health_loop())

    async def stop(self, app):
        app["health_task"].cancel()
        await self.session.close()

    async def health_loop(self):
        while True:
            for b in self.backends:
                try:
                    async with self.session.get(b + "/health", timeout=aiohttp.ClientTimeout(total=10)) as r:
                        ok = r.status == 200
                except Exception:
                    ok = False
                if ok and b not in self.healthy:
                    log.warning("backend up: %s", b)
                    self.healthy.add(b)
                elif not ok and b in self.healthy:
                    log.warning("backend DOWN: %s", b)
                    self.healthy.discard(b)
            await asyncio.sleep(10)

    def pick(self, key: str) -> str | None:
        pool = [b for b in self.backends if b in self.healthy]
        if not pool:
            return None
        return max(pool, key=lambda b: hashlib.sha256((b + "|" + key).encode()).digest())

    @staticmethod
    def affinity_key(body: bytes) -> str:
        try:
            msgs = json.loads(body).get("messages") or []
        except Exception:
            return body[:4096].decode(errors="ignore")
        head = []
        for m in msgs:
            c = m.get("content")
            head.append(c if isinstance(c, str) else json.dumps(c))
            if m.get("role") == "user":
                break
        return "\n".join(head)[:20000]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        key = self.affinity_key(body) if request.method == "POST" else str(time.time())
        b = self.pick(key)
        if b is None:
            return web.json_response({"error": "no healthy backend"}, status=503)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        self.inflight[b] += 1
        try:
            async with self.session.request(request.method, b + request.path_qs, data=body, headers=headers) as up:
                resp = web.StreamResponse(status=up.status, headers={k: v for k, v in up.headers.items() if k.lower() not in HOP})
                await resp.prepare(request)
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("upstream error %s: %r", b, e)
            return web.json_response({"error": f"upstream error: {e!r}"}, status=502)
        finally:
            self.inflight[b] -= 1

    async def stats(self, request):
        return web.json_response({"healthy": sorted(self.healthy), "inflight": self.inflight})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backends = [l.strip().rstrip("/") for l in open(a.backends) if l.strip() and not l.startswith("#")]
    r = Router(backends)
    app = web.Application(client_max_size=64 * 1024**2)
    app.on_startup.append(r.start)
    app.on_cleanup.append(r.stop)
    app.router.add_get("/router/stats", r.stats)
    app.router.add_route("*", "/{tail:.*}", r.handle)
    web.run_app(app, host=a.host, port=a.port, access_log=None)


if __name__ == "__main__":
    main()
