````markdown
# Cloud deployment notes (manual; not auto-applied)

See `docs/superpowers/specs/2026-05-09-zenigoke-phase3-design.md` §6 for context.

## Initial setup

```bash
# 1. S3 bucket
aws s3 mb s3://zenigoke-catalog
aws s3api put-bucket-cors --bucket zenigoke-catalog \
  --cors-configuration file://deploy/s3-cors.json

# 2. Initial data sync (run from the dev host)
aws s3 sync /data1/zenigoke/output s3://zenigoke-catalog/output
aws s3 cp db/kknmsmd.db s3://zenigoke-catalog/db/

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
ZENIGOKE_BUNDLES_PUBLIC=https://zenigoke-catalog.s3.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://<user>.github.io
EOF'

# 5. systemd unit
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl enable --now zenigoke

# 6. Caddy
sudo API_HOST=api.zenigoke.example.com caddy run --config deploy/Caddyfile
```

## Frontend deploy

```bash
# Build the static catalog with S3 URLs in the track links:
ZENIGOKE_S3_BASE=https://zenigoke-catalog.s3.amazonaws.com python3 scripts/build-catalog-pages.py

# Push to gh-pages
git checkout gh-pages
cp -r report/* .
git add -A && git commit -m "publish catalog"
git push origin gh-pages
```
````
