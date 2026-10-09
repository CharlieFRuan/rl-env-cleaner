# Model endpoint relay (replacing the Cloudflare tunnel)

How sandboxes reach our vLLM engines during the agent phase. The design and the verification done during the 2026-10-08/09
profiling pass, run at up to 500 concurrent trials.

## Why replace Cloudflare

The earlier runs exposed vLLM through a Cloudflare quick tunnel (`*.trycloudflare.com`), which has two hard limits:
- **A 125 s response timeout.** We had to cap `max_tokens` so one reply would finish within it (see `mswea_qwen36.yaml`).
- **About 200 in-flight requests**, which capped trial concurrency.

With TP=2 serving and a 1,500-vCPU Daytona quota, we wanted about 500 concurrent trials.

## Constraint: the Daytona allowlist is hostname-based

During the agent phase each sandbox may only reach the model endpoint: `update_network_settings(domain_allow_list=...)`,
applied by `labeled_daytona.py` `_apply_network_policy`. Probe (`relay/allowlist_probe.py`, allowlist
`1-1-1-1.sslip.io,portquiz.net`):

| Request | Result |
|---|---|
| `https://1-1-1-1.sslip.io/`, `http://1-1-1-1.sslip.io/` (listed) | reached (HTTP 403 from the far end) |
| `portquiz.net` on :80, :8443, :27000 (listed) | 200: non-standard ports are allowed |
| `https://github.com/` (not listed) | rc=6, DNS fails |
| `https://8-8-8-8.sslip.io/` (not listed, same wildcard domain) | rc=6: only the exact listed name resolves |
| `https://1.1.1.1/` (direct IP) | rc=35: blocked |

So the endpoint must have a DNS name: a bare IP doesn't work. GCP doesn't give public hostnames to external IPs, so we use
**sslip.io**, a free public DNS service that answers `a-b-c-d.sslip.io` with `a.b.c.d`. No DNS setup is needed.

## Implementation

```
sandbox (Daytona)
   │  HTTPS  https://34-106-202-154.sslip.io/v1/...   (allowlisted name; Bearer API key)
   ▼
mimo-relay VM  (n2-standard-8, us-west3-c, b200-vpc, static IP 34.106.202.154, internal 10.180.0.19)
   Caddy :443   TLS (automatic Let's Encrypt cert), reverse_proxy, no timeouts, unbuffered streaming
   │  HTTP 127.0.0.1:8000
   ▼
   affinity_router.py   prefix-affinity routing + health checks
   │  HTTP over the VPC (internal, free)
   ▼
vLLM TP=2 engines   :8200-8203 on each GPU node (currently nodes 4 and 5 = 8 engines; 16 during the pass)
```

The same model as SkyCap's `external_host`: a public front door that forwards to private backends, with TLS added.

| Piece | Code / config | Notes |
|---|---|---|
| VM, static IP, firewall | `relay/create_relay.sh` | Firewall rule `mimo-relay-allow-web`: tcp:80,443 from 0.0.0.0/0 to tag `mimo-relay`. Port 80 is for the Let's Encrypt HTTP-01 challenge. Relay → GPU nodes uses the existing `b200-allow-internal` rule. |
| Caddy and router install | `relay/setup_relay.sh <host>` | Writes `/etc/caddy/Caddyfile` and the systemd unit `mimo-router.service`; `LimitNOFILE` = 1M for both. |
| Caddy config | `/etc/caddy/Caddyfile` (in `setup_relay.sh`) | `reverse_proxy 127.0.0.1:8000` with `flush_interval -1` (stream tokens unbuffered) and `response_header_timeout` / `read_timeout` / `write_timeout` 0, i.e. no per-request timeout. |
| Router | `affinity_router.py` | Hashes the system + first user message, so every turn of a trajectory (and every attempt of a task) hits the same engine's prefix cache. Rendezvous hashing over healthy backends (`/health` every 10 s), so a failed engine only moves its own keys. Returns 503 if no backend is healthy and 502 on an upstream error. Auth headers pass through; vLLM checks the API key. No concurrency cap (`TCPConnector(limit=0)`, no total timeout). |
| Backends | `/opt/mimo-relay/backends.txt` (from `~/mimo/backends_tp2.txt`) | One URL per line. The 16-engine list is kept as `backends_16.txt.bak`. |
| Engines | `/mnt/local_storage/charlieruan/serve_tp2_pair.sh <pair>` on each GPU node (not in the repo) | GPUs 2P, 2P+1 → port 8200+P, `--max-num-seqs 64`, 262k context. Clears `NCCL_*` (the gIB plugin breaks pip NCCL). |
| Client side | `prof/relay_url`; the runner's `trial_config` | The runner sets `OPENAI_API_BASE=<url>/v1` and adds the host to `agent.extra_allowed_hosts`, which Harbor merges into the agent-phase allowlist. |
| Health check | `prof/health.sh` | `GET <url>/v1/models` with the key, through the whole chain. The runner pauses launches while it fails. |

Day-to-day operations:
- **Change the engine set:** edit `/opt/mimo-relay/backends.txt`, then `sudo systemctl restart mimo-router`.
- **Logs:** `journalctl -u mimo-router` and `journalctl -u caddy`.
- **Certificate:** Caddy renews it automatically. The current one is for `34-106-202-154.sslip.io`, valid to 2027-01-06.

`create_relay.sh` is reconstructed from the commands run during setup and hasn't been re-run end-to-end.

## Verification

| Check | Result |
|---|---|
| Allowlist semantics | Probe above: the listed name and ports work; unlisted names and direct IPs are blocked |
| TLS | Let's Encrypt cert issued automatically, CN = `34-106-202-154.sslip.io` |
| End-to-end through the chain | `prof/health.sh` passes. After the scale-down to 8 engines, all 8 backends return `/health` 200 from the relay and the end-to-end check passes. |
| Stress test (`jobs/prof-r1`, about 10,800 trials, about 800k model calls) | Ramped 50 → 200 → 500 concurrent trials (true 500 after the worker-pool fix), about 4 h at 500. Then the reruns (about 900 trials at 400 concurrent on 8 engines). |
| Failures caused by the endpoint | **0.** All 46 wave-1 infra failures were sandbox-side: OOM, verifier timeouts, DNS for github.com during install, docs.rs resets, one build failure. No LLM retries in agent logs; the "Retrying" lines are pip and HF downloads failing behind the allowlist. |
| Relay errors | 18 router log lines in about 9.5 h, clustered at the times runners were killed (client disconnects). |
| Relay load | Load average below 1 on 8 vCPU at 500 concurrent trials; about 330 open TLS connections |
| No per-request timeout | The longest single model call was about 199 s, which would have failed under Cloudflare's 125 s limit |

LLM call latency, measured in the agent (time from the previous tool result to the next assistant message):

| Concurrent trials | Engines | p50 | p90 | p99 | Engine requests running / waiting |
|---|---|---|---|---|---|
| 200 | 16 | 0.8 s | 2.3 s | 7.7 s | about 26 / 0 |
| 500 | 16 | 1.2–1.3 s | 4.6–5.7 s | 17–19 s | 150–200 / 0 |
| about 400 (reruns) | 8 | 1.35 s | 5.5 s | 22 s | / 0 |

Latency rose with load because of bigger engine batches, not the relay: relay load stayed flat and nothing ever queued at
the engines.

## Capacity

Per engine: 11.25M KV-cache tokens (42.9 sequences at a full 262k context) and `--max-num-seqs` 64. With 8 engines:
- 343 requests at a full 262k context, worst case;
- a cap of 512 running requests.

Measured contexts were a mean of 38k tokens per request and a p90 of 92k per trial at its peak, so KV isn't the limit. At 500
trials about 0.35 requests per trial are in flight (trials spend most of their time in tools), about 175 requests. So
**500 concurrent trials fit on 8 engines**, at an estimated 1.5–2× longer LLM calls than on 16 (not measured). Above about
1,000 trials the 512-request cap starts to bind. Daytona separately caps us at 750 sandboxes at 2 vCPU.

## Cost

- **GCP egress** is billed only for relay → internet, i.e. the responses. Each attempt gets about 21k output tokens over
  about 76 calls: about 0.2 MB with JSON overhead, so **about 2–5 GB (under $1) per 10.8k-trial pass**.
- **Free traffic:** requests are much larger (each turn resends the history: about 130 GB per pass) but they're ingress.
  Relay → engines is internal traffic in the same zone.
- **The VM costs more than the traffic:** about $0.39/h for the n2-standard-8, plus the static IP.

## Known weaknesses (fine for profiling; fix before long RL runs)

- **Single VM, single point of failure.** systemd restarts crashed processes, but not a dead VM. Fix: two relays behind a GCP
  load balancer.
- **Depends on sslip.io.** It's a free service run by one maintainer, with no SLA (xip.io, a similar service, shut down
  around 2021). If it fails, new sandboxes can't resolve the endpoint. Fix: a domain we own with an A record (Cloud DNS),
  then change the Caddyfile host and `prof/relay_url`.
- **One shared API key** protects a public HTTPS endpoint. Rotate it after sharing configs; per-trial `config.json` files
  contain it.
