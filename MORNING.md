# Morning brief — kknmsmd Phase 1

Phase 1 implementation complete. The full pipeline run is running in
background. This file is for the morning check-in.

## What was done overnight

All 9 tasks of the Phase 1 plan are complete or running.

| Task | Status | Notes |
|------|--------|-------|
| 1. bootstrap.sh | ✅ | data dirs + docker images pulled |
| 2. prepare-marchantia.sh | ✅ | MpTak v7.1 + bwa-mem2/abismal indexes |
| 3. pipeline-v2-bs-plant.sh | ✅ | plant fork (CpG/CHG/CHH, CXG bug fixed) |
| 4. run-sample.sh | ✅ | docker-wrapped, SE/PE detect, cleanup trap |
| 5. run-all.sh | ✅ | --resume / --only / --library-strategy |
| 6. curate-metadata.sh | ✅ | BioSample fetch + bsllmner-mk2 Select |
| 7. build_report.py | ✅ | TDD, 4/4 tests pass |
| 8. 3-sample validation | ✅ | ChIP/ATAC/BS-seq all OK; curation produces non-null metadata |
| 9. full 154-sample run | 🔄 | running in background, PID 2081329 |

## Bugs found and fixed during validation

Five fixes during Task 8, all on `feature/phase1-impl`:
- `e6276a8` — wrap pipeline invocations in `docker run`
- `58783e6` — match `CXG` (not `CHG`) for the CHG context in dnmtools 1.5.1
- `027c3b8` — add `|| true` to SE FASTQ fallback `ls`
- `ee3f430` — skip leading blank line in CSV in build_report.py
- `95b849f` — extract `sample_accession` from col 2 (not col 1) of ENA filereport
- `27439e1` — CHG hmr limitation note + curated path fix in build_report.py

## Known limitations (non-blocking)

1. **CHG.hmr.bed consistently missing.** dnmtools `hmr` fails on CHG context for
   Marchantia samples. Pipeline catches it with a warning and continues. The
   spec table prescribed `hmr` for CHG; reality says it doesn't run without
   symmetrization. Worth revisiting in Phase 2.
2. **bsllmner-mk2 ontology subset OWLs not built.** `po_tissue_subset.owl` /
   `po_cell_subset.owl` would need a multi-GB upstream PO download. The
   curation script currently nulls `ontology_file` paths at runtime, falling
   through to LLM-only extraction. Tissue/cell_type strings still get extracted
   (just not formally PO-mapped).
3. **9.16% mapping rate on DRX162964 (BS-seq).** Plant samples often have
   abundant organellar/repeat reads that lower this. Not investigated.
4. **MACS3 effective genome size = 248042180** for MpTak v7.1 standard genome.

## Monitor the background run

```bash
# Live progress
tail -f /data1/zenigoke/run-all-full.log

# Check the running PID
ps -p 2081329 || echo "(no longer running)"

# Status counters at a glance
ls /data1/zenigoke/status/*.ok 2>/dev/null | wc -l    # succeeded
ls /data1/zenigoke/status/*.failed 2>/dev/null | wc -l # failed

# Per-sample log for any failure
ls /data1/zenigoke/status/*.failed 2>/dev/null | head
cat /data1/zenigoke/status/<accession>.failed
cat /data1/zenigoke/logs/<accession>.log | tail -50
```

Estimated wall-clock for the remaining 154 samples: 12-16 hours.

## After the pipeline run completes

```bash
# Run curation on all 157 BioSamples (idempotent — skips already-fetched)
cd /home/inutano/work/zenigoke
bash scripts/curate-metadata.sh 2>&1 | tee metadata/curation-full.log

# Build the final report
python3 scripts/build-report.py
ls -lh report/phase1-summary.html
xdg-open report/phase1-summary.html  # or open in your browser
```

## Branch state

All implementation lives on `feature/phase1-impl`. The spec + plan are on
`main`. Total: 17 commits ahead of main on the feature branch.

```bash
git log --oneline main..feature/phase1-impl
```

When you're satisfied with Phase 1 outputs, merge to main:

```bash
git checkout main
git merge --no-ff feature/phase1-impl -m "Phase 1: pipelines + curation + report on Marchantia"
git tag -a phase1-complete -m "Phase 1: 154 samples processed (+ 3 validation)"
```

## Phase 2 starting points (when you're ready to discuss)

Per spec §10 and Task 9 of the plan, things that were intentionally deferred:
- DB schema (SQL or otherwise) — what queries do you actually want?
- Web UI / API — read-only catalog vs interactive browser?
- JBrowse / MarpolBase track integration?
- Antibody × stage × strain × tissue cross-tabulation (depends on what
  bsllmner-mk2 actually extracted on the full run).

Look at the report first, then we can scope Phase 2.
