# Zenigoke — Marchantia polymorpha multiomics database, Phase 1 design

**Date:** 2026-04-24
**Status:** Draft, awaiting user review
**Scope:** Phase 1 — pipeline run + curated metadata + summary report. No database, no web UI.

## 1. Goal and boundary

Produce, for each of the 157 Marchantia SRA experiments in `zenigoke_sra_experiments.csv`, a directory of pipeline outputs (BigWigs, peaks, methylation BEDs) and a curated per-sample metadata JSON, plus one HTML summary report.

Phase 1 is *done* when:

1. Every reachable SRA accession has either a populated output directory or a logged `.failed` status.
2. Every accession has a `metadata/curated/{accession}.json` (possibly with empty fields) from bsllmner-mk2.
3. `report/phase1-summary.html` opens in a browser and shows overview, failures, per-strategy tables, and curation columns.

Phase 2 (database schema, query API, web UI) is explicitly out of scope for this spec.

## 2. Inputs, constraints, and fixed decisions

### Inputs

- `~/work/zenigoke/zenigoke_sra_experiments.csv` — 157 rows: 90 ChIP-Seq, 36 ATAC-Seq, 31 Bisulfite-Seq. Header `library_strategy,experiment_accession`.
- Reference: **`MpTak_v7.1_standard_genome`** from MarpolBase (<https://marchantia.info/>), the combined male/female standard.
- Pipelines: `inutano/chip-atlas-pipeline-v2` (already cloned at `~/repos/chip-atlas-pipeline-v2`; containers published at `ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0` and `…-bs:v1.1.0`).
- Metadata curator: `dbcls/bsllmner-mk2` with its merged plant config (PR #3, `scripts/select-config-plants.json`, Plant Ontology for `tissue` + `cell_type`).

### Constraints

- **Compute:** local workstation only. 32 cores, 93 GB RAM, 713 GB free on `/data1` (NVMe), 189 GB free on `/`.
- **No upstream changes.** `chip-atlas-pipeline-v2` will not accept additions. All Marchantia/plant-specific logic lives in this repo. The BS-seq pipeline is forked locally.
- **Ollama model:** `qwen3.5:27b` is the pinned default for bsllmner-mk2. Runtime error if not pullable.

### Fixed decisions from brainstorm

- Run locally, one sample at a time, BS-seq pipeline gets all 32 cores per sample.
- Use the `MpTak_v7.1_standard_genome` combined reference.
- BS-seq outputs all three methylation contexts (CpG + CHG + CHH).
- Metadata curation scope = plant PO (tissue, cell_type) + custom extract-only fields (developmental_stage, genotype_strain, treatment, antibody_target).
- Phase-1 deliverable boundary is tight + an HTML summary report.

## 3. Directory layout

Code and small metadata in `~/work/zenigoke/` (committed to git). Large binaries on `/data1/zenigoke/` (outside git).

```
~/work/zenigoke/
├── zenigoke_sra_experiments.csv
├── scripts/
│   ├── prepare-marchantia.sh        # download ref + build indexes, idempotent
│   ├── pipeline-v2-bs-plant.sh      # local fork of upstream BS-seq pipeline
│   ├── run-sample.sh                # dispatcher: download FASTQ + pick pipeline
│   ├── run-all.sh                   # top-level driver over the CSV
│   ├── curate-metadata.sh           # fetch BioSamples + run bsllmner-mk2
│   └── build-report.py              # aggregate stats → single-file HTML
├── configs/
│   └── select-config-zenigoke.json  # custom extract-only fields
├── metadata/
│   ├── biosamples/{accession}.json  # raw BioSample JSON from NCBI/ENA
│   └── curated/{accession}.json     # merged bsllmner-mk2 Select output
├── docs/superpowers/specs/…         # this document + future plans
└── report/
    └── phase1-summary.html          # final artifact

/data1/zenigoke/
├── references/MpTak_v7.1/
│   ├── MpTak_v7.1.fa  +  .fa.fai  +  chrom.sizes
│   ├── MpTak_v7.1.gff3
│   ├── MpTak_v7.1.fa.bwt.2bit.64  +  bwa-mem2 companion files
│   ├── MpTak_v7.1.abismal.idx
│   └── macs_gsize.txt               # non-N base count, read by run-sample.sh
├── fastq/{accession}/               # raw FASTQs; deleted after successful run
├── tmp/{accession}/                 # per-sample scratch
├── status/{accession}.{ok,failed}   # run-all.sh resume markers
├── logs/{accession}.log             # per-sample stdout+stderr
└── output/
    ├── chipseq/{accession}/         # from upstream pipeline-v2.sh
    ├── atacseq/{accession}/         # from upstream pipeline-v2.sh
    └── bsseq/{accession}/           # from local pipeline-v2-bs-plant.sh
```

## 4. Reference genome preparation

`scripts/prepare-marchantia.sh` is idempotent — every step is skipped when its output already exists.

1. Download `MpTak_v7.1.fa` and `MpTak_v7.1.gff3` from MarpolBase. The script first fetches the directory listing and fails loudly if expected filenames are missing (avoids silent guessing). The exact filenames are pinned on first successful run.
2. `docker run ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0 samtools faidx MpTak_v7.1.fa`
   → `MpTak_v7.1.fa.fai`
   → `chrom.sizes = cut -f1,2 *.fai`
3. `docker run ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0 bwa-mem2 index MpTak_v7.1.fa`
4. `docker run ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0 dnmtools abismalidx MpTak_v7.1.fa MpTak_v7.1.abismal.idx`
5. Compute effective genome size for MACS3 as non-N bases in the FASTA; write to `macs_gsize.txt`. Read by `run-sample.sh` and passed as `--genome-size <int>`.

All indexes combined fit under ~5 GB.

## 5. Pipeline execution

### 5.1 `run-all.sh` (top-level driver)

- Iterates `zenigoke_sra_experiments.csv`, calls `run-sample.sh` per row.
- Failures never stop the loop.
- Flags: `--resume` (skip accessions with `.ok`), `--only <accession>`, `--library-strategy <ChIP-Seq|ATAC-Seq|Bisulfite-Seq>`.
- Default: one sample at a time, serial. BS-seq uses all 32 cores; parallelism at the sample level is not worth the NVMe contention.

### 5.2 `run-sample.sh` (dispatcher)

Given `(accession, library_strategy)`:

1. Download FASTQ via the upstream `fast-download.sh` → `/data1/zenigoke/fastq/{accession}/`. Auto-detects SE vs PE from the ENA filereport response.
2. Dispatch by library strategy:
   - `ChIP-Seq`, `ATAC-Seq` → upstream `pipeline-v2.sh`, `--genome-size $(cat macs_gsize.txt)`, output under `/data1/zenigoke/output/{chipseq|atacseq}/{accession}/`.
   - `Bisulfite-Seq` → local `pipeline-v2-bs-plant.sh`, output under `/data1/zenigoke/output/bsseq/{accession}/`.
3. On success: delete raw FASTQ, write `.ok`. On failure: keep FASTQ, write `.failed` with exit code and last 50 lines of the per-sample log.
4. Always: `trap EXIT` removes scratch under `/data1/zenigoke/tmp/{accession}/` so a crash never leaks scratch.

No retries in Phase 1. Failed samples get investigated by hand and re-run with `run-all.sh --resume` after fixes.

### 5.3 `pipeline-v2-bs-plant.sh` (local BS-seq fork)

Verbatim copy of upstream `pipeline-v2-bs.sh` with two functional changes:

1. `dnmtools counts` invoked **without** `-cpg-only`, producing a `counts.tsv` that covers CpG / CHG / CHH (column 4 = context).
2. Step 3 branches by context via `awk` filters and produces:

   | Context | BigWig pair | HMM regions |
   |--|--|--|
   | CpG | `{id}.CpG.methyl.bw`, `{id}.CpG.cover.bw` | `{id}.CpG.hmr.bed`, `{id}.CpG.hypermr.bed`, `{id}.CpG.pmd.bed` |
   | CHG | `{id}.CHG.methyl.bw`, `{id}.CHG.cover.bw` | `{id}.CHG.hmr.bed`, `{id}.CHG.hypermr.bed` |
   | CHH | `{id}.CHH.methyl.bw`, `{id}.CHH.cover.bw` | (none — low coverage makes HMM calls unreliable) |

   CpG runs through `dnmtools sym` first (symmetric strand pairs). CHG/CHH feed per-strand counts directly to `hypermr`. All HMM calls fan out in parallel (same pattern as upstream).

3. Stats TSV extended (local to this fork — not a drop-in replacement for the upstream 11-column format): the upstream columns, plus per-context mean methylation for CpG / CHG / CHH. The summary report reads the extended TSV directly; no re-scan of `counts.tsv` is required.

Runtime overhead vs upstream: ~30% (not 3×), because the HMM calls already run in parallel and share the same decompressed counts.

## 6. Metadata curation (bsllmner-mk2)

Runs independently of the pipeline; merges with pipeline outputs at report-build time.

### 6.1 Fetch BioSamples

`curate-metadata.sh` step 1:

- For each accession, query ENA filereport to get the BioSample accession.
- Fetch the BioSample JSON from NCBI (or ENA's equivalent endpoint).
- Save to `metadata/biosamples/{accession}.json`.

### 6.2 Run bsllmner-mk2 Select mode

Stand up `dbcls/bsllmner-mk2` via its own `docker compose up -d --build` in a sibling directory. Run **Select mode** with:

- `scripts/select-config-plants.json` (from the merged upstream PR #3) — PO mapping for `tissue`, `cell_type`.
- Local `configs/select-config-zenigoke.json` — extract-only fields (no ontology):
  - `developmental_stage` — e.g. thallus, gemma, gemmaling, antheridiophore, archegoniophore, sporophyte.
  - `genotype_strain` — e.g. Tak-1, Tak-2, Cam-1, Cam-2, mutant identifiers.
  - `treatment` — hormone / light / stress / mock / other.
  - `antibody_target` — ChIP-Seq samples only; e.g. H3K4me3, H3K27me3, MpTF* proteins.

Model: `qwen3.5:27b` via Ollama. Fails loudly if the tag is not pullable.

### 6.3 Output

One `metadata/curated/{accession}.json` per accession, merging outputs of both configs. Missing fields are retained as `null`. Per-sample curation errors are logged to `metadata/curation.log` but do not halt the run.

### 6.4 Decoupling

Metadata curation does not depend on pipeline success and can run before, during, or after `run-all.sh`. The report generator joins on accession at build time and tolerates missing fields in either stream.

## 7. Summary report

`build-report.py` produces a single self-contained `report/phase1-summary.html`:

- Overview card: totals per strategy, percent succeeded, cumulative wall-clock, disk usage.
- Failure table: one row per `.failed` sample (accession, strategy, exit code, last-log snippet).
- Per-strategy success tables:
  - **ChIP / ATAC:** FASTQ size, reads before/after filter, mapping rate, duplication rate, peak counts at q=1e-5/1e-10/1e-20, antibody target, elapsed minutes. Sourced from the 15-column `{id}.stats.tsv` the upstream pipeline already emits.
  - **BS-seq:** FASTQ size, reads, mapping rate, mean methylation per context (CpG/CHG/CHH), HMR/PMD counts (CpG), elapsed minutes. Sourced from the extended stats TSV emitted by `pipeline-v2-bs-plant.sh` (§5.3).
- Curation columns on every row: resolved PO term for tissue/cell_type, extracted stage/strain/treatment, and antibody target for ChIP-seq.
- Inline SVG histograms: mapping-rate distribution, duplication-rate, peaks-at-q1e-10, CpG methylation %.

Implementation: Python 3 stdlib only. No pandas / jinja / matplotlib. Keeps the generator dependency-free and runnable anywhere.

## 8. Data volume and runtime

Marchantia genome ~220 Mbp. Estimates:

| Item | Per sample | × 157 |
|--|--|--|
| FASTQ (typical 10–30M reads) | 2–6 GB | transient; ~6 GB peak |
| Indexes (one-time) | — | ~5 GB |
| ChIP / ATAC outputs | 50–150 MB | ~15 GB |
| BS-seq outputs (3-context) | 300–800 MB | ~15 GB |
| Metadata + report | negligible | < 50 MB |
| **Persistent on `/data1`** | | **~35 GB** |

Runtime extrapolating from ce11 production-run numbers:

- ChIP / ATAC: ~2–8 min per sample, median ~4 min → **8–12 hours** sequential for 126 samples.
- BS-seq: ~5 min per sample + 30% for three-context → **~4 hours** for 31 samples.
- bsllmner-mk2 on 157 BioSamples with `qwen3.5:27b`: ~1 hour on a GPU, otherwise overnight.

End-to-end: roughly a day, restartable via `--resume`.

## 9. Testing strategy

No bash unit tests — scripts are thin dispatchers. Three named validation commands:

1. **`run-all.sh --only SRX7548553`** — one ChIP-seq sample end-to-end. Validates dispatch, FASTQ download, bwa-mem2 against MpTak, MACS3 with the computed `genome-size`, expected output layout. Target: finishes in < 15 min.
2. **One sample per strategy** — ChIP + ATAC + BS-seq, three runs. The BS-seq run is what catches regressions in the plant fork (non-empty CHG/CHH outputs, `sym`/`hmr` only for CpG).
3. **`build-report.py` on the 3-sample output** — confirms the report generator handles partial/missing data without crashing. This is the cheap test that will break most often during iteration.

Correctness signals: pipeline outputs present and non-empty, curated JSON parseable, report builds cleanly. The `.failed` files and report surface anything else.

## 10. Out of scope (explicit)

- Database schema (SQL or otherwise).
- Any query API, REST endpoint, or web UI.
- Visualization beyond inline SVG in the summary report.
- Browser-track integration with MarpolBase / JBrowse.
- Changes to `inutano/chip-atlas-pipeline-v2` upstream.
- Retries / exponential backoff / automated re-submission.
- Multi-node / SLURM orchestration.

These are all candidates for Phase 2 but do not block Phase 1.

## 11. Open items to resolve at first run

1. **Exact MarpolBase download URLs.** The `/data/` path serves a JBrowse SPA for most subpaths; the script fetches the directory listing and fails loudly if expected FASTA/GFF3 filenames aren't present. Pinned on first successful run, not guessed now.
2. **MACS3 effective genome size.** Computed from the FASTA at prep time (non-N bases). Expected ~2.1–2.2e8 for MpTak v7.1.
3. **Ollama `qwen3.5:27b` availability.** Pull-test before committing to the full curation run.
