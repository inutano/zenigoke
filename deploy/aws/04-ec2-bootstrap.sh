#!/usr/bin/env bash
# Run this on the EC2 instance after it's launched and DNS is set, and again
# after every data update (02-sync-data.sh): each run pulls the code, mirrors
# the peak files (deleting ones no longer on S3), refreshes the DB and
# restarts the service. Idempotent: safe to re-run.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/inutano/zenigoke.git}"
BUCKET="${ZENIGOKE_BUCKET:-zenigoke-catalog-dsc}"
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
# caddy from official repo. gpg --batch --yes: on a re-run the keyring exists and
# plain gpg would ask to overwrite it on /dev/tty (no tty here -> abort).
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -qq
# --force-confold: this script overwrites /etc/caddy/Caddyfile (a package conffile);
# when a re-run upgrades caddy, keep it rather than stopping at dpkg's conffile prompt.
sudo apt-get install -y -o Dpkg::Options::=--force-confold caddy

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
# --delete: peak files the current outputs no longer have (e.g. v1 peaks of
# samples whose v2 peaks were all blacklisted) must not stay usable by enrichment.
aws s3 sync "s3://$BUCKET/output" "$HOME/zenigoke-data" \
  --region "$REGION" --delete \
  --exclude "*.bw" --exclude "*.bb" --exclude "*.bedgraph"
# Refresh the DB on every run; download beside it and rename so the running
# service never opens a half-written file.
mkdir -p db
aws s3 cp "s3://$BUCKET/db/kknmsmd.db" db/kknmsmd.db.tmp --region "$REGION"
mv -f db/kknmsmd.db.tmp db/kknmsmd.db
# And chrom.sizes (needed by enrichment for genome_bp)
mkdir -p references/MpTak_v7.1
aws s3 cp "s3://$BUCKET/references/MpTak_v7.1/chrom.sizes" \
  references/MpTak_v7.1/chrom.sizes --region "$REGION" 2>/dev/null || \
  echo "  WARN: chrom.sizes not in S3 — upload separately"

echo "== enrichment cache dir =="
# Blacklist-filtered peak files are cached here; /var/cache survives reboots
# (unlike the /tmp default). Owned by the service user (this user; see zenigoke.service).
ENRICH_CACHE="${ZENIGOKE_ENRICH_CACHE:-/var/cache/zenigoke-enrich}"
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$ENRICH_CACHE"
# Entries are keyed by peak path + mtime, so peaks removed or replaced by the
# --delete sync above would leave orphans forever: clear them on every refresh.
# The service restart below drops its in-memory copy of the same cache. Empty the
# directory (not remove it) so a request in flight never finds it missing.
if [ -d "$ENRICH_CACHE/blacklisted" ]; then
  find "$ENRICH_CACHE/blacklisted" -mindepth 1 -delete
fi

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
ZENIGOKE_ENRICH_CACHE=${ENRICH_CACHE}
EOF

echo "== installing systemd unit =="
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable zenigoke
# restart (not just enable --now) so a re-run picks up the new env, DB and
# peaks and drops the API's in-memory enrichment cache
sudo systemctl restart zenigoke

echo "== installing Caddyfile =="
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy

echo ""
echo "=== DONE ==="
echo "  https://${DOMAIN}/api/axes  ← test"
echo "  systemctl status zenigoke caddy"
