#!/usr/bin/env bash
# GCP VM startup script (runs as root on every boot) for the JUPITER SkyCap relay. No SSH needed: configure through
# instance metadata (`relay-host`, `chisel-auth`) and reboot/reset to apply.
#   https://<relay-host>/skycap/<ip>/<port>/... --Caddy--> socks5://127.0.0.1:1080 (chisel reverse SOCKS, exits on a
#   JUPITER login node) --> <ip>:<port> on the JUPITER IB network (10.128.0.0/16, ports 11500-11549 only).
#   https://tunnel.<relay-host>  --Caddy (websocket)--> chisel server 127.0.0.1:9000. The login node's chisel client
#   dials this over HTTPS/443 (JSC forbids outgoing SSH and private keys on its storage, so no ssh -R).
set -euxo pipefail
md() { curl -fsS -H Metadata-Flavor:Google "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
HOST=$(md relay-host)
CADDY_V=2.11.7 CHISEL_V=1.12.0 CHISEL_SHA=f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6
if ! /usr/bin/caddy version 2>/dev/null | grep -q "v$CADDY_V"; then
  curl -fsSL https://github.com/caddyserver/caddy/releases/download/v$CADDY_V/caddy_${CADDY_V}_linux_amd64.tar.gz | tar -xz -C /usr/bin caddy
fi
if ! /usr/local/bin/chisel --version 2>/dev/null | grep -q "$CHISEL_V"; then
  curl -fsSL -o /tmp/chisel.gz https://github.com/jpillora/chisel/releases/download/v$CHISEL_V/chisel_${CHISEL_V}_linux_amd64.gz
  echo "$CHISEL_SHA  /tmp/chisel.gz" | sha256sum -c
  gunzip -c /tmp/chisel.gz > /usr/local/bin/chisel && chmod 755 /usr/local/bin/chisel && rm /tmp/chisel.gz
fi
getent group caddy >/dev/null || groupadd --system caddy
id caddy >/dev/null 2>&1 || useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
id chisel >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin chisel
mkdir -p /etc/caddy /etc/chisel
# Only the reverse SOCKS listener on the VM's loopback is allowed for the tunnel user.
umask 077; printf '{"%s": ["^R:127\\\\.0\\\\.0\\\\.1:1080"]}\n' "$(md chisel-auth)" > /etc/chisel/users.json; umask 022
chown chisel /etc/chisel/users.json
cat > /etc/systemd/system/chisel.service <<UNIT
[Unit]
Description=chisel tunnel server (JUPITER reverse SOCKS)
After=network-online.target
[Service]
User=chisel
ExecStart=/usr/local/bin/chisel server --host 127.0.0.1 --port 9000 --reverse --authfile /etc/chisel/users.json --keepalive 25s
Restart=always
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/caddy.service <<UNIT
[Unit]
Description=Caddy
After=network-online.target
Wants=network-online.target
[Service]
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	@skycap path_regexp sk ^/skycap/(10\.128\.[0-9]{1,3}\.[0-9]{1,3})/(115[0-4][0-9])(/.*)$
	handle @skycap {
		rewrite * {re.sk.3}
		reverse_proxy {re.sk.1}:{re.sk.2} {
			flush_interval -1
			transport http {
				forward_proxy_url socks5://127.0.0.1:1080
				dial_timeout 30s
				response_header_timeout 0
				read_timeout 0
				write_timeout 0
			}
		}
	}
	respond 404
}
tunnel.$HOST {
	reverse_proxy 127.0.0.1:9000 {
		transport http {
			read_timeout 0
			write_timeout 0
		}
	}
}
CADDY
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl daemon-reload
systemctl enable chisel caddy
systemctl restart chisel caddy
