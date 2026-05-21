# Zenigoke — Phase 5 design (enrichment analysis on EC2)

**Date:** 2026-05-20
**Status:** Draft, awaiting user review
**Predecessors:**
- Phase 1: pipelines (156/157 ok)
- Phase 2: catalog DB + static pages
- Phase 3: matrix → bundle → IGV
- Phase 4: static-only S3 + GitHub Pages deployment

## 1. Goal and scope

Add the central server-side analytical feature: **enrichment analysis** ("in silico ChIP" à la ChIP-Atlas). User uploads a BED of regions of interest; the system tests each of the 157 catalogued experiments for over-representation of peaks in those regions and returns a ranked list.

This is the one feature that genuinely needs a server — every other UI capability we have can be done client-side, but enrichment requires bedtools intersection + statistical tests against all 157 sample peak sets, which is unreasonable to download to a browser.

### Phase 5 ships

1. A new endpoint `POST /api/enrichment` on the FastAPI server.
2. A new page `/enrichment.html` on the static frontend (uploads BED, calls the API, renders a sortable result table, integrates with the existing "Send to IGV" flow for top hits).
3. EC2 deployment (t3.small, Tokyo) with caddy + Let's Encrypt at `zenigoke.inutano.com`.
4. CORS update so the GitHub Pages frontend can call the API.
5. Deployment automation (EC2 bootstrap script, Caddyfile updated for the real domain, optional CLI helper for EC2 launch and Route53 A record creation).

### Phase 5 explicitly NOT

- Gene-ID input (researcher pastes `MpXXXXX,MpYYYYY,...`) — deferred. BED only initially.
- Multi-test correction methods beyond Benjamini-Hochberg.
- Per-experiment differential analysis (DESeq2/DiffBind) — out of scope; that's a different kind of question.
- Motif enrichment (MEME/HOMER style) — out of scope.
- Saved enrichment queries / shareable result URLs — defer; first ship and observe usage.
- Re-introduction of consensus tracks — explicitly dropped per discussion 2026-05-19; the matrix-pick-and-IGV-stack flow makes consensus redundant.

## 2. Architecture

```
GitHub Pages (HTTPS)            EC2 t3.small (zenigoke.inutano.com)
├── enrichment.html             ├── caddy (auto-TLS via Let's Encrypt)
│   ├── BED upload form         └── uvicorn (existing FastAPI app + new endpoint)
│   ├── result table                ├── /api/axes        (existing)
│   └── "open top N in IGV"         ├── /api/matrix      (existing)
│                                   ├── /api/bundle      (existing)
│   POST → zenigoke.inutano.com     └── /api/enrichment  (NEW)
│                                       ↓
│                                   bedtools intersect (per experiment)
│                                       ↓
│                                   scipy.stats.binomtest + BH correction
│                                       ↓
│                                   ranked JSON response

S3 (Tokyo, public-read)         Catalog DB at /home/ubuntu/zenigoke/db/kknmsmd.db
└── output/{strat}/{acc}/*.bw   Peak files at /home/ubuntu/zenigoke-data/{strat}/{acc}/*.narrowPeak,*.bed
    *.narrowPeak, *.bed         (synced from S3 to local disk at bootstrap;
                                 only ~400 MB so it fits easily)
```

Why local peak files on EC2 instead of streaming from S3: bedtools intersect needs to read each peak file once per query. 157 S3 round-trips per enrichment call would dominate latency. A one-time `aws s3 sync` at EC2 bootstrap pulls the 400 MB of peak/BED files to local disk; subsequent enrichment calls are pure-local I/O.

## 3. The `/api/enrichment` endpoint

### Request

```json
POST /api/enrichment
Content-Type: application/json
{
  "regions_bed": "chr1\t1234\t5678\nchr1\t9000\t12000\n...",
  "q_cutoff": "1e-10",
  "filter": {"strategy": ["ChIP-Seq", "ATAC-Seq", "Bisulfite-Seq"]}
}
```

- `regions_bed`: BED text. Lines starting with `track` or `#` ignored. Minimum 3 columns (chrom, start, end). Max 50,000 regions (return 400 if exceeded; the test isn't designed for genome-wide scans).
- `q_cutoff`: which catalog peak file to test against; one of `1e-5`, `1e-10`, `1e-20`. Default `1e-10`.
- `filter.strategy`: optional list; defaults to all three strategies.

### Algorithm (per experiment)

1. Read the experiment's peak file (`{acc}.{q_label}_peaks.narrowPeak` for ChIP/ATAC; `{acc}.CpG.hmr.bed` for BS-seq).
2. `bedtools intersect -u -a user.bed -b peaks` → count of user regions touched by at least one peak (`k`).
3. `p_null = total_peak_bp / genome_bp` (precomputed once per experiment at bootstrap; cached in the DB or a small JSON).
4. `p_value = scipy.stats.binom.sf(k - 1, n_user, p_null)` (one-sided, "more enriched than chance").
5. `fold_enrichment = (k / n_user) / p_null`.

### Output

```json
{
  "n_user_regions": 245,
  "n_experiments_tested": 157,
  "results": [
    {
      "accession": "SRX22603368",
      "library_strategy": "ChIP-Seq",
      "antibody_target": "H3K4me3",
      "genotype_strain": "Tak-1",
      "developmental_stage": "thallus",
      "overlap_count": 178,
      "p_null": 0.041,
      "fold_enrichment": 17.7,
      "p_value": 1.2e-38,
      "q_value": 1.5e-36
    },
    ...
  ]
}
```

- Results sorted by `q_value` ascending (most enriched first).
- BH correction applied across all tested experiments.

### Performance

- 157 experiments × ~10–500 KB per peak file × `bedtools intersect` ≈ 30–60 s serial.
- Parallelize via `concurrent.futures.ProcessPoolExecutor` with `max_workers=4` on a t3.small → ~10–15 s typical.
- Cache by hash of input BED + q_cutoff + filter → identical queries return in milliseconds.

## 4. The `/enrichment.html` page

### Layout

```
┌─NAV (Matrix | Browse | … | Enrichment) ────────────────────────┐
├──────────────────────────────────────────────────────────────────┤
│ Upload genomic regions (BED format)                              │
│ [ paste BED here, or drag-and-drop a file ]                      │
│ q-cutoff: [ 1e-10 ▾ ]   strategies: [☑ ChIP] [☑ ATAC] [☑ BS]    │
│                                                                  │
│         [ ▶ Run enrichment ]                                     │
│                                                                  │
│ ─── results (157 experiments tested, top 10 shown) ───           │
│  rank | acc        | antibody  | strain | stage    | overlap | fold | p-value | q-value | actions │
│   1   | SRX22603368| H3K4me3   | Tak-1  | thallus  | 178/245 | 17.7 | 1.2e-38 | 1.5e-36 | [→IGV] [sample] │
│   2   | SRX22603369| H3K4me3   | Tak-1  | thallus  | 165/245 | 16.4 | 7e-35   | 4e-33   | [→IGV] [sample] │
│   …                                                                                                       │
│                                                                  │
│ [ ▶ Open top 10 in IGV ]   [ ⤓ Download results CSV ]            │
└──────────────────────────────────────────────────────────────────┘
```

### Behavior

- BED textarea + drag-and-drop file input populate the same hidden field.
- "Run enrichment" → POST → render table.
- Row sort: click any column header → sort. Default sort: q-value ascending.
- "→IGV" per row: load that single experiment's tracks via `localhost:60151` (reuses Phase 4 client-side track builder).
- "Open top N in IGV": loads the top-10 rows' tracks all at once.
- "Download CSV": serializes the result table client-side.

### Error handling

- Empty BED → button disabled.
- BED that can't be parsed → 400 from API; surfaced as banner.
- API unreachable (DNS hasn't propagated, EC2 down) → friendly message with "try again in a minute".

## 5. Deployment

### Infrastructure layout

| Resource | Choice |
|---|---|
| Region | `ap-northeast-1` (Tokyo) |
| Compute | EC2 t3.small (2 vCPU, 2 GB) Ubuntu 22.04 |
| Storage | 20 GB gp3 root volume (enough for 400 MB peaks + indexes + OS) |
| Networking | EIP for stable IP; security group `:22 ssh from your IP`, `:80 + :443 from 0.0.0.0/0` |
| DNS | `zenigoke.inutano.com` A record → EIP (user manages inutano.com Route53 zone or wherever) |
| TLS | caddy with Let's Encrypt automatic |
| Process supervisor | systemd (`zenigoke.service` from `deploy/`) |

### One-time setup (CLI-scripted but split into reviewable steps)

```bash
# 1. Bucket already exists from Phase 4 — reuse it.

# 2. New: launch EC2 + EIP + security group
bash deploy/aws/03-launch-ec2.sh   # idempotent; outputs the EIP

# 3. User: create A record (inutano.com Route53 or wherever) pointing
#    zenigoke.inutano.com to the EIP. Wait for propagation.

# 4. On the EC2 instance (run once):
bash deploy/aws/04-ec2-bootstrap.sh   # installs deps, clones repo, syncs peak files from S3, starts caddy + systemd

# 5. Frontend rebuild + push:
git push origin main   # Pages workflow rebuilds with the new API base
```

### Env vars on the EC2 instance (`/etc/zenigoke.env`)

```
ZENIGOKE_DB_PATH=/home/ubuntu/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=/home/ubuntu/zenigoke/report
ZENIGOKE_BUNDLES_DIR=/home/ubuntu/zenigoke/report/bundles
ZENIGOKE_PEAKS_DIR=/home/ubuntu/zenigoke-data        # local mirror of S3 output/
ZENIGOKE_PUBLIC_BASE=https://zenigoke.inutano.com
ZENIGOKE_BUNDLES_PUBLIC=https://zenigoke-catalog.s3.ap-northeast-1.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://inutano.github.io
```

### Cost

- t3.small reserved or on-demand: ~$15/mo
- EIP: $0 when attached
- Outbound bandwidth: pennies at this traffic
- **Total: ~$15/mo + S3 from Phase 4**

## 6. Testing

### Unit tests

- `tests/test_api_enrichment.py`:
  - Fixture: tiny DB + 3 fake peak files + a chrom_sizes shim.
  - Test 1: empty BED → 400.
  - Test 2: known overlap → matches a hand-computed p_value within 1e-10.
  - Test 3: filter by strategy → only ChIP samples tested.
  - Test 4: identical request hits cache.
  - Test 5: BH q-values sum-correct vs raw p-values (test the BH math, not just call scipy).

### Integration test

- `tests/test_e2e_enrichment.py`: POST a small BED against the real `db/kknmsmd.db` + `/data1/zenigoke/output`, assert the top hit has the expected antibody (e.g. a BED of MpTAK-1 thallus DEG promoters should rank H3K4me3 ChIPs near the top).

### Manual

- Open `/enrichment.html` on Pages → paste a 100-region BED → result table appears within 15 seconds → "Open top 5 in IGV" loads tracks.

## 7. Out of scope (explicit)

- Gene-ID input (`MpXXXXX → promoter ±N kb`)
- Differential analysis between cells
- Motif enrichment
- Saved/shareable enrichment query URLs
- Multi-tenant / auth
- Consensus-track restoration
- Replicate-quality metrics across the catalog

## 8. Open items to resolve at first run

1. **DNS propagation**: caddy needs the A record live before it can issue the cert. The bootstrap script waits up to 5 min for DNS resolution before starting caddy.
2. **scipy install on t3.small**: pip install scipy on the small instance may take several minutes the first time (compiles wheels). Alternative: use `apt install python3-scipy` (older but installs in seconds).
3. **Peak file sync size**: ~400 MB. EC2 ↔ S3 in the same region transfers in seconds (no egress cost).
4. **Re-sync on catalog refresh**: when peak files change (e.g. you re-run Phase 1 on a new sample), you need to re-sync to EC2. Documented as a manual step in `04-ec2-bootstrap.sh` (and re-runnable).
