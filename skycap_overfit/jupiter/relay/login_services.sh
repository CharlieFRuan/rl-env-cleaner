#!/usr/bin/env bash
# Network services for MiMo/SkyCap jobs, run on ONE JUPITER login node (compute nodes have no internet).
#   tunnel: chisel client -> wss://tunnel.$RELAY_HOST (HTTPS/443). Forwards the relay VM's 127.0.0.1:1080 to `gate`, so
#           Daytona sandboxes can reach SkyCap on compute nodes (inbound).
#   gate:   socks_gate.py on this login node's loopback: SOCKS5 that admits only this user's running Slurm nodes, ports
#           11500-11549, so the public relay cannot reach anything else on the JUPITER network.
#   proxy:  connect_proxy.py on this login node's IB address. Authenticated, CONNECT-only, host allowlist; jobs use it as
#           HTTPS_PROXY to reach the Daytona API and W&B (outbound).
# No SSH and no private keys (both are against JSC policy); credentials live in ~/.config/jupiter-relay (mode 600).
#
# usage: login_services.sh start|stop|status
#   writes ~/.config/jupiter-relay/proxy.env (HTTPS_PROXY=...) for jobs to source.
source ~/.bashrc >/dev/null 2>&1  # before set -u: /etc/bashrc reads unset vars
set -uo pipefail
CFG=$HOME/.config/jupiter-relay
RELAY_HOST=${RELAY_HOST:-$(cat "$CFG/relay_host")}   # e.g. 1-2-3-4.sslip.io (the relay VM's public IP)
PROXY_PORT=${PROXY_PORT:-41873}
GATE_PORT=${GATE_PORT:-41874}
LOGD=$DATA/logs/relay
HERE=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
PY=$DATA/uv-python/cpython-3.12-linux-aarch64-gnu/bin/python3.12
IB_IP=$(ip -4 -o addr show ib0 | awk '{print $4}' | cut -d/ -f1)
mkdir -p "$LOGD"

running() { [[ -f $CFG/$1.pid ]] && kill -0 "$(cat "$CFG/$1.pid")" 2>/dev/null; }

case "${1:-status}" in
start)
  if running gate; then echo "gate already running"; else
    setsid "$PY" "$HERE/socks_gate.py" --bind 127.0.0.1 --port "$GATE_PORT" >> "$LOGD/gate.log" 2>&1 < /dev/null &
    echo $! > "$CFG/gate.pid"
  fi
  if running tunnel; then echo "tunnel already running"; else
    # chisel reconnects on its own (unlimited retries, backoff up to 5 min; we cap at 10 s).
    AUTH=$(cat "$CFG/chisel_auth") setsid "$DATA/opt/chisel/chisel" client --keepalive 25s --max-retry-interval 10s \
      "https://tunnel.$RELAY_HOST" "R:127.0.0.1:1080:127.0.0.1:$GATE_PORT" >> "$LOGD/tunnel.log" 2>&1 < /dev/null &
    echo $! > "$CFG/tunnel.pid"
  fi
  if running proxy; then echo "proxy already running"; else
    setsid "$PY" "$HERE/connect_proxy.py" --bind "$IB_IP" --port "$PROXY_PORT" --auth-file "$CFG/proxy_auth" \
      >> "$LOGD/proxy.log" 2>&1 < /dev/null &
    echo $! > "$CFG/proxy.pid"
  fi
  (umask 077; printf 'export HTTPS_PROXY=http://%s@%s:%s\nexport https_proxy=$HTTPS_PROXY\nexport RELAY_HOST=%s\nexport RELAY_LOGIN_NODE=%s\n' \
    "$(cat "$CFG/proxy_auth")" "$IB_IP" "$PROXY_PORT" "$RELAY_HOST" "$(hostname -s)" > "$CFG/proxy.env")
  sleep 3; "$0" status
  ;;
stop)
  for s in tunnel gate proxy; do running $s && kill "$(cat "$CFG/$s.pid")" && echo "stopped $s"; rm -f "$CFG/$s.pid"; done
  ;;
status)
  for s in tunnel gate proxy; do running $s && echo "$s: running (pid $(cat "$CFG/$s.pid"), $(hostname -s))" || echo "$s: NOT running"; done
  tail -n 2 "$LOGD/tunnel.log" 2>/dev/null
  ;;
*) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
