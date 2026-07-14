# Deployment

Two modes share the same code; pick one.

## A) Static-only (S3 + GitHub Pages) — recommended for sharing

**~$1/mo, no API server, no SSH.** See [`aws/README.md`](aws/README.md).

```bash
bash deploy/aws/01-bucket.sh    # one-time
bash deploy/aws/02-sync-data.sh # one-time + whenever data changes
git push origin main             # → workflow rebuilds Pages
```

What you lose vs mode B: server-side `bedtools merge` consensus tracks (per-sample tracks still work; IGV displays them side by side).

## B) Full API on EC2 — needed only if you want consensus tracks

Heavier (~$15/mo, requires a domain for TLS). The `Caddyfile`, `zenigoke.service`, and original instructions are preserved here as reference.

### Initial setup (manual; not auto-applied)

```bash
# 1. S3 bucket (same as mode A)
bash deploy/aws/01-bucket.sh

# 2. Initial data sync
bash deploy/aws/02-sync-data.sh

# 3. EC2 t3.small (Ubuntu 22.04). On the instance:
sudo apt-get update && sudo apt-get install -y python3-pip bedtools caddy
git clone https://github.com/<org>/zenigoke.git
cd zenigoke
pip install --user -r requirements.txt

# 4. /etc/zenigoke.env — fill these in:
sudo bash -c 'cat > /etc/zenigoke.env <<EOF
ZENIGOKE_DB_PATH=/home/ubuntu/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=/home/ubuntu/zenigoke/report
ZENIGOKE_BUNDLES_DIR=/home/ubuntu/zenigoke/report/bundles
ZENIGOKE_PUBLIC_BASE=https://api.zenigoke.example.com
ZENIGOKE_BUNDLES_PUBLIC=https://zenigoke-catalog-dsc.s3.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://<user>.github.io
EOF'

# 5. systemd unit
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl enable --now zenigoke

# 6. Caddy (requires a real domain pointed at the EC2 IP)
sudo API_HOST=api.zenigoke.example.com caddy run --config deploy/Caddyfile
```

### Frontend in mode B

Same as mode A — push to `main`, the GitHub Actions workflow builds and publishes to Pages. The published pages use S3 URLs for the per-sample tracks (regardless of whether the consensus API is up).
