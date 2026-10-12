#!/usr/bin/env bash
# skycap-relay: Caddy only. HTTPS for <ip>.sslip.io with two routes, no timeouts, unbuffered:
#   /base/*                          -> $BASE_BACKEND (plain vLLM router; only for no-SkyCap parity baselines)
#   /skycap/<node-ip>/<port>/*       -> skycap exposed listener on that node (nodes matching NODE_IP_REGEX, ports 11500-11549)
set -euxo pipefail
# usage: NODE_IP_REGEX='10\.0\.0\.(?:11|12)' BASE_BACKEND=10.0.0.11:8000 setup_skycap_relay.sh <public-host, e.g. 1-2-3-4.sslip.io>
#   NODE_IP_REGEX: the training nodes' internal IPs, escaped for a Caddy regexp; BASE_BACKEND only matters for parity baselines.
HOST=$1
NODE_IP_REGEX=${NODE_IP_REGEX:?}
BASE_BACKEND=${BASE_BACKEND:-127.0.0.1:8000}
V=2.11.7
curl -fsSL https://github.com/caddyserver/caddy/releases/download/v$V/caddy_${V}_linux_amd64.tar.gz | sudo tar -xz -C /usr/bin caddy
sudo groupadd --system caddy 2>/dev/null || true
id caddy >/dev/null 2>&1 || sudo useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
sudo mkdir -p /etc/caddy
sudo tee /etc/systemd/system/caddy.service >/dev/null <<UNIT
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
[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$HOST {
	handle_path /base/* {
		reverse_proxy $BASE_BACKEND {
			flush_interval -1
			transport http {
				response_header_timeout 0
				read_timeout 0
				write_timeout 0
			}
		}
	}
	@skycap path_regexp sk ^/skycap/($NODE_IP_REGEX)/(115[0-4][0-9])(/.*)$
	handle @skycap {
		rewrite * {re.sk.3}
		reverse_proxy {re.sk.1}:{re.sk.2} {
			flush_interval -1
			transport http {
				response_header_timeout 0
				read_timeout 0
				write_timeout 0
			}
		}
	}
	respond 404
}
CADDY
sudo mkdir -p /etc/systemd/system/caddy.service.d && printf '[Service]\nLimitNOFILE=1048576\n' | sudo tee /etc/systemd/system/caddy.service.d/limits.conf >/dev/null
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl daemon-reload && sudo systemctl enable --now caddy && sudo systemctl restart caddy
sleep 8; systemctl is-active caddy
