# Phase 1 — final summary

Phase 1 of the kknmsmd / zenigoke project is complete.

## Final tally

```
ChIP-Seq         89 / 90     (1 fail — SRX29617452, malformed source data)
ATAC-Seq         36 / 36     (100%)
Bisulfite-Seq    31 / 31     (100%)
─────────────────────────────────
total           156 / 157     99.4%
```

## What's on disk

| | path | size |
|---|---|---|
| Pipeline outputs | `/data1/zenigoke/output/` | 37 GB |
| MpTak v7.1 reference + indexes | `/data1/zenigoke/references/MpTak_v7.1/` | 2.3 GB |
| Per-sample status markers | `/data1/zenigoke/status/*.{ok,failed}` | tiny |
| Per-sample logs | `/data1/zenigoke/logs/*.log` | small |
| Curated metadata (157 samples) | `~/work/zenigoke/metadata/curated/` | ~5 MB |
| BioSample dumps | `~/work/zenigoke/metadata/biosamples/` | ~2 MB |
| Phase 1 HTML report | `~/work/zenigoke/report/phase1-summary.html` | 30 KB |

`/data1` free: 673 GB.

## Curation coverage (qwen3.5:27b on 157 BioSamples, 6 min)

| Field | Coverage |
|---|---|
| `genotype_strain` | 100% (Tak-1 dominant; some Tak-2, Cam-*) |
| `developmental_stage` | 92% |
| `tissue` | 56% (mostly thallus; some gemmaling, archegoniophore) |
| `treatment` | 19% |
| `antibody_target` | 0% (BioSample text doesn't carry antibody — see limitation 1) |

## Known limitations

1. **`antibody_target` not extractable from BioSample.** Antibody info lives in
   the SRA Experiment record, not BioSample. To fill this in Phase 2, also
   fetch SRX `experiment_xml` and run a separate extract.
2. **CHG `hmr` consistently missing for BS-seq.** dnmtools `hmr` is
   designed for CpG and fails on CHG without symmetrization. Pipeline
   handles gracefully (logs warning, continues); spec table needs updating.
3. **bsllmner-mk2 ontology subset OWLs not built.** Falls through to
   LLM-only extraction. To get formal PO term IDs in Phase 2, run
   `~/repos/bsllmner-mk2/scripts/build_subset_ontologies.sh`.
4. **`SRX29617452` malformed source data.** 39M reads, all 50bp, names
   ending in `/1` (BBMap-style), but bwa-mem2 produces SAM that samtools
   rejects with "SEQ and QUAL of different length". Two retries hit the
   same wall. Documented as data integrity issue from submitter.

## Bugs found and fixed during execution

Across the full run, 9 fixes ended up on `feature/phase1-impl`. The
notable ones:

- `e6276a8` — wrap pipeline invocations in `docker run` (host bash crashed
  immediately because tools live only in containers).
- `58783e6` — dnmtools 1.5.1 emits `CXG` (not `CHG`) for the CHG context.
- `95b849f` — ENA filereport TSV puts sample_accession in col 2, not col 1.
- `f5dd3af` — `_chipseq_stats_fields` was missing the `reads_mapped` column,
  shifting every downstream key by one in the report.
- `ae4c77c` / `f0c1f0b` — `retry-pe-fallback.sh` for the ENA
  "PAIRED-but-single-file" edge case (12 ChIP-Seq samples were affected).

Full list: `git log --oneline main..feature/phase1-impl`.

## Branch state — ready to merge

19 commits ahead of `main` on `feature/phase1-impl`. When you're ready:

```bash
cd ~/work/zenigoke
git checkout main
git merge --no-ff feature/phase1-impl -m "Phase 1: pipelines + curation + report on Marchantia"
git tag -a phase1-complete -m "Phase 1: 156/157 samples processed"
```

## Phase 2 starting points

When you want to talk Phase 2:

1. **DB schema** — what queries does the catalog need to answer?
   (Examples: by antibody/stage/strain crosses, by methylation region
   overlap, by genome interval.) Answer drives schema choice.
2. **antibody_target enrichment** — pull SRX experiment XML and run a
   second extract. Largest single coverage win on the curation side.
3. **Retry SRX29617452 with seqkit sanitize OR fasterq-dump** if you
   want 157/157 — about an hour of work, but data is genuinely broken
   at the source.
4. **Web UI / JBrowse track integration** — the BigWigs and BedRead
   files are JBrowse-ready as-is; adding a public/internal browser is
   straightforward.
5. **Container that runs the whole flow** for reproducibility and
   future plant species.

Open `report/phase1-summary.html` in a browser first — eyeballing the
real numbers will surface what Phase 2 should prioritize.
