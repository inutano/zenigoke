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
3. Downloads `s3://zenigoke-catalog/db/kknmsmd.db` (anonymous HTTPS — bucket is public-read).
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

## Adding consensus tracks back later

If you want server-side `bedtools merge`, launch an EC2 t3.small in the same region, install `bedtools`, run `python3 scripts/server.py`, and point a custom domain at the IP. The Caddyfile and systemd unit in `../` cover that.

## CORS

`deploy/s3-cors.json` allows GET/HEAD from `https://*.github.io`, `http://localhost:8088`, and `http://*.tail*.ts.net` (Tailscale). The first bucket script (`01-bucket.sh`) applies it.

## Tearing down

```bash
aws s3 rb s3://zenigoke-catalog --force --region ap-northeast-1
```
