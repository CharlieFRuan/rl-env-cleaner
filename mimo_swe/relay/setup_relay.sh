#!/usr/bin/env bash
# Runs on the mimo-relay VM: Caddy (TLS for <ip>.sslip.io) -> affinity router (127.0.0.1:8000) -> 16 TP=2 vLLM engines.
set -euxo pipefail
HOST=$1
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl python3-venv
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
sudo mkdir -p /opt/mimo-relay && sudo cp ~/affinity_router.py ~/backends.txt /opt/mimo-relay/
sudo python3 -m venv /opt/mimo-relay/venv && sudo /opt/mimo-relay/venv/bin/pip install -q aiohttp
sudo tee /etc/systemd/system/mimo-router.service >/dev/null <<UNIT
[Unit]
Description=MiMo vLLM affinity router
After=network-online.target
[Service]
ExecStart=/opt/mimo-relay/venv/bin/python /opt/mimo-relay/affinity_router.py --backends /opt/mimo-relay/backends.txt --host 127.0.0.1 --port 8000
Restart=always
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$HOST {
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1
		transport http {
			response_header_timeout 0
			read_timeout 0
			write_timeout 0
		}
	}
}
CADDY
sudo mkdir -p /etc/systemd/system/caddy.service.d && printf '[Service]\nLimitNOFILE=1048576\n' | sudo tee /etc/systemd/system/caddy.service.d/limits.conf >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now mimo-router && sudo systemctl restart caddy
sleep 5; systemctl is-active mimo-router caddy
