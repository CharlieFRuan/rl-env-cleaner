#!/usr/bin/env python3
"""Minimal authenticated HTTPS (CONNECT-only) forward proxy for JUPITER compute nodes.

Compute nodes have no internet; login nodes do. Run this on a login node, bound to its IB address, and point jobs at it:

    HTTPS_PROXY=http://<user>:<pass>@<login-ib-ip>:<port>

Only CONNECT to port 443 of hosts matching ALLOW (suffix match) is permitted, with Basic proxy auth, so this is not an
open proxy into the internet for everyone on the cluster network. Stdlib only.

usage: connect_proxy.py --bind 10.128.1.1 --port 3128 --auth-file ~/.config/jupiter-relay/proxy_auth \
           [--allow daytona.io,wandb.ai,...]
"""

import argparse
import asyncio
import base64
import logging
import secrets

DEFAULT_ALLOW = [
    "daytona.io",  # Daytona API, toolbox proxy, sandbox previews
    "daytona.work",
    "wandb.ai",  # W&B API
    "wandb.com",
    "storage.googleapis.com",  # W&B file uploads (signed URLs)
    "s3.us-east-1.amazonaws.com",  # Daytona image build contexts (bucket prod-daytona-build-context)
]

log = logging.getLogger("connect_proxy")


def allowed(host: str, allow: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allow)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader, writer, expected_auth: bytes, allow: list[str]) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except Exception:
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split()
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}

    def reply(status: str) -> None:
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())

    auth = headers.get("proxy-authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "basic" or not secrets.compare_digest(token.strip().encode(), expected_auth):
        writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="jupiter"\r\n'
                     b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        log.warning("auth failed from %s", peer)
        return
    if len(parts) != 3 or parts[0] != "CONNECT":
        reply("405 Method Not Allowed")
        await writer.drain()
        writer.close()
        return
    host, _, port = parts[1].rpartition(":")
    host = host.strip("[]")
    if port != "443" or not allowed(host, allow):
        reply("403 Forbidden")
        await writer.drain()
        writer.close()
        log.warning("denied %s from %s", parts[1], peer)
        return
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=30)
    except Exception as e:
        reply("502 Bad Gateway")
        await writer.drain()
        writer.close()
        log.warning("connect %s failed: %s", parts[1], e)
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    log.info("CONNECT %s from %s", parts[1], peer)
    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", required=True)
    ap.add_argument("--port", type=int, default=3128)
    ap.add_argument("--auth-file", required=True, help="file with one line 'user:password'")
    ap.add_argument("--allow", default=",".join(DEFAULT_ALLOW), help="comma-separated host suffixes")
    args = ap.parse_args()
    cred = open(args.auth_file).read().strip().encode()
    expected = base64.b64encode(cred)
    allow = [a.strip().lower() for a in args.allow.split(",") if a.strip()]
    server = await asyncio.start_server(lambda r, w: handle(r, w, expected, allow), args.bind, args.port,
                                        reuse_address=True, backlog=1024)
    log.info("listening on %s:%d, allow=%s", args.bind, args.port, allow)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
