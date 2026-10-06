#!/usr/bin/env bash
# Exit 0 if the public endpoint (tunnel -> router -> vLLM) answers. If the tunnel is dead, restart it
# (new URL -> tunnel_url; later waves allowlist the new host automatically via run_harbor.sh).
HERE=/home/charlieruan/mimo; cd $HERE
KEY=$(cat vllm_api_key)
ok() { curl -sf -m 30 -H "Authorization: Bearer $KEY" "$(cat tunnel_url)/v1/models" >/dev/null; }
ok && exit 0
sleep 10; ok && exit 0
# router alive?
if ! curl -sf -m 10 localhost:8000/router/stats >/dev/null; then
  echo "$(date -Is) router down; restarting" >> endpoint_events.log
  setsid nohup routervenv/bin/python rl-env-cleaner/mimo_swe/affinity_router.py --backends backends.txt --port 8000 >> router.log 2>&1 < /dev/null &
  sleep 5
fi
ok && exit 0
echo "$(date -Is) tunnel $(cat tunnel_url) unreachable; restarting cloudflared" >> endpoint_events.log
pkill -f "cloudflared tunne[l]"; sleep 2
setsid nohup bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 > cloudflared.log 2>&1 < /dev/null &
for i in $(seq 1 30); do u=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" cloudflared.log | head -1); [ -n "$u" ] && break; sleep 2; done
[ -n "$u" ] && echo "$u" > tunnel_url && echo "$(date -Is) new tunnel $u" >> endpoint_events.log
sleep 15; ok
