# Session handoff — zenigoke / kknmsmd

**As of:** 2026-05-29
**Repo:** `/home/inutano/work/zenigoke/` (machine `ota-ws-01`, Tailscale IP `100.88.253.33`)
**Branch:** `main` (all phases merged + tagged; no in-flight work)

A multiomics catalog for *Marchantia polymorpha*. 157 SRA experiments
(ChIP/ATAC/BS-Seq) processed against MpTak v7.1, curated via bsllmner-mk2,
served as an interactive web catalog with optional EC2-hosted enrichment
analysis.

---

## Phase summary

| Phase | Status | Tag | What it ships |
|---|---|---|---|
| 1 | ✅ done 2026-04-25 | `phase1-complete` | Pipelines processed 156/157 samples (89/90 ChIP, 36/36 ATAC, 31/31 BS-seq). Curation 100% genotype, 92% dev_stage, 56% tissue. |
| 2 | ✅ done 2026-05-06 | (merged into Phase 3) | Antibody enrichment via SRA Experiment XML: 87/90 (97%) coverage. SQLite catalog at `db/kknmsmd.db`. 164 static HTML pages. |
| 3 | ✅ done 2026-05-09 | `phase3-complete` | FastAPI + matrix UI + on-demand consensus bundles + IGV port command integration. |
| 4 | ✅ done 2026-05-15 | `phase4-complete` | Static-only S3+Pages deployment. No API server needed. ~$1/mo. Consensus track dropped (IGV stacking gives the same biological signal). |
| 5 | ✅ done 2026-05-20 | `phase5-complete` | Enrichment analysis (ChIP-Atlas in-silico-ChIP). `POST /api/enrichment` on EC2 t3.small at `zenigoke.inutano.com`. ~$15/mo. |

**Tests:** 51/51 pytest passing on `main`.

---

## Where things live

### Code

```
scripts/
├── server.py              # FastAPI entry point (used in local + EC2 modes)
├── api_axes.py            # GET /api/axes
├── api_matrix.py          # GET /api/matrix
├── api_bundle.py          # POST /api/bundle (consensus via bedtools — local/EC2 only)
├── api_enrichment.py      # POST /api/enrichment (NEW Phase 5; EC2 only in production)
├── igv_url_helper.py      # IGV track URL building
├── build_catalog_db.py    # ⇒ db/kknmsmd.db
├── build_catalog_pages.py # ⇒ report/*.html
├── build_static_data.py   # ⇒ report/data/{axes,matrix-*}.json
├── build_report.py        # ⇒ report/summary.html (Phase 1 summary)
├── bootstrap.sh           # Phase 1 — set up data dirs
├── prepare-marchantia.sh  # Phase 1 — download MpTak v7.1 + indexes
├── pipeline-v2-bs-plant.sh    # Phase 1 — plant-fork BS-seq pipeline
├── run-sample.sh / run-all.sh # Phase 1 — per-sample / driver
├── retry-pe-fallback.sh   # Phase 1 — ENA "PAIRED but single-file" workaround
├── curate-metadata.sh     # Phase 2 — BioSample → bsllmner-mk2 Select
├── curate-antibody.sh     # Phase 2A — SRA Experiment XML → antibody field
└── curate-antibody-gap.sh # Phase 2A — improved-prompt rerun for the 17 gap samples

report/
├── index.html             # matrix top page
├── browse.html            # all-samples table
├── bundle.html            # drilldown shell (reads URL params)
├── enrichment.html        # NEW Phase 5
├── summary.html, methods.html, about.html
├── samples/{acc}.html × 157
├── strategy/{chipseq,atacseq,bsseq}.html
└── assets/{matrix.js,matrix.css,enrichment.js,style.css}

tests/                     # pytest, 51 tests across 8 files
deploy/
├── Caddyfile              # auto-TLS for zenigoke.inutano.com
├── zenigoke.service       # systemd unit
├── s3-cors.json
└── aws/
    ├── 01-bucket.sh           # Phase 4 — create S3 bucket
    ├── 02-sync-data.sh        # Phase 4 — sync outputs + DB to S3
    ├── 03-launch-ec2.sh       # Phase 5 — launch t3.small + EIP + SG (idempotent)
    ├── 04-ec2-bootstrap.sh    # Phase 5 — runs ON the EC2 to set everything up
    └── README.md              # the deployment walkthrough
```

### Data (outside the repo, gitignored)

```
/data1/zenigoke/
├── references/MpTak_v7.1/  # 2.3 GB — FASTA + indexes
├── output/                 # 37 GB — BigWigs + peaks + methylation BEDs
└── status/, fastq/, logs/, tmp/, bundles/   # pipeline scratch / status markers

~/work/zenigoke/
├── db/kknmsmd.db           # 160 KB — SQLite catalog (gitignored; build with scripts/build-catalog-db.py)
├── metadata/biosamples/{acc}.json + experiments/{acc}.xml  # curation provenance
└── metadata/curated/{acc}.json                              # bsllmner-mk2 output
```

### Docs

```
docs/superpowers/
├── specs/
│   ├── 2026-04-24-zenigoke-phase1-design.md
│   ├── 2026-05-09-zenigoke-phase2-design.md
│   ├── 2026-05-09-zenigoke-phase3-design.md
│   └── 2026-05-20-zenigoke-phase5-enrichment.md
└── plans/
    ├── 2026-04-24-zenigoke-phase1-plan.md
    ├── 2026-05-09-zenigoke-phase3-plan.md
    └── 2026-05-20-zenigoke-phase5-plan.md
```

---

## Running locally

```bash
cd ~/work/zenigoke

# (re)start the FastAPI server (Tailscale-accessible)
[ -f /tmp/zenigoke-server.pid ] && kill $(cat /tmp/zenigoke-server.pid) 2>/dev/null
nohup python3 scripts/server.py > /tmp/zenigoke-server.log 2>&1 &
echo $! > /tmp/zenigoke-server.pid

# URL: http://100.88.253.33:8088/
```

Regenerate built artifacts (only needed after code changes):

```bash
python3 scripts/build-catalog-db.py
python3 scripts/build-catalog-pages.py
```

Run tests:

```bash
python3 -m pytest tests/ -q   # expect 51 passed
```

---

## Deploying to AWS

Two modes; pick one. Both documented in `deploy/aws/README.md`.

### Mode A — static-only (Phase 4)

Just S3 + GitHub Pages. ~$1/mo. **No enrichment analysis** (no server-side compute).

```bash
bash deploy/aws/01-bucket.sh
bash deploy/aws/02-sync-data.sh
# Enable Pages in repo Settings → Pages → gh-pages branch
git push origin main      # → workflow rebuilds & deploys
```

### Mode B — static + EC2 API (Phase 5)

Adds `/api/enrichment` on a `t3.small` at `zenigoke.inutano.com`. ~$15/mo on top.

```bash
# (do A first to set up the S3 bucket and sync data)
bash deploy/aws/03-launch-ec2.sh
# → outputs EIP. Set DNS: zenigoke.inutano.com → <EIP> (A record)

# Wait 5-30 min for DNS, then SSH in:
ssh -i ~/.ssh/zenigoke.pem ubuntu@<EIP>
git clone https://github.com/<you>/zenigoke.git
cd zenigoke
bash deploy/aws/04-ec2-bootstrap.sh

# Back on laptop:
git commit --allow-empty -m "trigger Pages rebuild for Phase 5"
git push origin main
```

---

## Where you left off

### Open / next-action items (not done yet)

1. **Mode B deployment not yet executed.** Code + scripts are ready; the EC2
   hasn't been launched. To bring `/api/enrichment` live for your friend:
   - `bash deploy/aws/03-launch-ec2.sh` from your laptop
   - Set DNS `zenigoke.inutano.com → <EIP>`
   - SSH in and run `bash deploy/aws/04-ec2-bootstrap.sh`
   - Push an empty commit to trigger Pages rebuild

2. **Mode A deployment not yet executed.** Same — code is ready, S3 bucket and
   GitHub Pages haven't been set up. Run `01-bucket.sh` + `02-sync-data.sh` +
   enable Pages.

### Known limitations carried forward (from review subagents)

- **Cache poisoning** on `/api/bundle` when bedtools transiently fails: the
  first failed run writes a permanent stale cache entry. Workaround: clear
  `report/bundles/{hash}/` manually. Phase 4+ fix: `?force=1` query param.
- **`x == y` matrix request** returns a diagonal of no value. Phase 4 added a
  client-side guard but the API still returns the useless result.
- **`SRX29617452`** failed in Phase 1 (malformed source data, not pipeline);
  accepted as a 1/157 loss.
- **3 ChIP samples without antibody** after Phase 2A gap recovery (generic
  `4_TAK-1_ChIP` titles); accepted as null.
- **CHG `hmr.bed` not produced** by `dnmtools` — pipeline catches the failure
  with a warning; spec was updated.
- **Consensus tracks** were intentionally dropped in Phase 4. Per-sample
  tracks loaded into IGV give the same biological signal by stacking. Phase 5
  considered Mode B "consensus restoration" obsolete after this realisation.
- **`tissue` axis excluded** from the matrix — only 1 distinct value in the
  curated metadata (re-curation is a Phase 6+ candidate).
- **PO subset OWLs not built** — bsllmner-mk2 falls through to LLM-only
  string extraction.

### Phase 6+ candidates (when you want to think bigger)

- **Target gene aggregation**: peak → nearest gene via GFF3; "top 100 H3K27me3
  targets across the catalog."
- **Differential analysis** (WT vs mutant for the same antibody): peak-count
  matrix + DESeq2.
- **Cross-sample similarity** for replicate QC (Spearman of binned signal).
- **Tissue re-curation** — sample more BioSample text + Experiment XML to
  improve the 56% coverage.
- **Public submission** path so collaborators can add their own samples.
- **Replicate consistency metric** displayed on each sample page.

---

## Key URLs and credentials

- **Local catalog:** http://100.88.253.33:8088/ (Tailscale, requires the
  FastAPI server running)
- **Public catalog (after Mode A deploy):** `https://<your-github>.github.io/zenigoke/`
- **API (after Mode B deploy):** `https://zenigoke.inutano.com`
- **GitHub repo:** TBD (user-controlled; the workflow assumes public)
- **AWS region:** `ap-northeast-1` (Tokyo)
- **S3 bucket:** `zenigoke-catalog` (public-read, CORS allows `*.github.io` + Tailscale)
- **EC2 key pair:** `zenigoke` (written by `03-launch-ec2.sh` to `~/.ssh/zenigoke.pem`)

---

## How to start the next session

```bash
cd ~/work/zenigoke
cat SESSION-HANDOFF.md       # this file
git log --oneline -5         # last commits
ps -p $(cat /tmp/zenigoke-server.pid 2>/dev/null) || echo "server not running"
```

Then either:
- **Resume deployment** (Mode A or B per "open items" above), or
- **Phase 6 brainstorm** for the next analytical feature, or
- **Fix one of the known limitations** above as a small-scope cleanup.
