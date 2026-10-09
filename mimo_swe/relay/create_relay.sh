#!/usr/bin/env bash
# Create the model-endpoint relay VM (static IP, firewall), then install Caddy + the affinity router on it.
# Sandboxes reach it as https://<ip-with-dashes>.sslip.io. Run from a machine with gcloud access to the project.
set -euo pipefail
ZONE=us-west3-c REGION=us-west3 NET=b200-vpc
HERE=$(cd "$(dirname "$0")" && pwd)
gcloud compute addresses create mimo-relay-ip --region $REGION --quiet
IP=$(gcloud compute addresses describe mimo-relay-ip --region $REGION --format='value(address)')
gcloud compute instances create mimo-relay --zone $ZONE --machine-type n2-standard-8 --network $NET --subnet $NET \
  --address $IP --tags mimo-relay --labels owner=charlieruan,purpose=mimo-harbor-eval \
  --image-family ubuntu-2204-lts --image-project ubuntu-os-cloud --boot-disk-size 50GB --quiet
# Public HTTPS in (80 is needed for the Let's Encrypt HTTP-01 challenge). The relay reaches the GPU nodes over the VPC
# through the existing b200-allow-internal rule (10.128.0.0/9 -> tag b200-train).
gcloud compute firewall-rules create mimo-relay-allow-web --network $NET --direction INGRESS --allow tcp:80,tcp:443 \
  --source-ranges 0.0.0.0/0 --target-tags mimo-relay
HOST=$(echo $IP | tr . -).sslip.io
gcloud compute scp $HERE/../affinity_router.py $HERE/setup_relay.sh ${BACKENDS:-$HERE/../../../backends_tp2.txt} mimo-relay:~ --zone $ZONE
gcloud compute ssh mimo-relay --zone $ZONE --command "mv ~/$(basename ${BACKENDS:-backends_tp2.txt}) ~/backends.txt; bash ~/setup_relay.sh $HOST"
echo "https://$HOST"
