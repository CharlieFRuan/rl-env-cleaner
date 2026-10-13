#!/bin/bash
#SBATCH --job-name=net-test
#SBATCH --account=reformo
#SBATCH --partition=booster
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --time=00:20:00
#SBATCH --output=/e/data1/mmlaion/playground/ruan1/logs/%x_%j.out
# Connectivity check for MiMo jobs: outbound via the login-node proxy, inbound via the GCP relay.
source ~/.bashrc
source ~/.config/jupiter-relay/proxy.env
set -a; source $WORK/charlie_keys.env; set +a
IP=$(hostname -i | tr ' ' '\n' | grep '^10\.128\.' | head -1); echo "node $(hostname) ib ip $IP"
export NO_PROXY=localhost,127.0.0.1,.jupiter.internal,$IP no_proxy=localhost,127.0.0.1,.jupiter.internal,$IP
curl -s -m 20 -o /dev/null -w "wandb via proxy: %{http_code}\n" https://api.wandb.ai/healthz
curl -s -m 20 -o /dev/null -w "daytona via proxy: %{http_code}\n" https://app.daytona.io/api/health
curl -s -m 20 -o /dev/null -w "github via proxy (expect fail): %{http_code}\n" https://github.com || echo "github blocked (ok)"
$DATA/envs/skyrl-mimo/bin/python - <<'PY'
import asyncio, os
from daytona import AsyncDaytona, DaytonaConfig
async def main():
    async with AsyncDaytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"])) as d:
        r = await d.list()
        items = getattr(r, "items", r)
        print("daytona SDK ok, sandboxes listed:", len(items))
asyncio.run(main())
PY
mkdir -p /tmp/www && echo "hello-from-$(hostname)" > /tmp/www/index.html
python3 -m http.server 11500 --bind 0.0.0.0 --directory /tmp/www > /dev/null 2>&1 &
echo "SERVING http://$IP:11500"; nvidia-smi --query-gpu=name,memory.total --format=csv
sleep 600
