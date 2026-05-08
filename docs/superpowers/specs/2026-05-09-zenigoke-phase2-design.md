# Zenigoke — Phase 2 design (catalog DB + antibody enrichment)

**Date:** 2026-05-09
**Status:** Draft, scoped for incremental delivery
**Predecessor:** `2026-04-24-zenigoke-phase1-design.md` (Phase 1 complete: 156/157 samples + 100%/92%/56% curation coverage)

## 1. Goal and scope

Turn the Phase 1 outputs (`/data1/zenigoke/output/`) and curated metadata (`metadata/curated/`) into a queryable read-only catalog. Anchor query: **filter samples by tissue × developmental_stage × genotype_strain × antibody**, return paths to BigWig / peak / methylation files. Phase 2 ships:

1. **2A — Antibody-target enrichment** (closes the 0% gap on 90 ChIP samples).
2. **2B — SQLite catalog schema** (single `kknmsmd.db` file under `~/work/zenigoke/db/`).
3. **2C — Static catalog HTML pages** (extends `build_report.py` into a multi-page browseable catalog).

Phase 2 is explicitly **not** a server, web API, interactive UI, or JBrowse integration. Read-only files on disk, openable in a browser.

## 2. Sub-piece 2A — Antibody enrichment

**Why:** BioSample records describe the biological material (tissue, cultivar, age) but not the IP antibody. The antibody info lives in the SRA Experiment record (`SRX*`). Phase 1's curation hit 0% on `antibody_target` because we only fetched BioSamples.

**What:**
- `scripts/curate-antibody.sh` — for each ChIP-Seq accession in the CSV, fetch the SRA Experiment XML (ENA `xml/SRX*` endpoint) and write `metadata/experiments/{SRX}.xml`.
- Convert XML to a JSON shape compatible with bsllmner-mk2's `characteristics` schema, with per-experiment fields for `library_construction_protocol`, `experiment_title`, `design_description`, and any populated antibody-related attributes.
- Run a focused bsllmner-mk2 Select pass with a single-field config (`antibody_target` only, with a tighter prompt that includes ChIP-specific examples like `H3K4me3`, `H3K27me3`, `H3K9me2`, `MpGCAM1`, `RNA Pol II`, `input`).
- Merge the antibody field into existing `metadata/curated/{SRX}.json` (under a new `extract_experiment` key, parallel to the existing `extract`).

**Out of scope:** ATAC and BS-seq don't have antibodies — skip them. No re-curation of tissue/stage/etc. — just the one new field.

**Success metric:** non-null `antibody_target` for ≥60% of the 90 ChIP samples.

## 3. Sub-piece 2B — SQLite catalog

**Why:** A flat directory of JSONs and BigWigs isn't queryable. Users want "show me all H3K4me3 samples on Tak-1 thallus" without grepping JSON.

**Schema** (single SQLite file, ~5 tables):

```sql
-- Core sample row, one per accession.
CREATE TABLE sample (
  accession            TEXT PRIMARY KEY,             -- e.g. SRX7548553
  library_strategy     TEXT NOT NULL,                -- ChIP-Seq / ATAC-Seq / Bisulfite-Seq
  status               TEXT NOT NULL,                -- ok / failed
  layout               TEXT,                         -- SE / PE
  reads_filtered       INTEGER,
  mapping_rate         REAL,                         -- 0–100 (percent)
  duplication_rate     REAL,
  elapsed_min          REAL,
  biosample_accession  TEXT,                         -- e.g. SAMN13672528
  output_dir           TEXT NOT NULL                 -- /data1/zenigoke/output/{strat}/{acc}
);

CREATE TABLE sample_curation (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  tissue               TEXT,
  cell_type            TEXT,
  developmental_stage  TEXT,
  genotype_strain      TEXT,
  treatment            TEXT,
  antibody_target      TEXT
);

-- ChIP/ATAC-specific facts.
CREATE TABLE sample_chipseq (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  peaks_q5             INTEGER,
  peaks_q10            INTEGER,
  peaks_q20            INTEGER,
  bigwig_path          TEXT,
  peaks_q5_path        TEXT,
  peaks_q10_path       TEXT,
  peaks_q20_path       TEXT
);

-- BS-seq-specific facts.
CREATE TABLE sample_bsseq (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  mean_cpg             REAL,
  mean_chg             REAL,
  mean_chh             REAL,
  cpg_hmr_count        INTEGER,
  cpg_hypermr_count    INTEGER,
  cpg_pmd_count        INTEGER,
  chg_hypermr_count    INTEGER,
  cpg_methyl_bw_path   TEXT,
  cpg_cover_bw_path    TEXT,
  cpg_hmr_path         TEXT,
  cpg_hypermr_path     TEXT,
  cpg_pmd_path         TEXT,
  chg_methyl_bw_path   TEXT,
  chg_cover_bw_path    TEXT,
  chg_hypermr_path     TEXT,
  chh_methyl_bw_path   TEXT,
  chh_cover_bw_path    TEXT
);

-- Indices for the anchor query (filter by tissue × stage × strain × antibody).
CREATE INDEX idx_curation_tissue   ON sample_curation(tissue);
CREATE INDEX idx_curation_stage    ON sample_curation(developmental_stage);
CREATE INDEX idx_curation_strain   ON sample_curation(genotype_strain);
CREATE INDEX idx_curation_antibody ON sample_curation(antibody_target);
CREATE INDEX idx_sample_strategy   ON sample(library_strategy);
CREATE INDEX idx_sample_status     ON sample(status);
```

**Build script:** `scripts/build_catalog_db.py` (Python 3 stdlib only — uses the built-in `sqlite3` module). Walks `/data1/zenigoke/status/`, `/data1/zenigoke/output/`, and `metadata/curated/`; populates all five tables; writes `db/kknmsmd.db`.

**Tests:** pytest, ≥4 tests covering: schema creation, sample insert, curation join, ChIP/BS-specific row insert, anchor query (`SELECT * FROM sample s JOIN sample_curation c USING (accession) WHERE c.tissue='thallus' AND c.antibody_target='H3K4me3'`).

**Out of scope:** triggers, foreign-key cascades, full-text search, multi-DB sharding.

## 4. Sub-piece 2C — Static catalog HTML pages

**Why:** Phase 1's report is a single 30 KB file. To make the catalog browseable, we need: a sample-detail page per accession, an index page filterable by the anchor fields, and the existing summary report.

**Layout:**

```
report/
├── index.html                  # filterable sample table (all 156 ok rows)
├── summary.html                # the existing Phase 1 overview (renamed)
├── samples/
│   └── {accession}.html        # one per sample — all stats + paths + curated metadata
├── strategy/
│   ├── chipseq.html            # all ChIP samples, sortable
│   ├── atacseq.html
│   └── bsseq.html
└── assets/
    └── style.css               # one shared stylesheet
```

`index.html` uses pure HTML + a tiny vanilla-JS filter (no React, no jQuery) — text input narrows visible rows. JS is ~30 lines, inline.

**Build script:** `scripts/build_catalog_pages.py` reads from `db/kknmsmd.db` (built by 2B) and emits the static tree. Python stdlib + `sqlite3` only. Idempotent.

**Tests:** pytest, ≥3 tests for the page generator (`build_catalog_pages` module): index rendering, per-sample page rendering, strategy page rendering. The vanilla-JS filter is verified manually in a browser, not unit-tested.

## 5. Order of delivery and dependencies

```
2A (antibody enrichment) ──┐
                           ├─► 2B (SQLite catalog) ──► 2C (HTML pages)
Existing Phase 1 outputs ──┘
```

2A and 2B are independent in code (2B reads whatever curated JSONs exist; 2A enriches them) — both can be built in parallel. 2C depends on 2B's `kknmsmd.db`.

**Suggested execution order:** 2A first (small, ~30 min runtime, biggest curation impact). Then 2B (schema + populator). Then 2C (templates).

## 6. Out of scope (explicit)

- Web server / REST API / GraphQL.
- Authentication.
- JBrowse track configuration / hosting.
- Re-running pipelines or re-curating tissue/stage/strain (those are Phase 1 deliverables and stand).
- Cross-sample analytics (colocalization, motif enrichment, GO term overlap).
- Pull-request integration with `dbcls/bsllmner-mk2` upstream.
- Phase 3 dynamic UI / public deployment.

## 7. Open items to resolve at first run

1. **ENA Experiment XML endpoint shape.** Need to confirm `https://www.ebi.ac.uk/ena/browser/api/xml/{SRX}` returns the antibody info we need. If not, fallback to NCBI eutils. (Probe at the start of 2A.)
2. **Antibody prompt calibration.** First pass on 5–10 known ChIP samples to verify the LLM recognizes plant-specific MpTF*/MpGCAM* names. May need prompt iteration.
3. **Vanilla-JS filter performance.** With 157 rows it's trivial; flagged just in case future expansion makes it laggy.
