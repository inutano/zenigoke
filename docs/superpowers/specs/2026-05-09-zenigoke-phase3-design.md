# Zenigoke — Phase 3 design (matrix → bundle → IGV webapp)

**Date:** 2026-05-09
**Status:** Draft, awaiting user review
**Predecessors:**
- `2026-04-24-zenigoke-phase1-design.md` (pipelines, 156/157 ok)
- `2026-05-09-zenigoke-phase2-design.md` (antibody enrichment, SQLite catalog, static pages)

## 1. Goal and boundary

Replace the static catalog top page with an interactive matrix-driven webapp. User picks two attributes for the X/Y axes from `{experiment_type, genotype_strain, genotype_class, developmental_stage}`; matrix shows sample counts per cell. User multi-selects cells (a row, a column, or arbitrary cells), reviews the resulting bundle in a side panel, and clicks **Send to IGV** — backend builds a consensus track on the fly + assembles per-sample track URLs and POSTs them to the user's local IGV at `localhost:60151`.

### Phase 3 ships

1. A FastAPI backend serving the existing static catalog files **and** new JSON endpoints for matrix/bundle.
2. A new top page `/` with the matrix + side panel (vanilla JS, no framework).
3. A drilldown bundle page at `/bundle/{hash}` (shareable URL, full sample table, advanced controls).
4. Consensus-BED generation via `bedtools` on demand, cached on disk (local mode) or S3 (cloud mode).
5. The existing static pages (samples, strategy, methods, about, summary) stay reachable from nav, with the old "All Samples" view renamed to **Browse**.
6. A two-mode deployment: same code runs single-host (Tailscale) or AWS (GitHub Pages + EC2 + S3); switching is env-vars only.

### Phase 3 explicitly NOT

- A public service. Single-user trust model — no auth at the application layer.
- A genome-browser replacement. Embedded `igv.js` was deferred (option C in brainstorm).
- A peak-merging analytics platform. Consensus is `bedtools merge` of peaks at q ≤ 1e-10; nothing fancier.
- A way to write or modify catalog data. Read-only operations + ephemeral bundle artifacts.

### Phase 4+ candidates (explicitly noted, deferred)

- **Enrichment analysis** — ChIP-Atlas-style "in silico ChIP". Given user regions, score which experiments are enriched.
- **Target gene analysis** — peak → gene assignment, top-N targets per antibody.
- **Embedded igv.js** — same-page genome browser for users without IGV installed.
- **Authentication / multi-tenant** — public submission, accounts, audit logging.
- **Bundle queue** — async bedtools when bundle sizes grow past a few minutes (relevant when sample count grows from 157 to thousands).
- **Tissue re-curation** — the `tissue` axis stays excluded until coverage improves.

## 2. Architecture

A single FastAPI process replaces the current `python3 -m http.server`. It does three things:

1. **Static file serving** — keeps serving everything currently in `report/` (sample pages, strategy pages, methods, about, summary, `assets/`, `output/` symlink in local mode; redirected to S3 in cloud mode).
2. **JSON API endpoints** — for the matrix UI to fetch counts, expand a cell to its sample list, and submit a bundle.
3. **Bundle generation** — synchronous on-demand `bedtools merge` of peak files for the selected samples, with caching.

### Local mode

```
                           ┌─────────────────────────┐
  Browser (matrix, JS) ──► │   FastAPI on port 8088  │
                           │  GET /api/axes          │
                           │  GET /api/matrix?x=&y=  │
                           │  POST /api/bundle       │
                           │  GET / (matrix HTML)    │
                           │  GET /samples/...       │  (static)
                           │  GET /output/...        │  (symlink to /data1)
                           │  GET /bundles/{hash}/   │  (cached)
                           └─────────────────────────┘
                                       │
                                       ▼
                           ┌─────────────────────────┐
                           │   db/kknmsmd.db (SQLite)│  read-only
                           │   /data1/.../*.bw       │
                           │   /data1/.../*.narrowPeak│
                           │   bundles/{hash}/        │  cached
                           └─────────────────────────┘
```

### Cloud mode

```
            GitHub Pages (static)
              │ matrix UI, sample/strategy/methods/about pages
              │ JS calls api.zenigoke.example.com
              ▼
        ┌───────────────────────────────────────┐
        │  EC2 t3.small + caddy + uvicorn       │
        │  /api/axes /api/matrix /api/bundle    │
        │  reads kknmsmd.db (on EBS)            │
        │  shells out to bedtools (apt)         │
        │  reads/writes S3 for source + cache   │
        └───────────────┬───────────────────────┘
                        │ aws sdk + signed URLs
                        ▼
          ┌──────────────────────────────────┐
          │  S3 bucket: zenigoke-catalog     │
          │  output/{strat}/{acc}/*.bw       │ (read-only, public-CORS)
          │  output/{strat}/{acc}/*.peaks    │
          │  output/bsseq/{acc}/*.bw         │
          │  bundles/{hash}/consensus.bed    │ (written by API)
          └──────────────────────────────────┘
```

The user's IGV (on their Mac) fetches BigWig data directly from S3 (cloud) or `report/output/*` via the catalog server (local) — no API in that path. Switching modes is purely an env-var change; no code branches.

### File structure (new code)

```
scripts/
├── server.py            # FastAPI app entry point + uvicorn launcher
├── api_axes.py          # /api/axes
├── api_matrix.py        # /api/matrix
├── api_bundle.py        # /api/bundle (and bedtools shell-out)
└── igv_url_helper.py    # builds track URL lists, group colors
templates/
└── index.html           # matrix UI (rendered once at startup, served as static)
report/assets/
├── matrix.js            # vanilla JS for the matrix interactions
└── matrix.css           # matrix-specific styles
report/bundles/{hash}/   # ephemeral; gitignored; auto-pruned
├── manifest.json        # samples, q_cutoff, generation time, warnings
└── consensus.{strat}.bed
deploy/
├── Caddyfile            # auto-TLS reverse proxy → uvicorn
├── zenigoke.service     # systemd unit (committed, not auto-installed)
└── s3-cors.json         # CORS config for the data bucket
```

## 3. UX flow and UI structure

### Top page (`/`) layout

```
┌───────────────────────────────────────────────────────────────────┐
│ NAV: Matrix | Browse | ChIP-Seq | ATAC-Seq | BS-Seq | Methods | About │
├───────────────────────────────────────────────────────────────────┤
│ X axis: [ experiment_type ▾ ]    Y axis: [ genotype_strain ▾ ]   │
│ filters: [ ChIP-Seq only ] [ exclude null ] [ exclude WT ]        │
├──────────────────────────────────┬────────────────────────────────┤
│  ┌─MATRIX────────────────────┐   │  ┌─SELECTION (collapsed)──┐    │
│  │       Tak-1  mut1  mut2…  │   │  │ Pick a cell to begin   │    │
│  │ ATAC   16            3    │   │  │                         │    │
│  │ BS     20    7        … │   │  │                         │    │
│  │ ChIP:H3K27me3  4   2    │   │  │                         │    │
│  │ ChIP:H3K4me3   3   2    │   │  │                         │    │
│  │ ChIP:H2Aub     5   2    │   │  │                         │    │
│  └─────────────────────────┘   │  └────────────────────────┘    │
└──────────────────────────────────┴────────────────────────────────┘
```

### Cell-click flow

When the user clicks a populated cell, the side panel expands with the sample list (all checked by default). Each subsequent cell click toggles a cell into/out of the multi-select set. Side panel groups by source cell so the comparison stays visible.

```
┌─SELECTION──────────────────────┐
│ ChIP:H3K27me3 × Tak-1   ✓4    │
│ ☑ SRX29617475                 │
│ ☑ SRX29617477                 │
│ ☑ SRX22603368                 │
│ ☑ SRX22603369                 │
│                                │
│ + click another cell to add   │
│                                │
│ [ ▶ Send to IGV ]             │
│ [ ⤓ Download IGV session ]    │
│ [ details page ↗ ] [ clear ]  │
└────────────────────────────────┘
```

### State machine

```
idle ──pick axes──► axes-selected ──click cell──► cells-selected
                                          ▲           │
                                          │           ├─click another cell─┐
                                          │           │                     │
                                          └───────────┴─────────────────────┘
                                                      │
                                                      ▼
                                          ──Send to IGV──► IGV-loaded
                                                      │
                                                      └─►drilldown page (/bundle/{hash})
```

### Drilldown page (`/bundle/{hash}`)

- URL is content-addressed by `hash(sorted sample list + q_cutoff)`. Same hash → same page. Bookmarkable, shareable.
- Full sample table (accession, strategy, antibody, strain, stage, peaks_q10, mapping_rate, links to per-sample page).
- Q-cutoff selector (1e-5 / 1e-10 / 1e-20) — recomputes the consensus track.
- Same Send-to-IGV / Download-session actions.
- "Source" panel showing which cell(s) were merged.

### Existing static pages

`/samples/*.html`, `/strategy/*.html`, `/summary.html`, `/methods.html`, `/about.html` stay at their current URLs. Nav adds **Matrix** as the first link and renames the old "All Samples" to **Browse**.

### Null-cell handling

Cells where one or both axis values is null are **hidden** by default. A toggle "show unknowns as `(unknown)` row/column" reveals them — opt-in.

### Error handling

- IGV-not-running: the POST to `localhost:60151` will fail fast. Frontend catches and shows a banner: "Couldn't reach IGV at :60151 — check it's running with the port enabled, or click 'Download session' instead."
- Bundle of zero samples: button disabled.
- Bundle too large (>50 samples): a confirm dialog warns about IGV track count.

## 4. Backend API design

Six endpoints. All under `/api/` except the bundle pages and BED files.

### `GET /api/axes`

Returns the four selectable attributes and the values present in each, with counts.

```json
{
  "axes": [
    {"key": "experiment_type", "label": "Experiment type",
     "values": [{"value": "ATAC-Seq", "n": 36}, {"value": "ChIP:H3K27me3", "n": 10}]},
    {"key": "genotype_strain", "label": "Genotype / strain",
     "values": [{"value": "Tak-1", "n": 60}]},
    {"key": "genotype_class", "label": "Genotype class",
     "values": [{"value": "wildtype", "n": 87}, {"value": "mutant", "n": 60}, {"value": "overexpression", "n": 9}]},
    {"key": "developmental_stage", "label": "Developmental stage",
     "values": [{"value": "thallus", "n": 89}]}
  ]
}
```

### `GET /api/matrix?x={axis}&y={axis}&include_unknown={0|1}`

Returns the 2D count grid plus row/column labels. Includes accessions per cell so the frontend doesn't need a follow-up call when the user clicks (cheap at this scale — a few KB).

```json
{
  "x_axis": "experiment_type",
  "y_axis": "genotype_strain",
  "x_values": ["ATAC-Seq", "Bisulfite-Seq", "ChIP:H3K27me3"],
  "y_values": ["Tak-1", "Mpez1,Mpknox2 ...", "wild type"],
  "cells": [
    {"x": "ATAC-Seq", "y": "Tak-1", "n": 16, "accessions": ["SRX...", "SRX..."]}
  ]
}
```

### `POST /api/bundle`

Body:

```json
{
  "accessions": ["SRX29617475", "SRX29617477"],
  "q_cutoff": "1e-10",
  "groups": [
    {"label": "ChIP:H3K27me3 × Tak-1", "accessions": ["SRX29617475", "SRX29617477"]}
  ]
}
```

`groups` is for multi-cell selections; each becomes a track-color group in IGV.

Response:

```json
{
  "hash": "a3f7c2e8b9d4",
  "drilldown_url": "/bundle/a3f7c2e8b9d4",
  "consensus_url": "https://.../bundles/a3f7c2e8b9d4/consensus.ChIP-Seq.bed",
  "tracks": [
    {"name": "consensus ChIP-Seq q≤1e-10 (n=2)", "url": "...", "type": "annotation", "color": "#1a9970"},
    {"name": "SRX29617475 — H3K27me3 / Tak-1", "url": "...", "type": "wig", "color": "#3060a0"},
    {"name": "SRX29617475 peaks", "url": "...", "type": "annotation", "color": "#3060a0"}
  ],
  "warnings": []
}
```

If the consensus already exists for the same hash, the endpoint returns immediately. Otherwise it computes synchronously (≤2 s typical).

### IGV loading: client-side only

The IGV port lives on the user's Mac, not the server. Frontend JS does:

```js
const url = bundle.tracks.map(t => `${t.url}|${t.name}`).join(',');
fetch(`http://localhost:60151/load?file=${encodeURIComponent(url)}`);
```

Backend never talks to IGV.

### `GET /bundle/{hash}` (HTML)

Drilldown page. Generated server-side from the cached `manifest.json`. Cached HTML output too.

### `GET /bundles/{hash}/consensus.{strat}.bed`

Static file serve. Same FastAPI process, same `report/` mount in local mode; redirected to S3 (302) in cloud mode.

### Caching policy

- `report/bundles/` is gitignored (local mode); `s3://.../bundles/` (cloud mode).
- Hash: `sha256(",".join(sorted(accessions)) + "|" + q_cutoff)[:16]`.
- TTL: 30 days. A cron job (documented but not shipped in Phase 3) prunes older.
- Cap: warn at >1000 entries; no auto-eviction in Phase 3.

### CORS

Cloud mode: API allows the GitHub Pages origin (`https://<user>.github.io`) and the configured custom domain. S3 bucket has CORS allowing GET / range from those origins (BigWig HTTP-range streaming).

## 5. Bundle generation pipeline

Synchronous part of `POST /api/bundle`. Stays under 2 seconds for typical bundle sizes.

### Algorithm

```python
def build_bundle(accessions: list[str], q_cutoff: str, groups: list[Group]) -> Bundle:
    samples = load_sample_rows(db, accessions)
    by_strategy = group_by_strategy(samples)

    consensus_paths = []
    for strategy, strat_samples in by_strategy.items():
        peak_files = [_consensus_input(s, q_cutoff) for s in strat_samples]
        peak_files = [p for p in peak_files if p and exists_on_storage(p)]
        if len(peak_files) >= 2:
            out = bundle_dir / f"consensus.{strategy}.bed"
            run(f"cat {' '.join(peak_files)} | sort -k1,1 -k2,2n | bedtools merge -i - > {out}")
            consensus_paths.append((strategy, out, len(peak_files)))

    tracks = []
    for strat, path, n in consensus_paths:
        tracks.append(Track(name=f"consensus {strat} q≤{q_cutoff} (n={n})",
                            url=public_url(path), type="annotation", color="#1a9970"))
    for sample in samples:
        tracks.extend(per_sample_tracks(sample, group_color_for(sample, groups)))

    write_manifest(bundle_dir / "manifest.json", samples, q_cutoff, groups, tracks)
    return Bundle(hash=h, tracks=tracks)
```

### Per-sample track defaults

| Strategy | Tracks per sample | Notes |
|---|---|---|
| ChIP-Seq | 2: `{id}.bw` + `{id}.{q}_peaks.narrowPeak` | q from selector (default 1e-10) |
| ATAC-Seq | 2: same shape | |
| Bisulfite-Seq | 3 BigWigs: `{id}.CpG.methyl.bw`, `{id}.CHG.methyl.bw`, `{id}.CHH.methyl.bw` | Cover BigWigs and HMR/PMD BEDs available in drilldown via "show all" toggle. |

Rationale: 5-sample ChIP bundle = 11 tracks. 5-sample BS-seq bundle = 16 tracks. Both IGV-tractable.

### Consensus per strategy

| Strategy | Consensus input | Operation |
|---|---|---|
| ChIP-Seq | `{q}_peaks.narrowPeak` per sample | `cat … \| sort -k1,1 -k2,2n \| bedtools merge -i -` |
| ATAC-Seq | same | same |
| Bisulfite-Seq | `{id}.CpG.hmr.bed` per sample | `cat … \| sort \| bedtools merge -i -` (CpG only — CHG hmr isn't produced; see Phase 1 §5.3) |

`bedtools` is in `ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0` (local mode) or `apt install bedtools` on the EC2 host (cloud mode). Same version pinning as Phase 1.

### Mixed-strategy bundles

Multi-cell selections can cross strategies. Produce **one consensus per strategy** rather than a mixed cross-strategy consensus. Per-sample tracks are still combined.

### Edge cases

- Single sample in selection: skip consensus generation.
- Failed sample (status='failed') in selection: include curation row; skip its tracks. Frontend shows it greyed-out.
- Missing on-disk file: track entry omitted, logged in `manifest.json["warnings"]`. Doesn't break the bundle.
- Group coloring: 8-color palette; cycles if more than 8 groups.
- Bundle of >50 samples: API returns 200 + `warning`; frontend shows confirm dialog.

### Cloud-mode peak retrieval

S3 input. Two options were considered:

- **(a) chosen:** `aws s3 cp` the needed peak files to `/tmp/{hash}/`, run bedtools, upload `consensus.bed` back to S3. Few hundred ms total. Simple, robust.
- (b) rejected: mount via s3fs; bedtools reads as if local. Adds dependency, has consistency caveats.

## 6. AWS deployment (cloud mode)

### Stack

| Layer | Choice | Rationale |
|---|---|---|
| Static frontend | **GitHub Pages** | Free, version-controlled, `git push` = deploy. |
| API | **One EC2 t3.small** + caddy (auto-TLS) + uvicorn | One config file. SSH-debuggable. ~$15/mo. |
| Data | **S3** + CORS + public-read | BigWigs / peaks / BEDs / methylation. ~$1/mo for 37 GB. |
| DNS | **Route53** | `zenigoke.example.com` (Pages) + `api.zenigoke.example.com` (EC2). ~$0.50/mo. |
| **Total** | | **~$20/mo** |

Skipped: Amplify (overkill), ECS Fargate (ALB > $16/mo alone), Lambda (bedtools layer + cold start friction).

### One-time deployment

```
# upload data:
aws s3 sync /data1/zenigoke/output s3://zenigoke-catalog/output
aws s3 cp db/kknmsmd.db s3://zenigoke-catalog/db/   # backup
aws s3api put-bucket-cors --bucket zenigoke-catalog --cors-configuration file://deploy/s3-cors.json

# API host:
git clone .../zenigoke
pip install -r requirements.txt
caddy run --config deploy/Caddyfile
systemctl enable zenigoke

# frontend:
ZENIGOKE_S3_BASE=https://zenigoke-catalog.s3.amazonaws.com python3 scripts/build-catalog-pages.py
git push origin gh-pages   # deploys via Pages
```

### Local-vs-cloud configuration

Single env var swap. No code branches.

| Variable | Local mode | Cloud mode |
|---|---|---|
| `ZENIGOKE_DATA_BASE` | `/data1/zenigoke/output` | `s3://zenigoke-catalog/output` |
| `ZENIGOKE_PUBLIC_BASE` | `http://100.88.253.33:8088/output` | `https://zenigoke-catalog.s3.amazonaws.com/output` |
| `ZENIGOKE_BUNDLES_BASE` | `report/bundles` | `s3://zenigoke-catalog/bundles` |
| `ZENIGOKE_BUNDLES_PUBLIC` | `http://100.88.253.33:8088/bundles` | `https://zenigoke-catalog.s3.amazonaws.com/bundles` |
| `ZENIGOKE_CORS_ORIGIN` | `*` | `https://<user>.github.io` |

### Authentication / access

Tailscale-only (local mode) or **public-read** (cloud mode). No application-layer auth. Documented as "single-user / friend-of-author trust model — not a multi-tenant service".

## 7. Testing strategy

Three tiers, modest:

1. **API unit tests** (`tests/test_api_*.py`) — pytest + FastAPI `TestClient`. One file per endpoint module:
   - `test_api_axes.py` — confirms the four axes return the expected value sets and counts; confirms `tissue` is excluded.
   - `test_api_matrix.py` — picks two axes, asserts cells count matches a hand-rolled SQL query against a tiny fixture DB.
   - `test_api_bundle.py` — POSTs a known sample list, mocks `subprocess.run` for bedtools, asserts manifest structure and that hash collision yields cache hit.

2. **Frontend smoke** — no JS unit tests (small DOM-coupled JS). One Playwright integration test would be valuable; deferred to Phase 4 to limit Phase 3 dependency surface. Manual verification via the running server is the bar for shipping.

3. **End-to-end smoke** (`tests/test_e2e_smoke.py`) — one test that:
   - Starts uvicorn against a fixture DB + a tiny output tree.
   - Hits `/api/matrix?x=experiment_type&y=genotype_class`, asserts non-empty cells.
   - Hits `/api/bundle` with two real sample accessions, asserts `consensus.bed` exists and is non-empty.
   - Confirms the static fallback returns the existing `samples/SRX*.html` unchanged.

**Test count target:** 8–12 new tests on top of the existing 29.

## 8. Out of scope (explicit)

Phase 3 ships matrix → bundle → IGV. Everything below is Phase 4+ unless re-scoped:

- **Enrichment analysis** (in silico ChIP).
- **Target gene analysis** (peak-to-gene, top-N targets).
- **Embedded igv.js** (option C in brainstorm).
- **Authentication** — accounts, role-based access, audit logging.
- **Cross-sample analytics** beyond `bedtools merge`.
- **Bundle annotations** (saved searches, named bundles, comments).
- **Real-time catalog updates** — current model: rebuild + restart.
- **Multi-tenant deployment** (single bucket / single DB / single host).
- **Public submission** — researchers uploading their own samples.
- **JBrowse / WashU / UCSC track-hub formats**.
- **Mobile-first frontend** — desktop-first.
- **Tissue re-curation** — `tissue` axis stays excluded.
- **CHG `hmr.bed` recovery** — dnmtools limitation.
- **The 3 ChIP samples without antibody** — accepted as null.

## 9. Open items to resolve at first run

1. **AWS account + IAM** — bucket name, IAM user with `s3:PutObject`/`GetObject`, EC2 instance role. Out of scope for the implementation; a deployment prerequisite.
2. **DNS / domain** — pick a subdomain. The implementer doesn't choose this.
3. **Whether the GitHub repo is public or private** for the frontend (Pages works either way; private may need GitHub Pro).
4. **CORS origin list** for the API once the Pages URL is known.
5. **Initial S3 sync** wall-clock will depend on residential upload speed; one-time pain.
