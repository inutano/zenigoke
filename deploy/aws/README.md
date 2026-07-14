# AWS deployment — static-only path

Single-user / single-friend catalog. **~$1/mo, no API server, no SSH.**

```
your laptop ──aws s3 sync──> S3 (Tokyo)              GitHub Pages (free)
                              output/*.bw,bed,peak     index.html, matrix.js, …
                              data/*.json (built)
                              db/kknmsmd.db (backup)
                              ↑ IGV streams these directly via HTTPS
```

## Prerequisites

- AWS account, region = `ap-northeast-1` (Tokyo).
- AWS CLI v2 installed and `aws configure` already run on your laptop (a personal IAM user with `AmazonS3FullAccess` is enough for the one-time bucket + sync).
- GitHub repo is public (Pages free).
- bedtools is **not** needed in this deployment path — consensus tracks are produced only by the optional API mode (see "Adding consensus tracks back" below).

## One-time setup (5 minutes)

```bash
# 1. Create the bucket (idempotent; safe to re-run)
bash deploy/aws/01-bucket.sh

# 2. First data sync — ~37 GB over your home link; can take an hour
bash deploy/aws/02-sync-data.sh

# 3. Enable GitHub Pages
#    Repo Settings → Pages → Source: "Deploy from a branch"
#                          Branch: "gh-pages" / "(root)"
#    (The first push to main triggers .github/workflows/pages.yml,
#     which creates the gh-pages branch.)

# 4. Push to main
git push origin main

# 5. Wait ~1 min, then open:
#    https://<your-username>.github.io/<repo-name>/
```

## Updating the catalog

When pipeline outputs change or the DB is rebuilt:

```bash
python3 scripts/build-catalog-db.py          # refresh db/kknmsmd.db
bash deploy/aws/02-sync-data.sh              # uploads output/ AND db/kknmsmd.db
git push origin main                          # → GitHub Actions rebuilds Pages
```

The workflow downloads `db/kknmsmd.db` from S3 at build time, so the DB on S3 is the source of truth for the published site.

## What the workflow does (`.github/workflows/pages.yml`)

1. Checks out the repo on `ubuntu-latest`.
2. Installs Python + `requirements.txt`.
3. Downloads `s3://zenigoke-catalog-dsc/db/kknmsmd.db` (anonymous HTTPS — bucket is public-read).
4. Runs `python3 scripts/build-catalog-pages.py` with `ZENIGOKE_DATA_BASE` set to the S3 HTTPS URL, so each per-sample track link in the static pages points at S3.
5. Publishes `report/` to the `gh-pages` branch via `peaceiris/actions-gh-pages`.

No AWS credentials needed in GitHub Secrets — the bucket is public-read.

## Costs (typical)

| | $/mo |
|---|---|
| S3 storage (37 GB) | ~$0.85 |
| S3 GET requests (researcher viewing tracks) | ~$0.10 |
| S3 outbound (IGV streaming BigWigs) | depends on use; <$1 for moderate browsing |
| GitHub Pages | free |
| **Total** | **~$1–2** |

## Local development unchanged

`python3 scripts/server.py` still serves the FastAPI version of the catalog over Tailscale, including the consensus-track API. The static build emits the same artifacts but with S3-flavored track URLs.

## Mode B (optional): EC2 API for enrichment analysis

Adds a `t3.small` in Tokyo running the FastAPI app with a new
`POST /api/enrichment` endpoint (bedtools intersect + binomial test
against the catalogued experiments). The static Pages frontend gains an
`/enrichment.html` page that calls this API.

**Cost:** ~$15/mo on top of the static path.
**DNS:** requires a real domain (the scripts assume `zenigoke.inutano.com`;
override `ZENIGOKE_DOMAIN` to use another).

### Launch + bootstrap

```bash
# 1. Launch EC2 + EIP + security group (idempotent)
bash deploy/aws/03-launch-ec2.sh
# → prints the public IP at the end. Save it.

# 2. Set DNS in your domain panel:
#    A record: zenigoke.inutano.com → <the EIP from step 1>
#    Wait for propagation (5–30 min).

# 3. SSH into the instance and bootstrap:
ssh -i ~/.ssh/zenigoke.pem ubuntu@<EIP>
git clone https://github.com/<you>/zenigoke.git
cd zenigoke
bash deploy/aws/04-ec2-bootstrap.sh
#   - installs python3-pip, scipy, bedtools, caddy
#   - syncs ~400 MB of peak files from S3 to /home/ubuntu/zenigoke-data
#   - writes /etc/zenigoke.env
#   - installs systemd unit + caddy
#   - caddy auto-issues Let's Encrypt cert for zenigoke.inutano.com
```

### Trigger Pages rebuild

After the API is live, push an empty commit so the frontend rebuild
picks up the `ZENIGOKE_API_BASE` env baked into `.github/workflows/pages.yml`:

```bash
git commit --allow-empty -m "trigger Pages rebuild for enrichment API"
git push origin main
```

### Verify

```bash
# from anywhere:
curl https://zenigoke.inutano.com/api/axes | head -c 200
# from the EC2 instance:
sudo systemctl status zenigoke caddy
sudo journalctl -u zenigoke -n 50
```

Then open `https://<you>.github.io/zenigoke/enrichment.html`, paste a
small BED, click Run. Expect results within ~30 seconds.

### Updating after a catalog refresh

When peak files change, re-sync to the EC2:

```bash
ssh -i ~/.ssh/zenigoke.pem ubuntu@<EIP>
cd zenigoke
git pull
bash deploy/aws/04-ec2-bootstrap.sh   # idempotent; only changed files re-sync
sudo systemctl restart zenigoke
```

### Tearing down mode B

```bash
INST_ID=$(aws ec2 describe-instances --region ap-northeast-1 \
  --filters 'Name=tag:Name,Values=zenigoke-api' \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
aws ec2 terminate-instances --region ap-northeast-1 --instance-ids "$INST_ID"
aws ec2 release-address --region ap-northeast-1 \
  --allocation-id "$(aws ec2 describe-addresses --region ap-northeast-1 \
    --filters 'Name=tag:Name,Values=zenigoke-api' \
    --query 'Addresses[0].AllocationId' --output text)"
```

## Adding consensus tracks back later

The consensus-track Phase 3 endpoint (`/api/bundle`) is also enabled when
mode B is active — same FastAPI process. Per-sample tracks always work
in static mode; consensus is a mode B bonus.

## CORS

`deploy/s3-cors.json` allows GET/HEAD from `https://*.github.io`, `http://localhost:8088`, and `http://*.tail*.ts.net` (Tailscale). The first bucket script (`01-bucket.sh`) applies it.

## Tearing down

```bash
aws s3 rb s3://zenigoke-catalog-dsc --force --region ap-northeast-1
```
