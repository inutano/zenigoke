# configs/

## organelle_like_regions.MpTak_v7.1.bed

1,278 regions (311,657 bp, 0.13% of the nuclear genome) of the MpTak_v7.1
nuclear chromosomes where reads from the chloroplast and mitochondrion map
when the organelle genomes are not in the reference: nuclear copies of
organelle DNA (NUPT/NUMT) and similar sequence. BED3, 0-based, sorted.

Built in the 2026-10-01 QC investigation by
`/data1/zenigoke/tmp/qc-20261001-chip/scripts/organelle_regions.sh`:

1. Organelle sequences: Tak-1 chloroplast AP025455.1 (`chrC`, 120,306 bp) and
   mitochondrion AP025456.1 (`chrM`, 186,197 bp).
2. Tiles: 150 bp every 10 bp along each genome. The genomes are circular, so
   the first 150 bp are appended and tiles also span the origin.
3. Mapping: `bwa-mem2 mem -t 4` with default parameters against the nuclear
   reference `/data1/zenigoke/references/MpTak_v7.1/MpTak_v7.1.fa`
   (chr1–8, chrU, chrV), in `ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0`.
   Primary alignments only (`samtools view -F 0x904`: unmapped, secondary and
   supplementary removed); no MAPQ filter.
4. Alignments with an aligned reference length (CIGAR `M`/`D`/`N`/`=`/`X`)
   of at least 100 bp are kept.
5. Alignments are sorted and merged when they lie on the same chromosome
   within 50 bp of each other.

The script writes `nupt/organelle_like_regions.bed` with two extra columns
(origin `chrC`/`chrM`/`chrC+chrM`, number of tiles). This file is
`cut -f1-3 | sort -k1,1 -k2,2n` of it.

Used by:

- `scripts/run-sample.sh` passes it as `--blacklist` to
  `scripts/pipeline-v2-plant.sh`, which drops q < 1e-5 peaks overlapping it
  (`bedtools intersect -v`) before writing the q < 1e-10 / 1e-20 subsets and
  the bigBeds.
- `scripts/api_enrichment.py` (default `ZENIGOKE_BLACKLIST`) removes user BED
  regions and sample peaks overlapping it and subtracts its length from the
  genome size used for the null probability.
- `deploy/aws/02-sync-data.sh` uploads it to
  `s3://<bucket>/references/MpTak_v7.1/`.

The `peaks_in_organelle_like_pct` values in
`docs/qc/2026-10-01-chip-atlas-qc.tsv` counted overlaps with these regions
padded by 500 bp on each side (1.45 Mb); the blacklist itself is not padded.

## curation_overrides.tsv

Manual corrections to the curated metadata (bsllmner-mk2 output in
`metadata/curated/`). Lines starting with `#` are comments, then a header and
one row per corrected field:

| column | meaning |
|---|---|
| `accession` | SRA experiment accession |
| `field` | one of `tissue`, `cell_type`, `developmental_stage`, `genotype_strain`, `treatment`, `antibody_target` (an unknown field stops the build) |
| `value` | value to use instead of the extracted one |
| `reason` | evidence, usually the SRA experiment title |

`scripts/build_catalog_db.py` applies the rows after reading the curated JSON
(`--curation-overrides`, default `<repo>/configs/curation_overrides.tsv`).
The current 12 rows fix `antibody_target` where the extraction contradicts
the experiment title (genotype read as target, H3 controls, a replicate
mismatch); source: `docs/qc/2026-10-01-chip-atlas-mapping-qc.md` §5.3, plus
the synonym H2AK119ub → H2Aub for SRX9800295.

## select-config-*.json

Field definitions for bsllmner-mk2 (`scripts/curate-metadata.sh`,
`scripts/curate-antibody.sh`, `scripts/curate-antibody-gap.sh`).
