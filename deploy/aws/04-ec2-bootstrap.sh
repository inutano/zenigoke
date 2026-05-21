#!/usr/bin/env bash
# Run this ONCE on the EC2 instance after it's launched and DNS is set.
# Idempotent: safe to re-run.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/inutano/zenigoke.git}"
BUCKET="${ZENIGOKE_BUCKET:-zenigoke-catalog}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
DOMAIN="${ZENIGOKE_DOMAIN:-zenigoke.inutano.com}"

# Wait for DNS to point at this instance (so caddy can get cert)
echo "== waiting for DNS $DOMAIN to point at this host =="
MY_IP=$(curl -s https://checkip.amazonaws.com)
for i in $(seq 1 30); do
  RESOLVED=$(dig +short "$DOMAIN" | tail -n1)
  if [ "$RESOLVED" = "$MY_IP" ]; then
    echo "  resolved: $RESOLVED"
    break
  fi
  echo "  attempt $i/30: DNS not yet propagated (got $RESOLVED, want $MY_IP)"
  sleep 10
done

echo "== installing system packages =="
sudo apt-get update -qq
sudo apt-get install -y \
  python3-pip python3-scipy bedtools git awscli \
  debian-keyring debian-archive-keyring apt-transport-https curl
# caddy from official repo
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -qq
sudo apt-get install -y caddy

echo "== cloning repo =="
cd "$HOME"
if [ ! -d zenigoke ]; then
  git clone "$REPO_URL"
fi
cd zenigoke
git pull --ff-only
pip install --user -r requirements.txt

echo "== syncing peak files from S3 (≈400 MB) =="
mkdir -p "$HOME/zenigoke-data"
aws s3 sync "s3://$BUCKET/output" "$HOME/zenigoke-data" \
  --region "$REGION" \
  --exclude "*.bw" --exclude "*.bb" --exclude "*.bedgraph"
# Also need the DB
mkdir -p db
aws s3 cp "s3://$BUCKET/db/kknmsmd.db" db/kknmsmd.db --region "$REGION"
# And chrom.sizes (needed by enrichment for genome_bp)
mkdir -p references/MpTak_v7.1
aws s3 cp "s3://$BUCKET/references/MpTak_v7.1/chrom.sizes" \
  references/MpTak_v7.1/chrom.sizes --region "$REGION" 2>/dev/null || \
  echo "  WARN: chrom.sizes not in S3 — upload separately"

echo "== writing /etc/zenigoke.env =="
sudo tee /etc/zenigoke.env > /dev/null <<EOF
ZENIGOKE_DB_PATH=$HOME/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=$HOME/zenigoke/report
ZENIGOKE_BUNDLES_DIR=$HOME/zenigoke/report/bundles
ZENIGOKE_PEAKS_DIR=$HOME/zenigoke-data
ZENIGOKE_CHROM_SIZES=$HOME/zenigoke/references/MpTak_v7.1/chrom.sizes
ZENIGOKE_PUBLIC_BASE=https://${DOMAIN}
ZENIGOKE_BUNDLES_PUBLIC=https://${BUCKET}.s3.${REGION}.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://inutano.github.io
EOF

echo "== installing systemd unit =="
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zenigoke

echo "== installing Caddyfile =="
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy

echo ""
echo "=== DONE ==="
echo "  https://${DOMAIN}/api/axes  ← test"
echo "  systemctl status zenigoke caddy"
