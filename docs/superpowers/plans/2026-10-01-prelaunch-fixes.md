# Pre-launch Fixes (Report §4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry out every recommendation in §4 of the 2026-10-01 pre-launch review: fix the catalog's QC reporting, curation and IGV hand-off now, and re-analyse all 157 experiments against a reference that includes the chloroplast and mitochondrion.

**Architecture:** Two tracks. Track A (Tasks 1–5) builds an organelle-augmented reference, adds a local ChIP/ATAC fork of the upstream pipeline (same pattern as the existing BS fork) and patches the BS fork, then re-runs everything into `/data1/zenigoke/output-v2` in the background (~2–3 days). Track B (Tasks 6–11) fixes the catalog build while the re-run is in progress: stats parsing for both the legacy and the new stats format, a `sample_qc` table with PASS/WARN/FAIL flags, curation overrides, page changes, a shared IGV module, and enrichment blacklisting. Task 12 swaps in the re-run outputs and rebuilds; Task 13 deploys only after the owner confirms.

**Tech Stack:** bash + docker (`ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0`, `ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0`), Python 3.8 stdlib + sqlite3, pytest, vanilla JS (node 20 for unit tests), bedtools.

**Spec:** `docs/2026-10-01-prelaunch-review.html` §4 (推奨する対応), backed by `docs/qc/2026-10-01-chip-atlas-mapping-qc.md` §6, `docs/qc/2026-10-01-bsseq-mapping-qc.md` §5–6 and `docs/proposals/2026-10-01-ui/proposal.md` §1.3 / §6.

## Global Constraints

- Never edit `~/repos/chip-atlas-pipeline-v2` (upstream does not accept additions). Plant logic lives in this repo as wrappers or local forks.
- Python must run on the host's Python 3.8: every new module starts with `from __future__ import annotations`; no `match`, no `str.removeprefix`.
- Organelle accessions: chloroplast `AP025455.1` → `chrC`, mitochondrion `AP025456.1` → `chrM` (both Tak-1). Not MK202951.1.
- Nuclear chromosome set = the 10 lines of `/data1/zenigoke/references/MpTak_v7.1/chrom.sizes`. Every bigWig/bigBed/peak/methylation output stays nuclear-only.
- Re-run writes only to `/data1/zenigoke/{output-v2,status-v2,logs-v2}`. Do not modify or delete `/data1/zenigoke/output` until Task 12.
- Re-run uses `THREADS=16` per job, at most 2 concurrent jobs (32 cores, 93 GB RAM).
- QC thresholds (verbatim from the report):
  - ChIP/ATAC: aligned ≥70 PASS / 30–70 WARN / <30 FAIL; dup <50 PASS / 50–80 WARN / ≥80 FAIL; usable fragments ≥4M PASS / 1M–4M WARN / <1M FAIL; foreign (human + bacterial-like) ≥10% WARN; organelle ≥20% WARN; pipeline failure FAIL.
  - ChIP/ATAC peak_flag: WARN when ≥20% of q05 peaks are in organelle-like regions and q05 ≥ 50 peaks; NA when no peaks; else OK.
  - BS: CpG, CHG and CHH means all ≥0.9 → FAIL; non-conversion ≥5% FAIL, >2% WARN; non-Marchantia reads ≥50% WARN; CpG ≥5× fraction <5% WARN.
- IGV genome until the owner decides on MarpolBase redistribution: `https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/hub.txt`.
- Peak threshold wording everywhere: "q < 1e-5 / 1e-10 / 1e-20" (MACS3 `-q 1e-05`, files `.05/.10/.20` = −log10 q ≥ 5/10/20).
- Work on branch `prelaunch-fixes`. Commit per task only if the owner approved commits during plan review; otherwise leave changes staged and list them at the end.
- No S3 upload, `git push`, or EC2 change before the owner confirms Task 13.

## Review Focus

1. A ChIP/ATAC `stats.tsv` in the **legacy 15-column format** (current outputs) next to one in the **new 21-column `zenigoke-v2` format** in the same build: both must parse, and the legacy one must not show its "usable %" as an alignment rate. → Task 6 test `test_parse_chipseq_stats_legacy_vs_v2`.
2. A sample with **no QC TSV row** (e.g. a future accession) must still get a flag computed from whatever metrics exist, not crash or silently PASS when metrics are missing entirely. → Task 7 test `test_flag_with_no_metrics_is_na`.
3. **IGV not running / Safari** — the send button must report failure and not claim "Sent". → Task 10 test `send result maps network error to "unreachable"`.
4. **Track names containing commas** (e.g. genotype "Mpez1,Mpknox2 double mutant") would break IGV's comma-separated `/load` lists. → Task 10 test `names never contain commas`.
5. **Single-file "PAIRED" ENA runs** (SRX29617444–455) must download and run as SE in the re-run without manual retry. → Task 4 smoke run on SRX29617444.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `scripts/prepare-marchantia.sh` | modify | add step 7: organelle-augmented reference dir `MpTak_v7.1_org/` |
| `configs/organelle_like_regions.MpTak_v7.1.bed` | create | 1,278 NUPT/NUMT-like regions (blacklist), copied from QC scratch |
| `scripts/pipeline-v2-plant.sh` | create | local fork of upstream `pipeline-v2.sh@8345566` (the version Phase 1 ran) |
| `scripts/pipeline-v2-bs-plant.sh` | modify | `-R`, nuclear-only counts, non-conversion, CpG count, extra stats columns |
| `scripts/run-sample.sh` | modify | org reference, fork, configurable output/status/log roots, single-file PAIRED download |
| `scripts/run-all.sh` | modify | honour `STATUS_DIR` |
| `scripts/build_report.py` | modify | stats field maps for legacy + v2 formats |
| `scripts/build_catalog_db.py` | modify | schema (`reads_raw`, `usable_rate`, `sample_qc`), parsers, QC import, curation overrides |
| `scripts/qc_flags.py` | create | pure functions: metrics → (flag, reasons, peak_flag) |
| `configs/curation_overrides.tsv` | create | manual curation fixes (accession, field, value, reason) |
| `scripts/build_catalog_pages.py` | modify | QC column/pill/card, labels, Methods + Known Limitations text, script includes, CSS |
| `report/assets/igv.js` | create | shared track building + IGV `/load` URL + send (browser + node-testable) |
| `report/assets/matrix.js`, `report/assets/enrichment.js` | modify | use `igv.js` |
| `scripts/igv_url_helper.py` | modify | peaks as bigBed |
| `scripts/api_enrichment.py` | modify | blacklist filtering of user BED and peak files |
| `deploy/aws/02-sync-data.sh` | modify | drop `--size-only`; `--dryrun` / `--delete`; also upload `configs/organelle_like_regions…bed` |
| `deploy/aws/04-ec2-bootstrap.sh` | modify | peaks `--delete`, DB refresh, enrichment cache dir, restart |
| `scripts/cpg_coverage.py` | create | CpG ≥5× coverage of v2 BS outputs (`docs/qc/bsseq-v2-cpgcov.tsv`) |
| `tests/test_qc_flags.py`, `tests/test_igv_js.py`, `tests/js/igv.test.js` | create | new tests |
| `tests/test_build_catalog_db.py`, `tests/test_build_report.py`, `tests/test_build_catalog_pages.py`, `tests/test_api_enrichment.py` | modify | updated/added tests |

---

## Track A — Re-analysis

### Task 1: Organelle-augmented reference + blacklist file

**Files:**
- Modify: `scripts/prepare-marchantia.sh` (append step 7 before `== done ==`)
- Create: `configs/organelle_like_regions.MpTak_v7.1.bed`

**Interfaces:**
- Produces: `/data1/zenigoke/references/MpTak_v7.1_org/` containing `MpTak_v7.1_org.fa` (+`.fai`, bwa-mem2 index files), `MpTak_v7.1_org.abismal.idx`, `chrom.sizes` (nuclear, 10 lines), `chrom.sizes.all` (12 lines), `macs_gsize.txt` (copied).

- [ ] **Step 1: Create branch and copy the blacklist**

```bash
cd ~/work/zenigoke && git checkout -b prelaunch-fixes
mkdir -p configs
cut -f1-3 /data1/zenigoke/tmp/qc-20261001-chip/nupt/organelle_like_regions.bed \
  | sort -k1,1 -k2,2n > configs/organelle_like_regions.MpTak_v7.1.bed
wc -l configs/organelle_like_regions.MpTak_v7.1.bed   # expect 1278
```

- [ ] **Step 2: Append step 7 to `prepare-marchantia.sh`** (insert before the `echo "== done =="` line)

```bash
echo "== step 7: organelle-augmented reference (nuclear + chrC + chrM) =="
# MpTak_v7.1 deliberately omits the organelle genomes. Without them, organelle
# reads land on nuclear NUPT/NUMT copies (spurious peaks, false low methylation).
# Outputs stay nuclear-only: chrom.sizes here is the nuclear list.
ORG_DIR="$DATA_ROOT/references/MpTak_v7.1_org"
mkdir -p "$ORG_DIR"
fetch_org() {  # $1 = NCBI accession, $2 = sequence name to use
  local out="$ORG_DIR/$2.fa"
  [ -s "$out" ] && return 0
  curl -fsSL "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=$1&rettype=fasta&retmode=text" \
    | sed "1s/.*/>$2 $1/" > "$out.tmp"
  mv "$out.tmp" "$out"
}
fetch_org AP025455.1 chrC   # Tak-1 chloroplast, 120,306 bp
fetch_org AP025456.1 chrM   # Tak-1 mitochondrion, 186,197 bp
if [ ! -f "$ORG_DIR/MpTak_v7.1_org.fa" ]; then
  cat MpTak_v7.1.fa "$ORG_DIR/chrC.fa" "$ORG_DIR/chrM.fa" | awk 'NF' > "$ORG_DIR/MpTak_v7.1_org.fa"
fi
ORG_DOCKER="docker run --rm -u $(id -u):$(id -g) -v $ORG_DIR:/ref -w /ref"
[ -f "$ORG_DIR/MpTak_v7.1_org.fa.fai" ] || $ORG_DOCKER "$CHIP_IMG" samtools faidx MpTak_v7.1_org.fa
cut -f1,2 "$ORG_DIR/MpTak_v7.1_org.fa.fai" > "$ORG_DIR/chrom.sizes.all"
cp chrom.sizes "$ORG_DIR/chrom.sizes"
cp macs_gsize.txt "$ORG_DIR/macs_gsize.txt"
[ -f "$ORG_DIR/MpTak_v7.1_org.fa.bwt.2bit.64" ] || $ORG_DOCKER "$CHIP_IMG" bwa-mem2 index MpTak_v7.1_org.fa
[ -f "$ORG_DIR/MpTak_v7.1_org.abismal.idx" ] || \
  $ORG_DOCKER "$BS_IMG" dnmtools abismalidx MpTak_v7.1_org.fa MpTak_v7.1_org.abismal.idx
echo "   org reference: $(wc -l < "$ORG_DIR/chrom.sizes.all") sequences ($(wc -l < "$ORG_DIR/chrom.sizes") nuclear)"
```

- [ ] **Step 3: Run it and verify**

Run: `bash scripts/prepare-marchantia.sh 2>&1 | tail -8` (steps 1–6 skip; step 7 takes ~15 min for bwa-mem2 index)

Verify:
```bash
R=/data1/zenigoke/references/MpTak_v7.1_org
cut -f1,2 $R/chrom.sizes.all | tail -2      # chrC 120306 / chrM 186197
wc -l < $R/chrom.sizes                      # 10
ls $R/MpTak_v7.1_org.fa.bwt.2bit.64 $R/MpTak_v7.1_org.abismal.idx
bash scripts/prepare-marchantia.sh | grep -c "step 7"   # re-run is a no-op apart from the echo
```

- [ ] **Step 4: Commit** — `git add scripts/prepare-marchantia.sh configs/organelle_like_regions.MpTak_v7.1.bed && git commit -m "ref: organelle-augmented MpTak_v7.1 reference + NUPT/NUMT blacklist"`

### Task 2: ChIP/ATAC local fork `pipeline-v2-plant.sh`

**Files:**
- Create: `scripts/pipeline-v2-plant.sh`

**Interfaces:**
- Consumes: Task 1 reference dir; `configs/organelle_like_regions.MpTak_v7.1.bed`.
- Produces: same CLI as upstream plus optional `--blacklist BED`. Output files identical in name to upstream. `stats.tsv` has 21 columns: cols 1–15 as upstream **except** col 7 = alignment rate (primary mapped before dedup ÷ fastp-passed reads × 100) and col 8 = duplication rate (duplicate primary ÷ primary mapped × 100); col 16 = literal `zenigoke-v2`; 17 = primary mapped reads; 18 = duplicate primary reads; 19 = usable reads (post-dedup mapped, all sequences); 20 = usable reads on chrC/chrM; 21 = usable nuclear reads (RPM denominator).

- [ ] **Step 1: Copy the Phase-1 upstream version**

```bash
git -C ~/repos/chip-atlas-pipeline-v2 show 8345566:scripts/pipeline-v2.sh > scripts/pipeline-v2-plant.sh
```

- [ ] **Step 2: Replace the header comment** (lines 1–2 `#!/bin/bash` + `#` stay) with a fork notice directly after `#!/bin/bash`:

```bash
#
# Zenigoke plant-aware ChIP/ATAC pipeline
#
# FORK of: chip-atlas-pipeline-v2/scripts/pipeline-v2.sh @ 8345566 (the version
# Phase 1 ran on 2026-04-28). Fork date: 2026-10-01. Container unchanged
# (ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0).
#
# Changes from upstream:
#   1. Alignment rate and duplication rate come from `samtools markdup -f`
#      (stats.tsv cols 7/8). Upstream col 7 was usable% and col 8 was 100-usable%.
#   2. The genome FASTA may contain extra sequences (chrC, chrM). Only the
#      sequences listed in --chrom-sizes are used for BigWig, MACS3 and the RPM
#      denominator; the rest are counted and reported (col 20).
#   3. Optional --blacklist BED: peaks overlapping it are dropped before the
#      q-value split (NUPT/NUMT-like regions).
#   4. stats.tsv gains cols 16-21 (see Step 5).
```

- [ ] **Step 3: Add the `--blacklist` option** — in the `case` block add `--blacklist)    BLACKLIST="$2"; shift 2 ;;` next to `--threads`.

- [ ] **Step 4: markdup stats** — in both Step-1 branches change the last pipe stage to:

```bash
    | samtools markdup -r -f "$WORK/markdup.stats" - "$DEDUP_BAM"
```

- [ ] **Step 5: Nuclear-only BAM** — replace the block from `MAPPED=$(samtools view -c -F 4 "$DEDUP_BAM")` through its `log` line with:

```bash
# Count usable reads, then keep only nuclear sequences (those in CHROM_SIZES)
# for BigWig, MACS3 and RPM. Extra FASTA sequences (chrC/chrM) soak up organelle
# reads so they no longer pile onto nuclear NUPT/NUMT copies.
samtools index "$DEDUP_BAM"
MAPPED_ALL=$(samtools view -c -F 4 "$DEDUP_BAM")
NUC_BAM="$WORK/${SAMPLE_ID}.nuclear.bam"
samtools view -b -o "$NUC_BAM" "$DEDUP_BAM" $(cut -f1 "$CHROM_SIZES" | tr '\n' ' ')
MAPPED=$(samtools view -c -F 4 "$NUC_BAM")
ORG_USABLE=$((MAPPED_ALL - MAPPED))
log "  Usable reads (post-dedup): all=$MAPPED_ALL nuclear=$MAPPED other=$ORG_USABLE"
```

and in Step 2 replace both remaining uses of `"$DEDUP_BAM"` (the `bedtools genomecov -ibam` and `macs3 callpeak -t`) with `"$NUC_BAM"`.

- [ ] **Step 6: Blacklist filter** — in Step 3, right after `PEAKS_05="$OUTDIR/${SAMPLE_ID}.05_peaks.narrowPeak"` insert:

```bash
  if [ -n "$BLACKLIST" ] && [ -s "$BLACKLIST" ]; then
    N_BEFORE=$(wc -l < "$PEAKS_05")
    bedtools intersect -v -a "$PEAKS_05" -b "$BLACKLIST" > "$WORK/.peaks05.filtered"
    mv "$WORK/.peaks05.filtered" "$PEAKS_05"
    log "  Blacklist: removed $((N_BEFORE - $(wc -l < "$PEAKS_05"))) of $N_BEFORE q05 peaks"
  fi
```

- [ ] **Step 7: Stats** — replace the `MAP_RATE`/`DUP_RATE` block with:

```bash
# samtools markdup -f: EXAMINED = primary mapped reads before dedup,
# DUPLICATE PRIMARY TOTAL = primary reads marked duplicate.
ALIGNED=$(awk -F': ' '$1=="EXAMINED"{print $2}' "$WORK/markdup.stats")
DUPS=$(awk -F': ' '$1=="DUPLICATE PRIMARY TOTAL"{print $2}' "$WORK/markdup.stats")
ALIGNED=${ALIGNED:-0}; DUPS=${DUPS:-0}
MAP_RATE=0; DUP_RATE=0
if [ "${READS_AFTER:-0}" -gt 0 ] 2>/dev/null; then
  MAP_RATE=$(awk "BEGIN {printf \"%.1f\", $ALIGNED / $READS_AFTER * 100}")
fi
if [ "$ALIGNED" -gt 0 ] 2>/dev/null; then
  DUP_RATE=$(awk "BEGIN {printf \"%.1f\", $DUPS / $ALIGNED * 100}")
fi
```

and change the `printf` to 21 fields, appending after `"$TOTAL_MIN"`:

```bash
  "zenigoke-v2" "$ALIGNED" "$DUPS" "$MAPPED_ALL" "$ORG_USABLE" "$MAPPED" \
```

(format string gets six more `\t%s`). Update the trailing `log "Stats: …"` to include `aligned=$ALIGNED dup_rate=${DUP_RATE}%`.

- [ ] **Step 8: Static check** — `bash -n scripts/pipeline-v2-plant.sh && grep -c 'NUC_BAM' scripts/pipeline-v2-plant.sh` (expect ≥4). The real run happens in Task 4.

- [ ] **Step 9: Commit** — `git add scripts/pipeline-v2-plant.sh && git commit -m "pipeline: local ChIP/ATAC fork with true alignment/dup rates, nuclear-only outputs, blacklist"`

### Task 3: BS fork changes

**Files:**
- Modify: `scripts/pipeline-v2-bs-plant.sh`

**Interfaces:**
- Produces: `stats.tsv` with 19 columns: cols 1–15 unchanged; 16 = `zenigoke-v2`; 17 = percent_unique (pairs for PE, reads for SE); 18 = % of deduplicated reads on non-nuclear sequences; 19 = non-conversion rate (coverage-weighted chrC CHH methylation, fraction, or `NA`).

- [ ] **Step 1: Header** — add to the "Changes from upstream" list:

```
#   4. (2026-10-01) abismal -R (random/PBAT-tolerant; rescues R1/R2-swapped runs,
#      ≤0.3 pt change on directional data); counts restricted to --chrom-sizes
#      sequences, chrC CHH used as the non-conversion estimate; CPG_COUNTS for
#      MpTak_v7.1; stats cols 16-19.
```

- [ ] **Step 2: `-R`** — add `-R \` after `-t "$THREADS" \` in both `dnmtools abismal` calls.

- [ ] **Step 3: CpG count** — add `[MpTak_v7.1]=20705802   # 10,352,901 CG dinucleotides x 2 strands, nuclear` to `CPG_COUNTS`.

- [ ] **Step 4: Organelle share before the BAM is deleted** — in Step 2, replace the `dnmtools counts … > "$WORK/counts.tsv"` command with:

```bash
dnmtools counts \
    -t "$THREADS" \
    -c "$GENOME_FA" \
    "$DEDUP_BAM" \
  | awk -F'\t' -v OFS='\t' '$6 > 0' > "$WORK/counts.all.tsv"

# Non-conversion: coverage-weighted CHH methylation on the chloroplast
# (unmethylated in vivo). Then keep nuclear sequences only.
NONCONV=$(awk -F'\t' '$1=="chrC" && $4=="CHH" {m+=$5*$6; n+=$6}
  END {if (n>0) printf "%.4f", m/n; else print "NA"}' "$WORK/counts.all.tsv")
awk -F'\t' 'NR==FNR {keep[$1]=1; next} ($1 in keep)' "$CHROM_SIZES" "$WORK/counts.all.tsv" \
  > "$WORK/counts.tsv"
rm -f "$WORK/counts.all.tsv"
ORG_PCT=$(samtools view "$DEDUP_BAM" \
  | awk -F'\t' 'NR==FNR {keep[$1]=1; next} {n++; if (!($3 in keep)) o++}
      END {if (n>0) printf "%.2f", o/n*100; else print "0"}' "$CHROM_SIZES" -)
log "  non-conversion (chrC CHH)=$NONCONV  non-nuclear reads=${ORG_PCT}%"
```

- [ ] **Step 5: Stats columns** — after the `MAP_RATE=…` line add
`PCT_UNIQUE=$(grep "percent_unique:" "$WORK/abismal.stats" | head -1 | awk '{print $2}')`
and extend the final `printf` with four more `\t%s` and arguments `"zenigoke-v2" "${PCT_UNIQUE:-NA}" "$ORG_PCT" "$NONCONV"`. Update the comment above it to list cols 16–19.

- [ ] **Step 6: Static check** — `bash -n scripts/pipeline-v2-bs-plant.sh && grep -c -- ' -R ' scripts/pipeline-v2-bs-plant.sh` (expect 2).

- [ ] **Step 7: Commit** — `git commit -am "bs-pipeline: abismal -R, nuclear-only counts, non-conversion, MpTak CpG count"`

### Task 4: Runner changes + smoke runs

**Files:**
- Modify: `scripts/run-sample.sh`, `scripts/run-all.sh`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: env knobs `OUTPUT_ROOT` (default `$DATA_ROOT/output`), `STATUS_DIR` (default `$DATA_ROOT/status`), `LOG_DIR` (default `$DATA_ROOT/logs`), `REF_DIR` (default `$DATA_ROOT/references/MpTak_v7.1_org`), `THREADS` (default 16).

- [ ] **Step 1: `run-sample.sh` variables** — replace the variable block with:

```bash
DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="${REF_DIR:-$DATA_ROOT/references/MpTak_v7.1_org}"
REF_NAME="${REF_NAME:-MpTak_v7.1_org}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$DATA_ROOT/output}"
STATUS_DIR="${STATUS_DIR:-$DATA_ROOT/status}"
LOG_DIR="${LOG_DIR:-$DATA_ROOT/logs}"
PIPELINES_REPO="${PIPELINES_REPO:-$HOME/repos/chip-atlas-pipeline-v2}"
ZENIGOKE_REPO="${ZENIGOKE_REPO:-$HOME/work/zenigoke}"
BLACKLIST="$ZENIGOKE_REPO/configs/organelle_like_regions.MpTak_v7.1.bed"
# 32 threads reproduced a bwa-mem2 SEQ/QUAL mismatch on SRX29617452; 16 did not.
THREADS="${THREADS:-16}"
```

and set `LOG="$LOG_DIR/$ACCESSION.log"`; replace `$DATA_ROOT/output/` with `$OUTPUT_ROOT/` in both `OUT_DIR=` lines.

- [ ] **Step 2: Single-file PAIRED download** — replace the `for run in $RUN_ACCS … done` loop with:

```bash
for run in $RUN_ACCS; do
  # ENA sometimes labels a run PAIRED but serves one interleaved-less file
  # (SRX29617444-455). fast-download.sh then fails at CONCAT, so fetch the
  # single file directly and let the SE path below pick it up.
  FTP=$(curl -fsSL "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${run}&result=read_run&fields=library_layout,fastq_ftp&format=tsv" \
        | awk -F'\t' 'NR==2 {print $1"\t"$2}')
  LAYOUT=${FTP%%$'\t'*}; URLS=${FTP#*$'\t'}
  if [ "$LAYOUT" = "PAIRED" ] && [ "$(echo "$URLS" | tr ';' '\n' | grep -c .)" = "1" ]; then
    echo "   $run: PAIRED with a single FASTQ on ENA → direct download, run as SE"
    aria2c --max-tries=10 --retry-wait=10 --split=4 --max-connection-per-server=4 \
      --continue=true --allow-overwrite=true --auto-file-renaming=false \
      --console-log-level=warn --dir="$FASTQ_DIR" --out="$(basename "$URLS")" "https://$URLS"
  else
    bash "$PIPELINES_REPO/scripts/fast-download.sh" "$run" "$FASTQ_DIR"
  fi
done
```

- [ ] **Step 3: ChIP/ATAC dispatch** — in the ChIP/ATAC branch use the org reference and the fork:

```bash
      --genome-fasta "$REF_DIR/$REF_NAME.fa"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      ...
      --blacklist "$BLACKLIST"
```

docker mounts: replace `-v "$PIPELINES_REPO:$PIPELINES_REPO:ro"` with `-v "$ZENIGOKE_REPO:$ZENIGOKE_REPO:ro"` and run `bash "$ZENIGOKE_REPO/scripts/pipeline-v2-plant.sh" "${ARGS[@]}"`.

- [ ] **Step 4: BS dispatch** — `--genome-fasta "$REF_DIR/$REF_NAME.fa"`, `--abismal-index "$REF_DIR/$REF_NAME.abismal.idx"`, keep `--genome MpTak_v7.1` and `--chrom-sizes "$REF_DIR/chrom.sizes"`.

- [ ] **Step 5: `run-all.sh`** — `STATUS_DIR="${STATUS_DIR:-$DATA_ROOT/status}"` and export it (`export STATUS_DIR`) so `run-sample.sh` inherits it.

- [ ] **Step 6: Smoke runs** (sequential, ~1 h total)

```bash
export OUTPUT_ROOT=/data1/zenigoke/output-v2 STATUS_DIR=/data1/zenigoke/status-v2 LOG_DIR=/data1/zenigoke/logs-v2
for a in "SRX22603362 ChIP-Seq" "SRX29617444 ChIP-Seq" "SRX22603367 ATAC-Seq" "SRX2613745 Bisulfite-Seq" "DRX162965 Bisulfite-Seq"; do
  bash scripts/run-sample.sh $a || echo "FAILED $a"
done
for f in $OUTPUT_ROOT/*/*/*.stats.tsv; do echo "$(basename $f)"; cat "$f"; done
```

Expected (subsample predictions from the QC reports, ±5 pt):
- SRX22603362: 21 cols, col16 `zenigoke-v2`, col7 ≈ 93, col8 = dup %.
- SRX29617444: runs as SE (`fwd=… rev=<SE>` in log), col7 ≈ 90+.
- SRX22603367: col20/col19 large (ATAC organelle share), peaks present, `Blacklist: removed …` line in the log.
- SRX2613745: col6 (concordant %) ≈ 70–75 (was 10.4), cols 13–15 ≈ 0.30 / 0.14 / 0.05 (were 0.99), col19 non-conversion ≈ 0.02.
- DRX162965: col6 ≈ 85 (was 68.1).
- `bigWigInfo`/`.bb` chromosome lists contain no chrC/chrM: `docker run --rm -v /data1:/data1 ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0 bigWigInfo -chroms $OUTPUT_ROOT/chipseq/SRX22603362/SRX22603362.bw | grep -c chr[CM]` → 0.

If any prediction is off by more than 5 pt, stop and debug (superpowers:systematic-debugging) before Task 5.

- [ ] **Step 7: Commit** — `git commit -am "runner: org reference, local fork, configurable roots, single-file PAIRED download, 16 threads"`

### Task 5: Launch the full re-run

**Files:** none in the repo (launch command only).

- [ ] **Step 1: Launch two detached drivers** (ChIP in one; ATAC then BS in the other). `--resume` skips the smoke-run samples.

```bash
cd ~/work/zenigoke
export OUTPUT_ROOT=/data1/zenigoke/output-v2 STATUS_DIR=/data1/zenigoke/status-v2 LOG_DIR=/data1/zenigoke/logs-v2 THREADS=16
setsid nohup bash scripts/run-all.sh --resume --library-strategy ChIP-Seq > /data1/zenigoke/rerun-v2-chip.log 2>&1 &
setsid nohup bash -c 'bash scripts/run-all.sh --resume --library-strategy ATAC-Seq; bash scripts/run-all.sh --resume --library-strategy Bisulfite-Seq' > /data1/zenigoke/rerun-v2-atac-bs.log 2>&1 &
```

The completion notification (user's webhook, token from `~/.claude/CLAUDE.md`) is appended by the session as a third detached watcher that polls for both drivers to exit; it is not committed to the repo.

- [ ] **Step 2: Check progress** — `ls /data1/zenigoke/status-v2/*.ok | wc -l` (target 157), `ls /data1/zenigoke/status-v2/*.failed 2>/dev/null`, `df -h /data1` (keep >100 GB free).

---

## Track B — Catalog fixes (run while Track A is in progress)

### Task 6: Stats parsing for legacy and v2 formats

**Files:**
- Modify: `scripts/build_report.py:62-84`, `scripts/build_catalog_db.py` (schema, fallback key lists, `parse_chipseq_stats`, `parse_bsseq_stats`, `collect_sample_rows`, `populate_sample`)
- Modify: `scripts/build_catalog_pages.py` `_load_samples` (select `s.reads_raw, s.usable_rate`)
- Test: `tests/test_build_catalog_db.py`, `tests/test_build_report.py`

**Interfaces:**
- Produces: `parse_chipseq_stats(row) -> dict` with keys `layout, fastq_size, reads_raw, reads_filtered, reads_mapped, mapping_rate, duplication_rate, usable_rate, elapsed_min, peaks_q5, peaks_q10, peaks_q20, format` plus for v2 `usable_reads, organelle_usable_reads, nuclear_usable_reads`. Legacy: `mapping_rate=None`, `duplication_rate=None`, `usable_rate=col7`, `reads_mapped=None`, `format="legacy"`. v2: `mapping_rate=col7`, `duplication_rate=col8`, `usable_rate=100*col19/col6`, `reads_mapped=col17`, `format="zenigoke-v2"`.
- `parse_bsseq_stats(row)` adds `unique_rate` (col17), `organelle_pct` (col18), `nonconv` (col19) when col16 == `zenigoke-v2`, else None.
- `sample` table gains `reads_raw INTEGER`, `usable_rate REAL`.

- [ ] **Step 1: Failing tests** (append to `tests/test_build_catalog_db.py`)

```python
LEGACY_CHIP = ["SRX1", "1", "3829082553", "0", "53268038", "52973920", "21.3", "78.7",
               "1661569781", "365054360", "68579040", "1097", "772", "403", "21.48"]
V2_CHIP = ["SRX2", "1", "3829082553", "0", "53268038", "52973920", "93.0", "20.0",
           "1661569781", "365054360", "68579040", "1097", "772", "403", "21.48",
           "zenigoke-v2", "49265000", "9853000", "39412000", "412000", "39000000"]


def test_parse_chipseq_stats_legacy_vs_v2():
    legacy = bcd.parse_chipseq_stats(LEGACY_CHIP)
    assert legacy["format"] == "legacy"
    assert legacy["reads_raw"] == 53268038
    assert legacy["reads_filtered"] == 52973920
    assert legacy["usable_rate"] == 21.3
    assert legacy["mapping_rate"] is None
    assert legacy["duplication_rate"] is None

    v2 = bcd.parse_chipseq_stats(V2_CHIP)
    assert v2["format"] == "zenigoke-v2"
    assert v2["mapping_rate"] == 93.0
    assert v2["duplication_rate"] == 20.0
    assert v2["reads_mapped"] == 49265000
    assert v2["nuclear_usable_reads"] == 39000000
    assert abs(v2["usable_rate"] - 100 * 39412000 / 52973920) < 0.05


def test_parse_bsseq_stats_v2_extra_columns():
    base = ["SRX3", "1", "100", "50", "17561884", "73.6", "30.0", "5.0",
            "22", "0", "3", "40", "0.3030", "0.1430", "0.0480"]
    assert bcd.parse_bsseq_stats(base)["nonconv"] is None
    v2 = bcd.parse_bsseq_stats(base + ["zenigoke-v2", "60.1", "45.2", "0.0207"])
    assert v2["unique_rate"] == 60.1
    assert v2["organelle_pct"] == 45.2
    assert v2["nonconv"] == 0.0207
```

Also update `tests/test_build_report.py`'s stats fixture so column 4 is the literal `"0"` (upstream format) and assert `reads_filt` is column 6.

- [ ] **Step 2: Run, expect FAIL** — `python3 -m pytest tests/test_build_catalog_db.py -k "legacy_vs_v2 or v2_extra" -v` → KeyError `format`.

- [ ] **Step 3: Implement**

`scripts/build_report.py`:

```python
def _chipseq_stats_fields(row: list[str]) -> dict[str, str]:
    # pipeline-v2.sh stats.tsv (verified against upstream Step 5):
    #   col 4 is a literal "0" placeholder, col 5 = fastp reads before filtering,
    #   col 6 = after filtering. Legacy (upstream) col 7 = usable % (post-dedup
    #   mapped / filtered) and col 8 = 100 - col 7. zenigoke-v2 (local fork,
    #   col 16 == "zenigoke-v2") redefines col 7/8 as alignment/duplication rate
    #   and appends cols 17-21.
    keys = [
        "sample", "layout", "fastq_size", "v1_placeholder", "reads_raw",
        "reads_filt", "rate7", "rate8",
        "dedup_bam_size", "bedgraph_size", "bigwig_size",
        "peaks_q5", "peaks_q10", "peaks_q20", "elapsed_min",
        "format", "aligned_reads", "duplicate_reads", "usable_reads",
        "organelle_usable_reads", "nuclear_usable_reads",
    ]
    return {k: v for k, v in zip(keys, row)}


def _bsseq_stats_fields(row: list[str]) -> dict[str, str]:
    # Fork cols 1-15 + zenigoke-v2 cols 16-19.
    keys = [
        "sample", "layout", "fastq_size", "dedup_bam_size", "read_count",
        "mapping_rate", "methylation_rate", "cpg_coverage",
        "hmr_count", "pmd_count", "hypermr_count", "elapsed_min",
        "mean_cpg", "mean_chg", "mean_chh",
        "format", "unique_rate", "organelle_pct", "nonconv",
    ]
    return {k: v for k, v in zip(keys, row)}
```

In `build_report.py` wherever `mapping_rate`/`duplication_rate` were read from the ChIP field dict (the report table, `cols = [...]`), read `rate7` and drop `duplication_rate` from the column list.

`scripts/build_catalog_db.py`: make the two fallback key lists identical to the above; then

```python
def parse_chipseq_stats(row: List[str]) -> Dict[str, Any]:
    """Parse a ChIP/ATAC stats.tsv row (legacy 15-col or zenigoke-v2 21-col)."""
    f = _chipseq_fields_fn(row)
    reads_filt = _safe_int(f.get("reads_filt"))
    out: Dict[str, Any] = {
        "layout": f.get("layout"),
        "fastq_size": f.get("fastq_size"),
        "reads_raw": _safe_int(f.get("reads_raw")),
        "reads_filtered": reads_filt,
        "elapsed_min": _safe_float(f.get("elapsed_min")),
        "peaks_q5": _safe_int(f.get("peaks_q5")),
        "peaks_q10": _safe_int(f.get("peaks_q10")),
        "peaks_q20": _safe_int(f.get("peaks_q20")),
    }
    if f.get("format") == "zenigoke-v2":
        usable = _safe_int(f.get("usable_reads"))
        out.update({
            "format": "zenigoke-v2",
            "mapping_rate": _safe_float(f.get("rate7")),
            "duplication_rate": _safe_float(f.get("rate8")),
            "reads_mapped": _safe_int(f.get("aligned_reads")),
            "usable_reads": usable,
            "organelle_usable_reads": _safe_int(f.get("organelle_usable_reads")),
            "nuclear_usable_reads": _safe_int(f.get("nuclear_usable_reads")),
            "usable_rate": (100.0 * usable / reads_filt) if usable and reads_filt else None,
        })
    else:
        # Legacy upstream: col 7 is usable %, not an alignment rate; col 8 is
        # 100 - col 7 and mixes unmapped with duplicates, so it is dropped.
        out.update({
            "format": "legacy",
            "mapping_rate": None,
            "duplication_rate": None,
            "reads_mapped": None,
            "usable_rate": _safe_float(f.get("rate7")),
        })
    return out
```

`parse_bsseq_stats`: add
```python
        "unique_rate": _safe_float(fields.get("unique_rate")) if fields.get("format") == "zenigoke-v2" else None,
        "organelle_pct": _safe_float(fields.get("organelle_pct")) if fields.get("format") == "zenigoke-v2" else None,
        "nonconv": _safe_float(fields.get("nonconv")) if fields.get("format") == "zenigoke-v2" else None,
```
Schema: add `reads_raw INTEGER,` after `fastq_size` and `usable_rate REAL,` after `duplication_rate` in `sample`; `collect_sample_rows` initialises and copies `reads_raw`/`usable_rate` from `parsed`; `populate_sample` inserts the two new columns (14 placeholders). `_load_samples` in `build_catalog_pages.py` selects `s.reads_raw, s.usable_rate`.

- [ ] **Step 4: Run all tests** — `python3 -m pytest tests -q` → all pass (fix the existing `test_populate_sample_writes_correct_row` fixture: its stats row must now have col 4 = `"0"`; assert `mapping_rate is None` and `usable_rate` = old col 7 value).

- [ ] **Step 5: Commit** — `git commit -am "db: parse legacy vs zenigoke-v2 stats; stop presenting usable% as mapping rate"`

### Task 7: QC flags module + `sample_qc` table

**Files:**
- Create: `scripts/qc_flags.py`, `tests/test_qc_flags.py`
- Modify: `scripts/build_catalog_db.py` (schema, QC TSV loader, `build()` populates `sample_qc`, CLI `--qc-dir`)

**Interfaces:**
- Consumes: Task 6 parsed stats (`_chipseq_stats` / `_bsseq_stats` dicts on each row).
- Produces:
  - `qc_flags.chip_flag(m: dict) -> tuple[str, list[str], str]` → `(qc_flag, reasons, peak_flag)`; keys read from `m`: `status, aligned_pct, dup_pct, usable_fragments, organelle_pct, human_pct, bacterial_pct, peaks_q05, peaks_in_organelle_like_pct`.
  - `qc_flags.bs_flag(m: dict) -> tuple[str, list[str]]`; keys: `status, mean_cpg, mean_chg, mean_chh, nonconv, foreign_pct, frac_cpg_cov_ge5`.
  - Flag `"NA"` when a sample is `ok` but has no metric at all.
  - Table `sample_qc(accession PK, qc_flag, qc_reasons, peak_flag, aligned_pct, dup_pct, usable_fragments, organelle_pct, foreign_pct, peaks_in_organelle_like_pct, nonconv_pct, frac_cpg_cov_ge5, metrics_source)`; `metrics_source` ∈ {`pipeline`, `subsample-2026-10-01`, `pipeline+subsample`}.
  - `build(data_root, metadata_root, db_path, qc_dir=None, curation_overrides=None)`.

- [ ] **Step 1: Failing tests** — `tests/test_qc_flags.py`

```python
from __future__ import annotations
import csv, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import qc_flags  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
CHIP_TSV = REPO / "docs/qc/2026-10-01-chip-atlas-qc.tsv"
BS_TSV = REPO / "docs/qc/2026-10-01-bsseq-qc.tsv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def test_chip_thresholds():
    ok = dict(status="ok", aligned_pct=95, dup_pct=20, usable_fragments=10e6,
              organelle_pct=2, human_pct=0, bacterial_pct=1, peaks_q05=5000,
              peaks_in_organelle_like_pct=2)
    assert qc_flags.chip_flag(ok)[0] == "PASS"
    assert qc_flags.chip_flag({**ok, "aligned_pct": 29.9})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "aligned_pct": 69.9})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "dup_pct": 80})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "usable_fragments": 0.9e6})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "human_pct": 6, "bacterial_pct": 4})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "organelle_pct": 20})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_in_organelle_like_pct": 25})[2] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_q05": 0})[2] == "NA"
    assert qc_flags.chip_flag({"status": "failed"})[0] == "FAIL"


def test_bs_thresholds():
    ok = dict(status="ok", mean_cpg=0.4, mean_chg=0.2, mean_chh=0.05, nonconv=0.005,
              foreign_pct=5, frac_cpg_cov_ge5=0.5)
    assert qc_flags.bs_flag(ok)[0] == "PASS"
    assert qc_flags.bs_flag({**ok, "mean_cpg": 0.99, "mean_chg": 0.99, "mean_chh": 0.99})[0] == "FAIL"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.092})[0] == "FAIL"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.028})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "foreign_pct": 85})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "frac_cpg_cov_ge5": 0.014})[0] == "WARN"


def test_flag_with_no_metrics_is_na():
    assert qc_flags.chip_flag({"status": "ok"})[0] == "NA"
    assert qc_flags.bs_flag({"status": "ok"})[0] == "NA"


def test_reproduces_investigation_flags():
    """The module must agree with the 2026-10-01 investigation on every sample."""
    for r in csv.DictReader(CHIP_TSV.open(), delimiter="\t"):
        m = qc_flags.chip_metrics_from_tsv(r)
        assert qc_flags.chip_flag(m)[0] == r["qc_flag"], r["accession"]
        assert qc_flags.chip_flag(m)[2] == r["peak_flag"], r["accession"]
    for r in csv.DictReader(BS_TSV.open(), delimiter="\t"):
        m = qc_flags.bs_metrics_from_tsv(r)
        assert qc_flags.bs_flag(m)[0] == r["qc_flag"], r["accession"]
```

- [ ] **Step 2: Run, expect FAIL** — `python3 -m pytest tests/test_qc_flags.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement `scripts/qc_flags.py`**

```python
"""QC flags for the zenigoke catalog (thresholds from the 2026-10-01 review).

Pure functions: metrics dict -> flag. Missing metrics are skipped, so the same
rules work on subsample estimates (docs/qc TSVs) and on zenigoke-v2 pipeline
stats. A sample that completed but has no metrics at all gets "NA".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _num(m: Dict[str, Any], k: str) -> Optional[float]:
    v = m.get(k)
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _worst(fails: List[str], warns: List[str], any_metric: bool) -> str:
    if fails:
        return "FAIL"
    if warns:
        return "WARN"
    return "PASS" if any_metric else "NA"


def chip_flag(m: Dict[str, Any]) -> Tuple[str, List[str], str]:
    if m.get("status") not in (None, "ok"):
        return "FAIL", ["pipeline did not complete"], "NA"
    a, d = _num(m, "aligned_pct"), _num(m, "dup_pct")
    frag, org = _num(m, "usable_fragments"), _num(m, "organelle_pct")
    hum, bac = _num(m, "human_pct"), _num(m, "bacterial_pct")
    fails: List[str] = []
    warns: List[str] = []
    if a is not None:
        if a < 30:
            fails.append(f"alignment rate {a:.0f}% < 30%")
        elif a < 70:
            warns.append(f"alignment rate {a:.0f}% < 70%")
    if d is not None:
        if d >= 80:
            fails.append(f"duplication {d:.0f}% ≥ 80%")
        elif d >= 50:
            warns.append(f"duplication {d:.0f}% ≥ 50%")
    if frag is not None:
        if frag < 1e6:
            fails.append(f"{frag / 1e6:.1f}M usable fragments < 1M")
        elif frag < 4e6:
            warns.append(f"{frag / 1e6:.1f}M usable fragments < 4M")
    if hum is not None or bac is not None:
        foreign = (hum or 0) + (bac or 0)
        if foreign >= 10:
            parts = []
            if bac and bac >= 1:
                parts.append(f"bacterial-like {bac:.0f}%")
            if hum and hum >= 1:
                parts.append(f"human {hum:.0f}%")
            warns.append("non-Marchantia DNA " + ", ".join(parts))
    if org is not None and org >= 20:
        warns.append(f"organelle reads {org:.0f}%")
    any_metric = any(x is not None for x in (a, d, frag, org, hum, bac))

    p05, pov = _num(m, "peaks_q05"), _num(m, "peaks_in_organelle_like_pct")
    if not p05:
        peak_flag = "NA"
    elif pov is not None and pov >= 20 and p05 >= 50:
        peak_flag = "WARN"
        warns.append(f"{pov:.0f}% of peaks in organelle-like (NUPT/NUMT) regions")
    else:
        peak_flag = "OK"
    # peak_flag is reported separately; it does not change qc_flag on its own
    flag = _worst(fails, [w for w in warns if "NUPT" not in w], any_metric)
    return flag, fails + warns, peak_flag


def bs_flag(m: Dict[str, Any]) -> Tuple[str, List[str]]:
    if m.get("status") not in (None, "ok"):
        return "FAIL", ["pipeline did not complete"]
    cg, chg, chh = _num(m, "mean_cpg"), _num(m, "mean_chg"), _num(m, "mean_chh")
    nc, fo, cov = _num(m, "nonconv"), _num(m, "foreign_pct"), _num(m, "frac_cpg_cov_ge5")
    fails: List[str] = []
    warns: List[str] = []
    if None not in (cg, chg, chh) and min(cg, chg, chh) >= 0.9:
        fails.append("methylation ≥ 0.9 in all contexts (reads mapped to the wrong strand)")
    if nc is not None:
        if nc >= 0.05:
            fails.append(f"non-conversion {nc * 100:.1f}% ≥ 5%")
        elif nc > 0.02:
            warns.append(f"non-conversion {nc * 100:.1f}% > 2%")
    if fo is not None and fo >= 50:
        warns.append(f"non-Marchantia reads {fo:.0f}%")
    if cov is not None and cov < 0.05:
        warns.append(f"only {cov * 100:.1f}% of CpGs covered ≥5×")
    any_metric = any(x is not None for x in (cg, chg, chh, nc, fo, cov))
    return _worst(fails, warns, any_metric), fails + warns


def chip_metrics_from_tsv(r: Dict[str, str]) -> Dict[str, Any]:
    """Map a docs/qc/2026-10-01-chip-atlas-qc.tsv row to chip_flag() input."""
    return {
        "status": r.get("status") or "ok",
        "aligned_pct": r.get("aligned_pct"),
        "dup_pct": r.get("dup_pct"),
        "usable_fragments": r.get("usable_fragments"),
        "organelle_pct": r.get("organelle_pct"),
        "human_pct": r.get("human_pct"),
        "bacterial_pct": r.get("other_unmapped_pct"),
        "peaks_q05": r.get("peaks_q05"),
        "peaks_in_organelle_like_pct": r.get("peaks_in_organelle_like_pct"),
    }


def bs_metrics_from_tsv(r: Dict[str, str]) -> Dict[str, Any]:
    """Map a docs/qc/2026-10-01-bsseq-qc.tsv row to bs_flag() input."""
    unmapped = _num(r, "pct_reads_unmapped_all_refs")
    bacteria = _num(r, "pct_reads_bacteria_panel")
    foreign = None if unmapped is None and bacteria is None else (unmapped or 0) + (bacteria or 0)
    return {
        "status": "ok",
        "mean_cpg": r.get("mean_cpg"),
        "mean_chg": r.get("mean_chg"),
        "mean_chh": r.get("mean_chh"),
        "nonconv": r.get("nonconv_chrC_CHH"),
        "foreign_pct": foreign,
        "frac_cpg_cov_ge5": r.get("frac_cpg_cov_ge5"),
    }
```

- [ ] **Step 4: Run tests** — `python3 -m pytest tests/test_qc_flags.py -v`. If `test_reproduces_investigation_flags` fails on a few accessions, print the metrics for those rows and reconcile against the investigation scripts (`/data1/zenigoke/tmp/qc-20261001-chip/scripts/final_tsv.py` `classify()`, `/data1/zenigoke/tmp/qc-20261001-bs/flag_tsv.py`) — e.g. a `status` column value, or BS `frac_cpg_cov_ge5` being exempted for FAIL samples. Adjust the rule, not the test.

- [ ] **Step 5: DB integration test** (append to `tests/test_build_catalog_db.py`)

```python
def test_build_populates_sample_qc_from_tsv(tmp_path):
    data_root = tmp_path / "data"
    out = data_root / "output" / "chipseq" / "SRX9800303"
    _write(out / "SRX9800303.stats.tsv", "\t".join(LEGACY_CHIP).replace("SRX1", "SRX9800303") + "\n")
    _write(data_root / "status" / "SRX9800303.ok", "")
    qc_dir = tmp_path / "qc"
    _write(qc_dir / "2026-10-01-chip-atlas-qc.tsv",
           "accession\tstrategy\tstatus\taligned_pct\tdup_pct\tusable_fragments\torganelle_pct\t"
           "human_pct\tother_unmapped_pct\tpeaks_q05\tpeaks_in_organelle_like_pct\tqc_flag\tpeak_flag\n"
           "SRX9800303\tChIP-Seq\tok\t12.9\t26.6\t5600000\t0.3\t0.2\t86\t2662\t1.0\tFAIL\tOK\n")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db, qc_dir=qc_dir)
    row = sqlite3.connect(str(db)).execute(
        "SELECT qc_flag, aligned_pct, metrics_source FROM sample_qc WHERE accession='SRX9800303'").fetchone()
    assert row == ("FAIL", 12.9, "subsample-2026-10-01")
```

- [ ] **Step 6: Implement in `build_catalog_db.py`**

Schema addition:

```sql
CREATE TABLE sample_qc (
  accession                    TEXT PRIMARY KEY REFERENCES sample(accession),
  qc_flag                      TEXT NOT NULL,
  qc_reasons                   TEXT,
  peak_flag                    TEXT,
  aligned_pct                  REAL,
  dup_pct                      REAL,
  usable_fragments             INTEGER,
  organelle_pct                REAL,
  foreign_pct                  REAL,
  peaks_in_organelle_like_pct  REAL,
  nonconv_pct                  REAL,
  frac_cpg_cov_ge5             REAL,
  metrics_source               TEXT
);
CREATE INDEX idx_qc_flag ON sample_qc(qc_flag);
```

Loader + merge (new functions):

```python
import qc_flags  # top of file, after sys.path tweak: sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

QC_TSV_NAMES = {"chip": "2026-10-01-chip-atlas-qc.tsv", "bs": "2026-10-01-bsseq-qc.tsv"}


def load_qc_tsvs(qc_dir: Optional[pathlib.Path]) -> Dict[str, Dict[str, str]]:
    """accession -> raw TSV row (with '_kind' = chip|bs). Empty if qc_dir is None."""
    out: Dict[str, Dict[str, str]] = {}
    if qc_dir is None:
        return out
    for kind, name in QC_TSV_NAMES.items():
        p = qc_dir / name
        if not p.exists():
            continue
        with p.open() as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                out[r["accession"]] = {**r, "_kind": kind}
    return out


def qc_row(row: Dict[str, Any], tsv: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """Merge pipeline stats (preferred when zenigoke-v2) with subsample estimates."""
    strat, status = row["library_strategy"], row["status"]
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        m = qc_flags.chip_metrics_from_tsv(tsv) if tsv else {}
        m["status"] = status
        st = row.get("_chipseq_stats", {})
        source = "subsample-2026-10-01" if tsv else None
        if st.get("format") == "zenigoke-v2":
            pe = row.get("layout") == "1"
            nuc = st.get("nuclear_usable_reads") or 0
            usable = st.get("usable_reads") or 0
            m.update({
                "aligned_pct": st.get("mapping_rate"),
                "dup_pct": st.get("duplication_rate"),
                "usable_fragments": nuc / 2 if pe else nuc,
                "organelle_pct": 100.0 * (st.get("organelle_usable_reads") or 0) / usable if usable else None,
                "peaks_q05": st.get("peaks_q5"),
                "peaks_in_organelle_like_pct": 0.0,  # v2 peaks are blacklist-filtered
            })
            source = "pipeline+subsample" if tsv else "pipeline"
        flag, reasons, peak_flag = qc_flags.chip_flag(m)
        hum, bac = m.get("human_pct"), m.get("bacterial_pct")
        foreign = None if hum in (None, "") and bac in (None, "") else float(hum or 0) + float(bac or 0)
        return {"qc_flag": flag, "qc_reasons": "; ".join(reasons), "peak_flag": peak_flag,
                "aligned_pct": _safe_float(m.get("aligned_pct")), "dup_pct": _safe_float(m.get("dup_pct")),
                "usable_fragments": _safe_int(_safe_float(m.get("usable_fragments"))),
                "organelle_pct": _safe_float(m.get("organelle_pct")), "foreign_pct": foreign,
                "peaks_in_organelle_like_pct": _safe_float(m.get("peaks_in_organelle_like_pct")),
                "nonconv_pct": None, "frac_cpg_cov_ge5": None, "metrics_source": source}
    m = qc_flags.bs_metrics_from_tsv(tsv) if tsv else {}
    m["status"] = status
    st = row.get("_bsseq_stats", {})
    source = "subsample-2026-10-01" if tsv else None
    for k in ("mean_cpg", "mean_chg", "mean_chh"):
        if st.get(k) is not None:
            m[k] = st[k]
    if st.get("nonconv") is not None:
        m["nonconv"] = st["nonconv"]
        source = "pipeline+subsample" if tsv else "pipeline"
    flag, reasons = qc_flags.bs_flag(m)
    nc = _safe_float(m.get("nonconv"))
    return {"qc_flag": flag, "qc_reasons": "; ".join(reasons), "peak_flag": None,
            "aligned_pct": None, "dup_pct": None, "usable_fragments": None,
            "organelle_pct": _safe_float(st.get("organelle_pct")) if st.get("organelle_pct") is not None
            else _safe_float((tsv or {}).get("pct_reads_chrC")),
            "foreign_pct": _safe_float(m.get("foreign_pct")),
            "peaks_in_organelle_like_pct": None,
            "nonconv_pct": nc * 100 if nc is not None else None,
            "frac_cpg_cov_ge5": _safe_float(m.get("frac_cpg_cov_ge5")), "metrics_source": source}


def populate_qc(conn: sqlite3.Connection, accession: str, q: Dict[str, Any]) -> None:
    cols = ["qc_flag", "qc_reasons", "peak_flag", "aligned_pct", "dup_pct", "usable_fragments",
            "organelle_pct", "foreign_pct", "peaks_in_organelle_like_pct", "nonconv_pct",
            "frac_cpg_cov_ge5", "metrics_source"]
    conn.execute(
        f"INSERT OR REPLACE INTO sample_qc(accession, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))})",
        [accession] + [q.get(c) for c in cols],
    )
```

`build()` gains `qc_dir: Optional[pathlib.Path] = None`; loads `qc = load_qc_tsvs(qc_dir)` once and, after `populate_sample`, calls `populate_qc(conn, acc, qc_row(row, qc.get(acc)))` (count in `counts["sample_qc"]`). `main()` gains `--qc-dir` (default `docs/qc`). Add `import csv` at the top.

- [ ] **Step 7: Run all tests; rebuild the real DB and check counts**

```bash
python3 -m pytest tests -q
python3 scripts/build-catalog-db.py
python3 -c "import sqlite3;c=sqlite3.connect('db/kknmsmd.db');print(c.execute('select qc_flag,count(*) from sample_qc group by 1').fetchall())"
```
Expected: `[('FAIL', 18), ('PASS', 67), ('WARN', 72)]`.

- [ ] **Step 8: Commit** — `git add scripts/qc_flags.py tests/test_qc_flags.py && git commit -am "qc: PASS/WARN/FAIL flags in sample_qc from pipeline stats + 2026-10-01 subsample estimates"`

### Task 8: Curation overrides

**Files:**
- Create: `configs/curation_overrides.tsv`
- Modify: `scripts/build_catalog_db.py` (`load_curation_overrides`, apply in `build()`; CLI `--curation-overrides`, default `configs/curation_overrides.tsv`)
- Test: `tests/test_build_catalog_db.py`

**Interfaces:**
- Produces: `load_curation_overrides(path) -> Dict[str, Dict[str, str]]` (accession → {field: value}); `apply_curation_overrides(curation: dict, overrides_for_acc: dict) -> dict` (returns a new dict; unknown fields raise `ValueError`).

- [ ] **Step 1: Write the overrides file**

```tsv
# Manual fixes where bsllmner-mk2 output contradicts the SRA experiment title.
# Source: docs/qc/2026-10-01-chip-atlas-mapping-qc.md §5.3 (+ SRX9800295 synonym).
accession	field	value	reason
SRX29617466	antibody_target	H3	title "H3 ChIP-seq in wild-type Tak1"
SRX29617480	antibody_target	H3	title "H3 ChIP-seq in Mpez1,Mpknox2 double mutant"
SRX29617481	antibody_target	H3	title "H3 ChIP-seq in Mpez1,Mpknox2 double mutant"
SRX8604219	antibody_target	WRKY14	title "6_WRKY14-RFP_ChIP_rep2" (rep1 SRX8604213 is WRKY14)
SRX9800283	antibody_target	H3	title "Upp5_H3_R2"
SRX9800284	antibody_target	H3	title "H2AK115RK116R_H3_R1" (genotype, not target)
SRX9800286	antibody_target	H3	title "H2AK119R_H3_R1" (genotype, not target)
SRX9800287	antibody_target	H3	title "H2AK119R_H3_R2" (genotype, not target)
SRX9800295	antibody_target	H2Aub	title "H2AK119R_H2Aub_R2"; normalise synonym H2AK119ub
SRX9800300	antibody_target	H3K27me3	title "H2AK115RK116R_H3K27me3_R1_L16"
SRX9800302	antibody_target	H3K27me3	title "H2AK119R_H3K27me3__R1"
SRX9800303	antibody_target	H3K27me3	title "H2AK119R_H3K27me3_R2_L16"
```

- [ ] **Step 2: Failing test**

```python
def test_curation_overrides_apply(tmp_path):
    p = tmp_path / "o.tsv"
    p.write_text("# c\naccession\tfield\tvalue\treason\nSRX1\tantibody_target\tH3\tt\n")
    ov = bcd.load_curation_overrides(p)
    cur = {"antibody_target": "H2A", "tissue": "thallus"}
    assert bcd.apply_curation_overrides(cur, ov["SRX1"]) == {"antibody_target": "H3", "tissue": "thallus"}
    assert cur["antibody_target"] == "H2A"  # input not mutated
    with pytest.raises(ValueError):
        bcd.apply_curation_overrides(cur, {"antibdy": "x"})


def test_repo_overrides_file_is_valid():
    ov = bcd.load_curation_overrides(pathlib.Path(__file__).resolve().parent.parent / "configs/curation_overrides.tsv")
    assert len(ov) == 12
    for acc, fields in ov.items():
        bcd.apply_curation_overrides({}, fields)
```

- [ ] **Step 3: Implement**

```python
CURATION_FIELDS = ("tissue", "cell_type", "developmental_stage",
                   "genotype_strain", "treatment", "antibody_target")


def load_curation_overrides(path: Optional[pathlib.Path]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    if path is None or not path.exists():
        return out
    with path.open() as fh:
        rows = csv.DictReader((ln for ln in fh if not ln.startswith("#")), delimiter="\t")
        for r in rows:
            out.setdefault(r["accession"], {})[r["field"]] = r["value"]
    return out


def apply_curation_overrides(curation: Dict[str, Any], overrides: Dict[str, str]) -> Dict[str, Any]:
    bad = set(overrides) - set(CURATION_FIELDS)
    if bad:
        raise ValueError(f"unknown curation field(s): {sorted(bad)}")
    return {**curation, **overrides}
```

In `build()`, before `populate_curation`: `curation = apply_curation_overrides(curation, overrides.get(acc, {}))` when the accession has overrides (also when `curation` was empty). `build()` gains `curation_overrides: Optional[pathlib.Path] = None`.

- [ ] **Step 4: Tests + rebuild** — `python3 -m pytest tests -q`; `python3 scripts/build-catalog-db.py`; check `SELECT antibody_target, count(*) FROM sample_curation WHERE accession LIKE 'SRX98002%' OR accession LIKE 'SRX98003%' GROUP BY 1` → only H3, H2Aub, H3K27me3.

- [ ] **Step 5: Commit** — `git add configs/curation_overrides.tsv && git commit -am "curation: overrides for 12 antibody targets contradicting experiment titles"`

### Task 9: Pages — QC display, labels, Methods text

**Files:**
- Modify: `scripts/build_catalog_pages.py` (`_load_samples`, `_sample_row_html`, `render_browse`, `render_sample`, `_strategy_columns`, `render_summary`, `render_methods`, `STYLE_CSS`)
- Test: `tests/test_build_catalog_pages.py`

**Interfaces:**
- Consumes: `sample_qc` columns (Task 7), `reads_raw`/`usable_rate` (Task 6).
- Produces: helpers `_qc_pill(flag) -> str` (`<span class="qc qc-FAIL" title=…>FAIL</span>`), `_aligned(s) -> Optional[float]` (= `s["aligned_pct"]` if set, else `s["mapping_rate"]`).

- [ ] **Step 1: Failing tests** (append)

```python
def _qc_sample(**kw):
    s = dict(CHIP_SAMPLE)          # existing ChIP fixture dict at the top of this file
    s.update(accession="SRX9800303", library_strategy="ChIP-Seq", status="ok",
             mapping_rate=None, usable_rate=9.6, aligned_pct=12.9, dup_pct=26.6,
             qc_flag="FAIL", qc_reasons="alignment rate 13% < 30%; non-Marchantia DNA bacterial-like 86%",
             peak_flag="OK", metrics_source="subsample-2026-10-01")
    s.update(kw)
    return s


def test_sample_page_shows_qc_flag_and_reasons():
    html_out = bcp.render_sample(_qc_sample())
    assert "qc-FAIL" in html_out
    assert "bacterial-like 86%" in html_out
    assert "12.9%" in html_out
    assert "estimated from a 100k-read subsample" in html_out
    assert "duplication_rate" not in html_out


def test_browse_has_qc_and_aligned_columns():
    out = bcp.render_browse([_qc_sample()])
    assert ">QC<" in out and ">aligned %<" in out
    assert "mapping_rate" not in out


def test_methods_q_value_wording_and_limitations():
    out = bcp.render_methods()
    assert "q &lt; 1e-5, 1e-10, 1e-20" in out
    assert "0.05, 0.10, 0.20" not in out
    assert "corrupted FASTQ" not in out
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**
  - `_load_samples`: `LEFT JOIN sample_qc q USING (accession)` and select `q.qc_flag, q.qc_reasons, q.peak_flag, q.aligned_pct, q.dup_pct, q.usable_fragments, q.organelle_pct, q.foreign_pct, q.peaks_in_organelle_like_pct, q.nonconv_pct, q.frac_cpg_cov_ge5, q.metrics_source`, plus `s.reads_raw, s.usable_rate`.
  - Helpers:

```python
def _qc_pill(flag: Optional[str], reasons: Optional[str] = None) -> str:
    if not flag:
        return "&mdash;"
    title = f' title="{_esc(reasons)}"' if reasons else ""
    return f'<span class="qc qc-{_esc(flag)}"{title}>{_esc(flag)}</span>'


def _aligned(s: dict) -> Optional[float]:
    a = s.get("aligned_pct")
    return a if a is not None else s.get("mapping_rate")
```
  - Browse: columns `["accession", "strategy", "QC", "tissue", "dev_stage", "strain", "antibody", "aligned %", "elapsed_min", ""]`; row cells `_qc_pill(s.get("qc_flag"), s.get("qc_reasons"))` and `_fmt(_aligned(s), 1)`. Keep `data-col` indices consistent with the sort JS (it sorts by column index).
  - Strategy pages: replace each `("mapping_rate", …)` with `("aligned %", lambda s: _fmt_pct(_aligned(s)))` and insert `("QC", lambda s: _qc_pill(s.get("qc_flag"), s.get("qc_reasons")))` after `status`. For `bsseq` label it `"mapped %"` (abismal concordant pairs/reads; keep `s.get("mapping_rate")`).
  - Sample page, after the title card: a QC card

```python
    qc_rows = [("QC flag", _qc_pill(sample.get("qc_flag")))]
    if sample.get("qc_reasons"):
        qc_rows.append(("reasons", "<br>".join(_esc(r) for r in sample["qc_reasons"].split("; "))))
    if strat != "Bisulfite-Seq":
        qc_rows += [
            ("alignment rate", _fmt_pct(_aligned(sample))),
            ("duplication rate", _fmt_pct(sample.get("dup_pct") if sample.get("dup_pct") is not None else sample.get("duplication_rate"))),
            ("usable reads (dedup, aligned) / trimmed", _fmt_pct(sample.get("usable_rate"))),
            ("organelle reads", _fmt_pct(sample.get("organelle_pct"))),
            ("non-Marchantia DNA", _fmt_pct(sample.get("foreign_pct"))),
            ("peaks in organelle-like regions", _fmt_pct(sample.get("peaks_in_organelle_like_pct"))),
        ]
    else:
        qc_rows += [
            ("non-conversion (chloroplast CHH)", _fmt_pct(sample.get("nonconv_pct"))),
            ("non-Marchantia reads", _fmt_pct(sample.get("foreign_pct"))),
        ]
    note = ""
    if (sample.get("metrics_source") or "").startswith("subsample"):
        note = ('<p class="note">Alignment, duplication, organelle and contamination values are '
                "estimated from a 100k-read subsample (2026-10-01 QC investigation).</p>\n")
    parts.append(_card("Quality Control", _kv_table(qc_rows) + note))
```

    In the "Pipeline Stats" card, drop the `mapping_rate`/`duplication_rate`/`reads_mapped` rows for ChIP/ATAC (now in the QC card); for BS rename the row to `mapped pairs/reads (abismal, incl. multi-mapped)`.
  - `render_summary`: average uses `_aligned(s)` and the heading says "alignment rate".
  - `render_methods`:
    - tools line → `"<li>MACS3 3.0.4 (peak calling at q &lt; 1e-5; peak sets at q &lt; 1e-5, 1e-10, 1e-20)</li>\n"`
    - Known Limitations list → replace the SRX29617452 item with `"<li>SRX29617452 failed during alignment with 32 threads (bwa-mem2 SEQ/QUAL length mismatch). The FASTQ is intact (md5 matches ENA); it is being re-run with 16 threads.</li>"`; replace the ATAC organelle item with `"<li>Until the 2026-10 re-analysis is published, the reference lacks the chloroplast and mitochondrion: organelle reads map to nuclear NUPT/NUMT copies, lowering BS-seq mapping rates and creating spurious ATAC/input peaks there. QC flags mark the affected experiments.</li>"`; add `"<li>Several ChIP libraries contain non-Marchantia DNA (bacterial-like and human), up to 86% of reads; see each experiment's QC card.</li>"`.
  - `STYLE_CSS` additions:

```css
.qc { display: inline-block; padding: 0 .45rem; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.qc-PASS { background: #e4f3e2; color: #1d6b1d; }
.qc-WARN { background: #fbf0d6; color: #8a5a00; }
.qc-FAIL { background: #f9e2e0; color: #a12a2a; }
.qc-NA   { background: #eee; color: #555; }
.note { font-size: .85rem; color: #555; }
```

- [ ] **Step 4: Tests pass; fix existing tests that asserted `mapping_rate` labels** (`test_mapping_rate_rendered_with_percent` → assert `aligned %`/`alignment rate` with `%`).

- [ ] **Step 5: Rebuild and eyeball** — `python3 scripts/build-catalog-pages.py`; open `report/samples/SRX9800303.html`, `report/samples/SRX2613745.html`, `report/browse.html` via the local server and confirm the pills and QC cards.

- [ ] **Step 6: Commit** — `git commit -am "pages: QC flags/reasons, true alignment rate, corrected q-value wording and limitations"`

### Task 10: Shared IGV module (fix hand-off)

**Files:**
- Create: `report/assets/igv.js`, `tests/js/igv.test.js`, `tests/test_igv_js.py`
- Modify: `report/assets/matrix.js`, `report/assets/enrichment.js`, `scripts/build_catalog_pages.py` (`render_enrichment_page` and `render_bundle_shell` include `igv.js` instead of `matrix.js`; `render_index` includes `igv.js` before `matrix.js`), `scripts/igv_url_helper.py` (peaks `.{q}.bb`)

**Interfaces:**
- Produces (global `window.ZenigokeIGV` in browser, `module.exports` in node):
  - `dataUrl(path, base) -> string` — joins with exactly one `/`.
  - `tracksForAccession(acc, {strategy, q_cutoff, label}, color, base) -> [{name, url, format, color}]` — ChIP/ATAC: `{acc}.bw` (bigwig) + `{acc}.{05|10|20}.bb` (bigbed); BS: CpG/CHG/CHH `methyl.bw` + `CpG.hmr.bed` (bed).
  - `loadUrl(tracks, {genome, locus, port}) -> string` — `http://127.0.0.1:{port}/load?genome=…&file=u1,u2&name=n1,n2&format=f1,f2&merge=true[&locus=…]`, each value `encodeURIComponent`-ed, list separator a literal comma; names have commas replaced by `;`.
  - `async send(tracks, opts) -> {ok: bool, status: "loaded"|"igv-error"|"unreachable", message: string}` — probes `/execute?command=version` first; `/load` 204 → loaded; 200 with body starting `ERROR` → igv-error; network error → unreachable.
  - `DEFAULT_GENOME = "https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/hub.txt"` (overridable via `window.ZENIGOKE_IGV_GENOME`).

- [ ] **Step 1: Failing node test** — `tests/js/igv.test.js`

```js
"use strict";
const assert = require("assert");
const I = require("../../report/assets/igv.js");
const BASE = "https://b.s3.ap-northeast-1.amazonaws.com/";

assert.strictEqual(I.dataUrl("output/x.bw", BASE), "https://b.s3.ap-northeast-1.amazonaws.com/output/x.bw");
assert.strictEqual(I.dataUrl("output/x.bw", "https://b.example"), "https://b.example/output/x.bw");
assert.strictEqual(I.dataUrl("output/x.bw", ""), "output/x.bw");

const chip = I.tracksForAccession("SRX1", {strategy: "ChIP-Seq", q_cutoff: "1e-10", label: "H3K27me3, Mpez1,Mpknox2"}, "#123456", BASE);
assert.deepStrictEqual(chip.map(t => t.format), ["bigwig", "bigbed"]);
assert.ok(chip[1].url.endsWith("/output/chipseq/SRX1/SRX1.10.bb"));
// names never contain commas
for (const t of chip) assert.ok(!t.name.includes(","), t.name);

const bs = I.tracksForAccession("SRX2", {strategy: "Bisulfite-Seq"}, "#000", BASE);
assert.deepStrictEqual(bs.map(t => t.format), ["bigwig", "bigwig", "bigwig", "bed"]);

const u = new URL(I.loadUrl(chip, {genome: I.DEFAULT_GENOME, locus: "chr6:1-100", port: 60151}));
assert.strictEqual(u.origin, "http://127.0.0.1:60151");
assert.strictEqual(u.pathname, "/load");
assert.strictEqual(u.searchParams.get("genome"), I.DEFAULT_GENOME);
assert.strictEqual(u.searchParams.get("file").split(",").length, 2);
assert.strictEqual(u.searchParams.get("name").split(",").length, 2);
assert.strictEqual(u.searchParams.get("format"), "bigwig,bigbed");
assert.strictEqual(u.searchParams.get("merge"), "true");

(async () => {
  // send result maps network error to "unreachable"
  const down = async () => { throw new TypeError("Failed to fetch"); };
  let r = await I.send(chip, {fetchImpl: down});
  assert.strictEqual(r.ok, false); assert.strictEqual(r.status, "unreachable");
  const ok = async (url) => ({status: url.includes("/execute") ? 200 : 204, text: async () => ""});
  r = await I.send(chip, {fetchImpl: ok});
  assert.strictEqual(r.status, "loaded");
  const err = async (url) => ({status: 200, text: async () => url.includes("/execute") ? "2.19.1" : "ERROR: genome not found"});
  r = await I.send(chip, {fetchImpl: err});
  assert.strictEqual(r.status, "igv-error"); assert.ok(r.message.includes("genome not found"));
  console.log("igv.js: all tests passed");
})().catch(e => { console.error(e); process.exit(1); });
```

`tests/test_igv_js.py`:

```python
from __future__ import annotations
import pathlib, shutil, subprocess
import pytest

HERE = pathlib.Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_igv_js_unit_tests():
    r = subprocess.run(["node", str(HERE / "js" / "igv.test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Run, expect FAIL** — `python3 -m pytest tests/test_igv_js.py -v` → "Cannot find module".

- [ ] **Step 3: Implement `report/assets/igv.js`**

```js
"use strict";
// Shared IGV helpers: track URLs, the IGV batch-port /load URL, and sending.
// Loaded as a plain <script> (window.ZenigokeIGV) and by node unit tests.
(function (root) {
  const DEFAULT_GENOME = "https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/hub.txt";
  const Q_LABEL = {"1e-5": "05", "1e-10": "10", "1e-20": "20"};

  function dataUrl(path, base) {
    const b = (base || "").replace(/\/+$/, "");
    return b ? `${b}/${path.replace(/^\/+/, "")}` : path;
  }

  function cleanName(s) { return String(s).replace(/,/g, ";"); }

  function tracksForAccession(acc, opts, color, base) {
    const strat = opts.strategy;
    const label = opts.label ? ` ${opts.label}` : "";
    if (strat === "ChIP-Seq" || strat === "ATAC-Seq") {
      const sub = strat === "ChIP-Seq" ? "chipseq" : "atacseq";
      const q = opts.q_cutoff || "1e-10";
      const ql = Q_LABEL[q] || "10";
      return [
        {name: cleanName(`${acc}${label} | signal`), url: dataUrl(`output/${sub}/${acc}/${acc}.bw`, base), format: "bigwig", color},
        {name: cleanName(`${acc}${label} | peaks q<${q}`), url: dataUrl(`output/${sub}/${acc}/${acc}.${ql}.bb`, base), format: "bigbed", color},
      ];
    }
    if (strat === "Bisulfite-Seq") {
      const t = (ctx) => ({name: cleanName(`${acc}${label} | ${ctx} methyl`),
                           url: dataUrl(`output/bsseq/${acc}/${acc}.${ctx}.methyl.bw`, base), format: "bigwig", color});
      return [t("CpG"), t("CHG"), t("CHH"),
              {name: cleanName(`${acc}${label} | CpG HMR`), url: dataUrl(`output/bsseq/${acc}/${acc}.CpG.hmr.bed`, base), format: "bed", color}];
    }
    return [];
  }

  function loadUrl(tracks, opts) {
    const o = opts || {};
    const port = o.port || 60151;
    const enc = (xs) => xs.map(encodeURIComponent).join(",");
    let u = `http://127.0.0.1:${port}/load?genome=${encodeURIComponent(o.genome || DEFAULT_GENOME)}` +
      `&file=${enc(tracks.map(t => t.url))}&name=${enc(tracks.map(t => t.name))}` +
      `&format=${enc(tracks.map(t => t.format))}&merge=true`;
    if (o.locus) u += `&locus=${encodeURIComponent(o.locus)}`;
    return u;
  }

  async function send(tracks, opts) {
    const o = opts || {};
    const f = o.fetchImpl || ((u) => fetch(u));
    const port = o.port || 60151;
    try {
      await f(`http://127.0.0.1:${port}/execute?command=version`);
    } catch (e) {
      return {ok: false, status: "unreachable",
              message: "Could not reach IGV on port " + port + ". Start IGV desktop (Advanced › Enable port), or download the session file instead."};
    }
    let r;
    try {
      r = await f(loadUrl(tracks, {...o, genome: o.genome || (root && root.ZENIGOKE_IGV_GENOME) || DEFAULT_GENOME}));
    } catch (e) {
      return {ok: false, status: "unreachable", message: "IGV stopped responding while loading."};
    }
    const body = r.status === 200 ? String(await r.text()) : "";
    if (body.startsWith("ERROR")) return {ok: false, status: "igv-error", message: body};
    return {ok: true, status: "loaded", message: `Loaded ${tracks.length} tracks in IGV`};
  }

  const api = {DEFAULT_GENOME, Q_LABEL, dataUrl, tracksForAccession, loadUrl, send};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.ZenigokeIGV = api;
})(typeof window !== "undefined" ? window : null);
```

- [ ] **Step 4: Rewire callers**
  - `matrix.js`: delete `dataUrl`, `Q_LABEL`, and the body of `tracksForAccession`; keep `window.tracksForAccession = (acc, sample, color) => ZenigokeIGV.tracksForAccession(acc, sample, color, window.ZENIGOKE_DATA_BASE || "")` for bundle compatibility. `sendToIgv(groups)` becomes:

```js
async function sendToIgv(groups) {
  const btn = document.getElementById("send-igv-btn");
  btn.disabled = true;
  const tracks = buildTracks(groups, "1e-10");
  if (tracks.length === 0) {
    btn.textContent = "No track URLs (pick a cell with experiment_type on either axis)";
    return;
  }
  btn.textContent = "Loading into IGV…";
  const r = await ZenigokeIGV.send(tracks);
  btn.textContent = r.ok ? "✔ " + r.message : "✖ " + r.message;
  btn.disabled = false;
}
```
    and `buildTracks` calls `ZenigokeIGV.tracksForAccession(acc, {strategy: strat, q_cutoff: qCutoff}, color, window.ZENIGOKE_DATA_BASE || "")`. Leave `init();` at the bottom (index.html only loads matrix.js now).
  - `enrichment.js`: `sendOneToIGV`/`sendTopToIGV` build tracks with `ZenigokeIGV.tracksForAccession(...)` and call `ZenigokeIGV.send(tracks)`; show `r.message` in the status area instead of `alert`.
  - `build_catalog_pages.py`: in `render_enrichment_page` replace `<script src='assets/matrix.js' defer></script>` with `<script src='assets/igv.js' defer></script>`; same in `render_bundle_shell` and change its inline sender to `ZenigokeIGV.send(tracks).then(r => { status.textContent = r.message; })`; in `render_index` add `<script src='assets/igv.js' defer></script>` before `matrix.js`. Ensure `write_pages` copies `igv.js` into `report/assets/` if assets are copied from a source dir (check how `matrix.js` is shipped; if it is tracked in place, nothing to copy).
  - `igv_url_helper.py`: peaks URL → `f"{base}/output/{sub}/{acc}/{acc}.{q_label}.bb"`; update `tests/test_api_bundle.py` expectations if they assert `.narrowPeak`.

- [ ] **Step 5: Run tests** — `python3 -m pytest tests -q` and `node tests/js/igv.test.js`.

- [ ] **Step 6: Browser check with a fake IGV** — run `python3 -m http.server 8790 -d report` and a 10-line fake IGV on 60151 (`http.server` subclass that returns 200 `2.19.1` for `/execute`, 204 for `/load`, `Access-Control-Allow-Origin: *`, and logs the request path). Click "Send to IGV" on the matrix page and the enrichment page (headless Firefox via the BiDi helper in the scratchpad is fine). Confirm the logged `/load` has `genome=`, a `/output/` path with exactly one slash after the host, `.bb` peaks, and no console errors on `enrichment.html` (the old `xsel is null`). Stop both servers.

- [ ] **Step 7: Commit** — `git add report/assets/igv.js tests/js tests/test_igv_js.py && git commit -am "igv: shared module; fix URL join, /load args, genome, bigBed peaks; stop matrix init on enrichment/bundle"`

### Task 11: Enrichment blacklist

**Files:**
- Modify: `scripts/api_enrichment.py`
- Test: `tests/test_api_enrichment.py`

**Interfaces:**
- Produces: env `ZENIGOKE_BLACKLIST` (default `<repo>/configs/organelle_like_regions.MpTak_v7.1.bed`); `_blacklist_path() -> Optional[pathlib.Path]`; `_filtered(path: pathlib.Path) -> pathlib.Path` (cached `bedtools intersect -v` result under `ZENIGOKE_ENRICH_CACHE`/`blacklisted/`, keyed by source path + mtime); genome size reduced by blacklist bp.

- [ ] **Step 1: Failing test** — in `tests/test_api_enrichment.py`, make the existing `client` fixture disable the blacklist (`monkeypatch.setenv("ZENIGOKE_BLACKLIST", "")` and `monkeypatch.setenv("ZENIGOKE_ENRICH_CACHE", str(tmp_path / "ecache"))`), then add:

```python
@pytest.fixture
def client_bl(tmp_path, monkeypatch):
    db, peaks_dir, chrom_sizes = _build_fixture(tmp_path)
    bl = tmp_path / "blacklist.bed"
    bl.write_text("chr1\t100\t200\n")          # covers user region 120-180 and part of SRX_A/SRX_B peaks
    for k, v in {"ZENIGOKE_DB_PATH": db, "ZENIGOKE_REPORT_DIR": tmp_path,
                 "ZENIGOKE_BUNDLES_DIR": tmp_path / "bundles", "ZENIGOKE_PEAKS_DIR": peaks_dir,
                 "ZENIGOKE_CHROM_SIZES": chrom_sizes, "ZENIGOKE_BLACKLIST": bl,
                 "ZENIGOKE_ENRICH_CACHE": tmp_path / "ecache"}.items():
        monkeypatch.setenv(k, str(v))
    (tmp_path / "bundles").mkdir()
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def test_enrichment_excludes_blacklisted_regions(client_bl):
    r = client_bl.post("/api/enrichment", json={
        "regions_bed": "chr1\t120\t180\nchr1\t910\t950\n",
        "q_cutoff": "1e-10",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_user_regions"] == 1          # 120-180 is inside the blacklist
    top = body["results"][0]
    assert top["accession"] == "SRX_A"
    assert top["overlap_count"] == 1            # only 910-950 vs SRX_A 900-1100
```

- [ ] **Step 2: Implement** in `scripts/api_enrichment.py`:

```python
def _blacklist_path() -> pathlib.Path | None:
    raw = os.getenv("ZENIGOKE_BLACKLIST")
    if raw is None:
        raw = str(pathlib.Path(__file__).resolve().parent.parent / "configs" / "organelle_like_regions.MpTak_v7.1.bed")
    p = pathlib.Path(raw) if raw else None
    return p if p is not None and p.is_file() else None


def _cache_dir() -> pathlib.Path:
    d = pathlib.Path(os.getenv("ZENIGOKE_ENRICH_CACHE", "/tmp/zenigoke-enrich")) / "blacklisted"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _filtered(path: pathlib.Path) -> pathlib.Path:
    """Return `path` minus blacklist overlaps (cached by path + mtimes)."""
    bl = _blacklist_path()
    if bl is None:
        return path
    key = hashlib.sha256(f"{path}:{path.stat().st_mtime_ns}:{bl}:{bl.stat().st_mtime_ns}".encode()).hexdigest()[:24]
    out = _cache_dir() / f"{key}.bed"
    if not out.exists():
        res = subprocess.run(["bedtools", "intersect", "-v", "-a", str(path), "-b", str(bl)],
                             check=True, capture_output=True, timeout=60)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(res.stdout)
        tmp.replace(out)
    return out


def _blacklist_bp(chrom_sizes: dict[str, int]) -> int:
    """Blacklisted bp clipped to the chromosomes being tested."""
    bl = _blacklist_path()
    if bl is None:
        return 0
    n = 0
    for line in bl.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] in chrom_sizes:
            s, e = max(0, int(parts[1])), min(int(parts[2]), chrom_sizes[parts[0]])
            n += max(0, e - s)
    return n
```

  Then: in `_score_one`, `peak_path = _filtered(peak_path)` right after the existence check (so both `_total_peak_bp` and the `-b` argument use it). In `run_enrichment`, after writing the user BED to its temp file, set `user_bed_path = str(_filtered(pathlib.Path(user_bed_path)))`, recount `n_user` from that file (lines with ≥3 fields), return a 400-style error if it drops to 0, and compute `genome_bp = _genome_bp() - _blacklist_bp(<chrom sizes dict>)` (read the dict from `_chrom_sizes_path()` the same way `_genome_bp` does). Add `str(_blacklist_path())` and its mtime to the string hashed in `_hash_payload`. Import `hashlib` if it is not already imported.

- [ ] **Step 3: Tests pass** — `python3 -m pytest tests/test_api_enrichment.py -v`.

- [ ] **Step 4: Commit** — `git commit -am "enrichment: exclude NUPT/NUMT-like regions from user BED and sample peaks"`

---

## Finishing

### Task 12: Swap in the re-run outputs and rebuild (after Task 5 completes)

- [ ] **Step 1: Check completeness** — `ls /data1/zenigoke/status-v2/*.ok | wc -l` = 157 (or list `.failed` and debug them before continuing). The single-file "PAIRED" runs must have downloaded through the md5-checked path: `for a in $(seq 29617445 29617455); do grep -H "md5 ok:" /data1/zenigoke/logs-v2/SRX$a.log || echo "SRX$a: no md5 line"; done` prints an `md5 ok:` line for each, and `ls /data1/zenigoke/status-v2/SRX29617452.ok` exists.
- [ ] **Step 2: CpG coverage of the v2 BS outputs** (before the rebuild; the catalog never uses the 2026-10-01 TSV coverage for v2 rows):

```bash
PYTHONPATH=/data1/zenigoke/tmp/qc-20261001-bs/pylib \
  python3 scripts/cpg_coverage.py --output-root /data1/zenigoke/output-v2 --out docs/qc/bsseq-v2-cpgcov.tsv
wc -l docs/qc/bsseq-v2-cpgcov.tsv    # 32 (header + 31 BS samples)
```
- [ ] **Step 3: Compare v1 vs v2** — script in scratch: for every accession print v1 displayed rate, v2 alignment rate (col 7 / BS col 6), v2 dup, BS means, and side by side: organelle % (TSV `organelle_pct` / BS `pct_reads_chrC` vs v2 dedup share, ChIP col 20 / col 19, BS col 18), non-conversion (TSV `nonconv_chrC_CHH` vs v2 BS col 19) and CpG ≥5× (TSV `frac_cpg_cov_ge5` vs `docs/qc/bsseq-v2-cpgcov.tsv`). Expected: ChIP/ATAC v2 alignment ≥ QC TSV `aligned_pct` − 2 pt (organelles now map); the dedup organelle share is lower than the TSV value for high-duplication samples (different basis: the flag keeps the TSV value); BS rates close to `sub_R_plus_organelle`; SRX2613745/731 CpG ≈ 0.30/0.43 and much higher CpG coverage. Investigate anything outside ±5 pt that the basis change does not explain.
- [ ] **Step 4: Swap** (reversible)

```bash
cd /data1/zenigoke
mv output output-v1 && mv output-v2 output
mv status status-v1 && mv status-v2 status
mv logs logs-v1 && mv logs-v2 logs
```
- [ ] **Step 5: Rebuild** — `python3 scripts/build-catalog-db.py && python3 scripts/build-catalog-pages.py` (no "no CpG >=5x coverage" warnings); `metrics_source` should read `pipeline+subsample` for all ok rows; re-check flag counts against Step 3 (organelle WARNs must not drop: the flag still uses the TSV basis) and spot-check 5 sample pages, including the QC-card note (it names only the values still taken from the 2026-10-01 investigation) and "removed by blacklist" for peaks in organelle-like regions.
- [ ] **Step 6: Docs** — Methods page (Task 9 text): reference now "MpTak v7.1 + chloroplast AP025455.1 + mitochondrion AP025456.1 (outputs nuclear-only)", ChIP/ATAC "local fork `pipeline-v2-plant.sh`", BS "`abismal -R`"; replace the "Until the 2026-10 re-analysis…" limitation with one stating peaks overlapping 1,278 NUPT/NUMT-like regions are removed. Update `SESSION-HANDOFF.md` (phase table + where things live) and the summary HTML §4 status. (`deploy/aws/02-sync-data.sh` already has no `--size-only`, uploads the blacklist BED and takes `--dryrun` / `--delete`.)
- [ ] **Step 7: Commit** — `git add docs/qc/bsseq-v2-cpgcov.tsv && git commit -am "rerun: swap in organelle-aware outputs; docs"`

### Task 13: Deploy (only after the owner confirms)

- [ ] **Sync, dry run first** (profile `chiba-dsc`): `bash deploy/aws/02-sync-data.sh --dryrun --delete` and read the delete list (expected: only v1 files the v2 run no longer writes, e.g. `.NN.bb` / `.05_peaks.narrowPeak` of inputs/IgGs whose peaks were all blacklisted); then `bash deploy/aws/02-sync-data.sh --delete` (~40 GB) — uploads outputs, DB and the blacklist BED. The DB must be on S3 before the merge: the Pages workflow builds from it.
- [ ] **Local check before merging** (merging deploys Pages): the owner opens a local build (`python3 scripts/server.py`) and sends a matrix selection and an enrichment top-10 to real IGV desktop; tracks load on the GenArk genome with "q < 1e-10" peak track names.
- [ ] Merge `prelaunch-fixes` → `main` and push (Pages workflow rebuilds from the S3 DB).
- [ ] **EC2** (`zenigoke.inutano.com`): re-run `bash deploy/aws/04-ec2-bootstrap.sh` — pulls the code, mirrors peaks with `--delete`, refreshes `db/kknmsmd.db`, creates `/var/cache/zenigoke-enrich` and sets `ZENIGOKE_ENRICH_CACHE`, restarts `zenigoke.service`. Verify `POST /api/enrichment` on a known BED (response includes `n_user_regions_excluded`).
- [ ] Verify live: a sample page shows the QC card; matrix "Send to IGV" against real IGV desktop loads tracks on the GenArk genome (owner checks, since IGV desktop is not available here).
