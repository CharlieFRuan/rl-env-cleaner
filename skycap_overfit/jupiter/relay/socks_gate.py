#!/usr/bin/env python3
"""SOCKS5 gate for the inbound relay: lets the public relay reach ONLY this user's running Slurm nodes.

The chisel tunnel forwards the relay VM's 127.0.0.1:1080 to this gate on the login node. Caddy on the VM only knows
"some 10.128.x.x address"; this gate admits a CONNECT only if the address belongs to a node of one of $USER's RUNNING
jobs (from squeue, refreshed every REFRESH_SEC) and the port is a SkyCap port (11500-11549). Everything else on the
JUPITER network stays unreachable from the internet. Stdlib only.

usage: socks_gate.py --bind 127.0.0.1 --port 41874
"""

import argparse
import asyncio
import getpass
import ipaddress
import logging
import socket
import struct
import subprocess
import time

PORTS = range(11500, 11550)
REFRESH_SEC = 30
log = logging.getLogger("socks_gate")


class Allowed:
    def __init__(self) -> None:
        self.ips: set[str] = set()
        self.at = 0.0

    def get(self) -> set[str]:
        if time.time() - self.at > REFRESH_SEC:
            self.at = time.time()
            try:
                nodelists = subprocess.run(["squeue", "-u", getpass.getuser(), "-h", "-t", "R", "-o", "%N"],
                                           capture_output=True, text=True, timeout=20).stdout.split()
                ips = set()
                for nl in nodelists:
                    hosts = subprocess.run(["scontrol", "show", "hostnames", nl], capture_output=True, text=True,
                                           timeout=20).stdout.split()
                    for h in hosts:
                        try:
                            ips.add(socket.gethostbyname(f"{h}.jupiter.internal"))
                        except OSError:
                            pass
                if ips != self.ips:
                    log.info("allowed nodes: %s", sorted(ips))
                self.ips = ips
            except Exception as e:  # keep the last known set
                log.warning("refresh failed: %s", e)
        return self.ips


async def pipe(reader, writer) -> None:
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


async def handle(reader, writer, allowed: Allowed) -> None:
    async def fail(code: int) -> None:
        writer.write(struct.pack("!BBBB4sH", 5, code, 0, 1, b"\0\0\0\0", 0))
        await writer.drain()
        writer.close()

    try:
        ver, nmethods = await asyncio.wait_for(reader.readexactly(2), 30)
        await reader.readexactly(nmethods)
        if ver != 5:
            writer.close()
            return
        writer.write(b"\x05\x00")  # no auth: the gate only listens on the login node's loopback
        await writer.drain()
        ver, cmd, _, atyp = await reader.readexactly(4)
        if atyp == 1:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        else:
            return await fail(8)  # address type not supported
        (port,) = struct.unpack("!H", await reader.readexactly(2))
    except Exception:
        writer.close()
        return
    try:
        ok_ip = str(ipaddress.ip_address(host)) in allowed.get()
    except ValueError:
        ok_ip = False
    if cmd != 1 or not ok_ip or port not in PORTS:
        log.warning("denied %s:%s", host, port)
        return await fail(2)  # not allowed by ruleset
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), 30)
    except Exception as e:
        log.warning("connect %s:%s failed: %s", host, port, e)
        return await fail(5)
    writer.write(struct.pack("!BBBB4sH", 5, 0, 0, 1, b"\0\0\0\0", 0))
    await writer.drain()
    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=41874)
    args = ap.parse_args()
    allowed = Allowed()
    allowed.get()
    server = await asyncio.start_server(lambda r, w: handle(r, w, allowed), args.bind, args.port,
                                        reuse_address=True, backlog=1024)
    log.info("listening on %s:%d", args.bind, args.port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
