# zenigoke — deployment architecture & as-built runbook

**Status:** both modes live (2026-07-15). This is the *as-built* record — the
real accounts, resource IDs, and the fixes/gotchas we actually hit. The generic
per-script docs live in `deploy/aws/README.md`; this file is the source of truth
for **how the running system is wired and how to reproduce it**.

---

## 1. Architecture at a glance

Two independently useful layers. Mode A is the whole catalog; Mode B adds one
compute-backed feature (enrichment analysis).

```
                         ┌──────────────────────── Mode A (static, ~$1–2/mo) ───────────────────────┐
                         │                                                                          │
  build machine          │   GitHub (inutano/zenigoke)              S3  zenigoke-catalog-dsc        │
  (ota-ws-01)            │   ├─ main  ──push──▶ Actions              (chiba-dsc, ap-northeast-1)     │
  /data1/zenigoke/output │   │        pages.yml: build report/       ├─ output/  37 GB tracks       │
        │  aws s3 sync    │   │        with S3-flavoured track URLs   │           (bw/bb/peaks/beds) │
        └────────────────┼──▶│        └─push─▶ gh-pages ─▶ Pages     ├─ data|db backup             │
                         │                    │                       └─ references/chrom.sizes      │
                         │   viewer's browser ─┤                                   ▲                 │
                         │   https://inutano.github.io/zenigoke/                   │ IGV streams     │
                         │        ├─ matrix / browse / sample pages ───────────────┘ tracks (Range)  │
                         │        └─ enrichment.html ──POST /api/enrichment──┐                        │
                         └──────────────────────────────────────────────────┼────────────────────────┘
                                                                            │
                         ┌──────────────────────── Mode B (EC2 API, +~$15/mo) ───┼────────────────────┐
                         │                                                       ▼                     │
                         │   zenigoke.inutano.com  ──▶  EC2 t3.small (chiba-dsc default VPC)           │
                         │   (Route 53 A record,        i-06b55f62b1bb84191 @ EIP 35.73.72.14          │
                         │    profile inutano)          ├─ Caddy :443  Let's Encrypt, reverse-proxy ─┐ │
                         │                              │                                            ▼ │
                         │                              ├─ uvicorn 127.0.0.1:8088 (systemd zenigoke) │ │
                         │                              │   FastAPI: /api/axes,/matrix,/bundle,        │
                         │                              │            /api/enrichment                   │
                         │                              └─ reads peaks from ~/zenigoke-data,           │
                         │                                 pulled from S3 via IAM instance profile     │
                         └─────────────────────────────────────────────────────────────────────────────┘
```

Key idea: **the frontend is 100% static and account-agnostic.** It only needs
(a) the public S3 bucket for track data and (b) — for the enrichment page only —
the EC2 API. Kill Mode B and everything except `enrichment.html` still works.

---

## 2. Resource inventory (as built)

| Thing | Value |
|---|---|
| **Compute + storage account** | AWS profile `chiba-dsc` — account `090413359466` (tazro.ohta@chiba-u.jp) |
| **DNS account** | AWS profile `inutano` — account `788543821682`, Route 53 zone `inutano.com` (`Z02132493TE75JCLL4WGV`) |
| **Region** | `ap-northeast-1` (Tokyo) |
| **S3 bucket** | `zenigoke-catalog-dsc` (public-read GetObject via bucket policy; CORS from `deploy/s3-cors.json`) |
| **GitHub repo** | https://github.com/inutano/zenigoke (public); Pages served from `gh-pages` branch |
| **Public site (Mode A)** | https://inutano.github.io/zenigoke/ |
| **EC2 instance** | `i-06b55f62b1bb84191`, `t3.small`, Ubuntu 22.04 (AMI `ami-0d52744d6551d851e`), 20 GB gp3, chiba-dsc **default VPC** |
| **Elastic IP** | `35.73.72.14` |
| **Security group** | `sg-084f7fe9af597da5a`: 22←`133.82.251.170/32` (build machine), 80←`0.0.0.0/0`, 443←`0.0.0.0/0` |
| **SSH key** | key pair `zenigoke` → `~/.ssh/zenigoke.pem` (created by `03-launch-ec2.sh`) |
| **EC2 → S3 auth** | IAM instance profile/role `zenigoke-ec2-s3read`, inline policy `s3read-zenigoke-catalog-dsc` = `s3:ListBucket` on bucket + `s3:GetObject` on bucket/* |
| **API endpoint (Mode B)** | https://zenigoke.inutano.com (Caddy auto-TLS via Let's Encrypt) |
| **DNS record** | `zenigoke.inutano.com` A → `35.73.72.14`, TTL 300 |

> AWS CLI access uses the `chiba-dsc` profile and its session can expire —
> re-auth (`aws login`/SSO or refresh creds for that profile) before running any
> `aws ... --profile chiba-dsc` command below.

---

## 3. Data flow

**Mode A (catalog):** `build_catalog_pages.py` runs in GitHub Actions with
`ZENIGOKE_DATA_BASE=https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.com/`.
Every track/download link (matrix JS **and** per-sample pages) is rewritten to an
absolute S3 URL. The published HTML is tiny; the browser/IGV streams the big
BigWig/peak files straight from S3 over HTTPS Range requests (that's why the S3
CORS rules must expose `Accept-Ranges`/`Content-Range`).

**Mode B (enrichment):** `enrichment.html` reads `window.ZENIGOKE_API_BASE`
(baked to `https://zenigoke.inutano.com` by `pages.yml`) and POSTs a BED to
`/api/enrichment`. The FastAPI app intersects the user's regions against the
catalogued peak files in `~/zenigoke-data` (bedtools), computes a binomial test
per experiment with `genome_bp` from `chrom.sizes`, and returns BH q-values. The
app allows CORS only from `https://inutano.github.io` (`ZENIGOKE_CORS_ORIGIN`).

---

## 4. Runbook — Mode A (static catalog)

All commands from the build machine (`ota-ws-01`), repo root, with data present
at `/data1/zenigoke/output` and `db/kknmsmd.db` built.

```bash
export AWS_PROFILE=chiba-dsc          # compute/storage account

# 1. Bucket + public-read policy + CORS (idempotent). Defaults to the -dsc name.
bash deploy/aws/01-bucket.sh

# 2. Push the 37 GB of tracks + the DB (idempotent; --size-only). ~6–10 min on a
#    fast uplink. NOTE: this script does NOT upload references/, so also do:
bash deploy/aws/02-sync-data.sh
aws s3 cp /data1/zenigoke/references/MpTak_v7.1/chrom.sizes \
  s3://zenigoke-catalog-dsc/references/MpTak_v7.1/chrom.sizes --region ap-northeast-1

# 3. Publish the frontend (first time: create the GitHub repo, then enable Pages).
gh repo create inutano/zenigoke --public --source=. --remote=origin --push   # once
gh workflow run pages.yml --ref main            # or just push a change under the paths filter
gh api -X POST repos/inutano/zenigoke/pages -f 'source[branch]=gh-pages' -f 'source[path]=/'  # enable Pages, once
```

Verify: `curl -s -o /dev/null -w '%{http_code}\n' https://inutano.github.io/zenigoke/`
returns 200, and a per-sample page's `.bw` link is an absolute
`zenigoke-catalog-dsc.s3...` URL that answers a Range request with HTTP 206.

---

## 5. Runbook — Mode B (enrichment API)

```bash
export AWS_PROFILE=chiba-dsc

# 1. Launch t3.small + SG + EIP (idempotent). chiba-dsc has a default VPC, so
#    this "just works" — EXCEPT the SG step gets the launcher IP from
#    `curl checkip.amazonaws.com`, which is blocked on the Chiba-U network. If it
#    dies at "Creating security group", add the ingress rules by hand:
MYIP=$(curl -s https://icanhazip.com)      # checkip is blocked here; this works
SG=$(aws ec2 describe-security-groups --region ap-northeast-1 \
       --filters Name=group-name,Values=zenigoke-api --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 authorize-security-group-ingress --region ap-northeast-1 --group-id $SG --protocol tcp --port 22  --cidr ${MYIP}/32
aws ec2 authorize-security-group-ingress --region ap-northeast-1 --group-id $SG --protocol tcp --port 80  --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region ap-northeast-1 --group-id $SG --protocol tcp --port 443 --cidr 0.0.0.0/0
bash deploy/aws/03-launch-ec2.sh           # re-run: skips the existing SG, creates the instance + EIP

# 2. DNS: point the domain at the EIP (Route 53 lives in the *inutano* profile).
aws route53 change-resource-record-sets --profile inutano \
  --hosted-zone-id Z02132493TE75JCLL4WGV \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"zenigoke.inutano.com","Type":"A","TTL":300,"ResourceRecords":[{"Value":"<EIP>"}]}}]}'

# 3. Give the EC2 read access to S3 (bootstrap's `aws s3 sync` needs s3:ListBucket,
#    which the public bucket policy does NOT grant). Use an IAM instance profile.
#    Creating IAM roles is gated by the Claude Code auto-mode guard, so this is
#    packaged as a script the user runs:  bash ~/run/zenigoke-iam.sh
#    (creates role+policy `zenigoke-ec2-s3read`, attaches it to the instance).

# 4. Bootstrap ON the instance (waits for DNS, installs deps + caddy, pulls peaks
#    from S3, writes /etc/zenigoke.env, installs systemd + Caddyfile, gets cert).
ssh -i ~/.ssh/zenigoke.pem ubuntu@<EIP>
git clone https://github.com/inutano/zenigoke.git && cd zenigoke
bash deploy/aws/04-ec2-bootstrap.sh
```

`/etc/zenigoke.env` as written by the bootstrap:

```
ZENIGOKE_DB_PATH=/home/ubuntu/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=/home/ubuntu/zenigoke/report
ZENIGOKE_BUNDLES_DIR=/home/ubuntu/zenigoke/report/bundles
ZENIGOKE_PEAKS_DIR=/home/ubuntu/zenigoke-data
ZENIGOKE_CHROM_SIZES=/home/ubuntu/zenigoke/references/MpTak_v7.1/chrom.sizes
ZENIGOKE_PUBLIC_BASE=https://zenigoke.inutano.com
ZENIGOKE_BUNDLES_PUBLIC=https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://inutano.github.io
```

The frontend already targets the API (`pages.yml` bakes
`ZENIGOKE_API_BASE=https://zenigoke.inutano.com`), so no Pages rebuild is needed
after the API comes up — but pushing an empty commit doesn't hurt.

---

## 6. Verification

```bash
# From the EC2 (or any network NOT behind the Chiba-U FortiGate — see gotchas):
curl -s -o /dev/null -w '%{http_code}\n' https://zenigoke.inutano.com/api/axes        # 200
echo | openssl s_client -connect zenigoke.inutano.com:443 2>/dev/null | openssl x509 -noout -issuer  # Let's Encrypt
curl -s -X POST https://zenigoke.inutano.com/api/enrichment \
  -H 'Content-Type: application/json' -H 'Origin: https://inutano.github.io' \
  -d '{"regions_bed":"chr1\t1000000\t1010000"}' | head -c 300                          # JSON results
# On the box:
ssh -i ~/.ssh/zenigoke.pem ubuntu@35.73.72.14 'systemctl is-active zenigoke caddy'
```

---

## 7. Updating after a catalog refresh

```bash
export AWS_PROFILE=chiba-dsc
python3 scripts/build-catalog-db.py                       # rebuild db/kknmsmd.db
bash deploy/aws/02-sync-data.sh                            # re-sync changed tracks + DB
git push origin main                                      # → Actions rebuilds Pages
# Mode B: refresh peaks on the EC2
ssh -i ~/.ssh/zenigoke.pem ubuntu@35.73.72.14 \
  'cd zenigoke && git pull && bash deploy/aws/04-ec2-bootstrap.sh && sudo systemctl restart zenigoke'
```

---

## 8. Teardown

```bash
export AWS_PROFILE=chiba-dsc
# Mode B
INST=$(aws ec2 describe-instances --region ap-northeast-1 --filters Name=tag:Name,Values=zenigoke-api \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
aws ec2 terminate-instances --region ap-northeast-1 --instance-ids "$INST"
aws ec2 release-address --region ap-northeast-1 --allocation-id "$(aws ec2 describe-addresses \
  --region ap-northeast-1 --filters Name=tag:Name,Values=zenigoke-api --query 'Addresses[0].AllocationId' --output text)"
aws iam remove-role-from-instance-profile --instance-profile-name zenigoke-ec2-s3read --role-name zenigoke-ec2-s3read
aws iam delete-instance-profile --instance-profile-name zenigoke-ec2-s3read
aws iam delete-role-policy --role-name zenigoke-ec2-s3read --policy-name s3read-zenigoke-catalog-dsc
aws iam delete-role --role-name zenigoke-ec2-s3read
# DNS (inutano profile) — delete the A record with an UPSERT→DELETE change batch.
# Mode A
aws s3 rb s3://zenigoke-catalog-dsc --force --region ap-northeast-1
```

> S3 holds a **deleted bucket name** for a while (minutes–hours), especially when
> recreating it in a *different* account. If you tear down and redeploy, expect
> `OperationAborted: A conflicting conditional operation is currently in progress`
> and either wait it out or pick a new bucket name (that's exactly why this
> deployment is `zenigoke-catalog-dsc` and not `zenigoke-catalog`).

---

## 9. Gotchas / lessons learned (things that actually bit us)

1. **Wrong account.** Mode A was first deployed to `togoid` (928810569478) by
   mistake and fully torn down. The correct account is **`chiba-dsc`**. DNS is
   separately in **`inutano`**. Cross-account (compute in one, DNS in another) is
   fine.
2. **Bucket name reuse is slow across accounts** → we use `zenigoke-catalog-dsc`.
   `pages.yml`, the deploy-script defaults, and the READMEs were repointed;
   historical `docs/superpowers/specs|plans` were left as-is.
3. **`checkip.amazonaws.com` is blocked on the Chiba-U network.**
   `03-launch-ec2.sh` uses it to fill the SSH ingress rule and dies with curl
   exit 6. Use `icanhazip.com`/`ifconfig.me` and add the SG rule manually.
4. **`aws s3 sync` needs `s3:ListBucket`.** The public bucket policy only grants
   `GetObject`, so the bootstrap can't `sync` anonymously. Solved with the
   `zenigoke-ec2-s3read` IAM instance profile (Get + List, bucket-scoped) — not
   by making the bucket publicly listable.
5. **`chrom.sizes` isn't synced by `02-sync-data.sh`** (it only does `output/` +
   DB). Upload it separately or enrichment loses `genome_bp`. Bootstrap only
   *warns* if it's missing.
6. **S3 CORS can't have two wildcards.** `http://*.tail*.ts.net` was rejected;
   replaced with `https://*.ts.net` in `deploy/s3-cors.json`.
7. **Per-sample links used to 404 on Pages.** `build_catalog_pages.py` emitted
   relative `../output/...` paths; the `output/` tree isn't published to Pages
   (it's S3-only). Fixed so `_file_link` honors `ZENIGOKE_DATA_BASE`.
8. **Creating IAM roles is gated by the Claude Code auto-mode guard.** Expected —
   hand the user a `~/run/*.sh` script to run, then continue.
9. **Verifying the API from the build machine shows a Fortinet cert / HTTP 000.**
   That's the Chiba-U FortiGate doing outbound TLS inspection on the build
   machine's traffic — a *local* artifact. The EC2 (and any off-campus client)
   sees the real Let's Encrypt endpoint. Verify from the EC2 or an external host.

---

## 10. Cost

| | $/mo |
|---|---|
| S3 storage (~37 GB) + GET/egress | ~$1–2 |
| GitHub Pages | free |
| EC2 t3.small + EIP + 20 GB gp3 (Mode B) | ~$15 |
| **Total with Mode B** | **~$16** |

Mode A alone is ~$1–2/mo. Tear down Mode B (§8) to drop back to that.
