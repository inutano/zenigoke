#!/bin/bash
#
# Zenigoke plant-aware BS-seq pipeline
#
# FORK of: chip-atlas-pipeline-v2/scripts/pipeline-v2-bs.sh
# Fork date: 2026-04-24
#
# Changes from upstream (two functional, one output extension):
#   1. dnmtools counts: -cpg-only flag removed → counts.tsv covers CpG + CHG + CHH.
#   2. Step 3 replaced: per-context fan-out instead of single-context parallel calls.
#      CpG: sym → hmr + hypermr + pmd + BigWig pair
#      CHG: hmr + hypermr + BigWig pair (no sym; per-strand counts feed directly)
#      CHH: BigWig pair only (HMR/hyperMR unreliable at plant-typical low coverage)
#   3. Stats TSV: three new columns appended (mean_cpg, mean_chg, mean_chh) —
#      arithmetic mean of meth_fraction (col 5) across sites with coverage > 0.
#
# Everything else (container, variable names, fastp/fifo logic, Steps 0-2) is
# preserved verbatim from upstream.
#
# Runs entirely inside one container. Optimized for throughput:
#   1. Single-container execution (no per-step docker startup overhead)
#   2. All intermediates on local TMPDIR (NVMe), deleted as consumed
#   3. Parallel region calling per context: fan-out within each context
#
# Note: dnmtools format/uniq use htslib, which requires seekable BAM files,
# so step 1 uses file-based intermediates rather than a Unix pipe. The big
# wins are NVMe locality, container reuse, and the parallel step 3 fan-out.
#
# Container: ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.0.0
#
# Usage:
#   apptainer exec --bind /data1/tmp:/tmp pipeline-v2-bs.sif bash pipeline-v2-bs-plant.sh \
#     --sample-id SRX12345678 \
#     --fastq-fwd reads_1.fastq.gz \
#     [--fastq-rev reads_2.fastq.gz] \
#     --genome MpTak_v7.1 \
#     --genome-fasta MpTak_v7.1.fa \
#     --abismal-index MpTak_v7.1.abismal.idx \
#     --chrom-sizes MpTak_v7.1.chrom.sizes \
#     --outdir ./output \
#     [--threads 16]
#
set -eo pipefail

# ============================================================
# Parse arguments
# ============================================================
THREADS=16

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample-id)     SAMPLE_ID="$2"; shift 2 ;;
    --fastq-fwd)     FASTQ_FWD="$2"; shift 2 ;;
    --fastq-rev)     FASTQ_REV="$2"; shift 2 ;;
    --genome)        GENOME="$2"; shift 2 ;;
    --genome-fasta)  GENOME_FA="$2"; shift 2 ;;
    --abismal-index) ABISMAL_IDX="$2"; shift 2 ;;
    --chrom-sizes)   CHROM_SIZES="$2"; shift 2 ;;
    --outdir)        OUTDIR="$2"; shift 2 ;;
    --threads)       THREADS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

for var in SAMPLE_ID FASTQ_FWD GENOME GENOME_FA ABISMAL_IDX CHROM_SIZES OUTDIR; do
  if [ -z "${!var}" ]; then
    echo "ERROR: --$(echo $var | tr '_' '-' | tr '[:upper:]' '[:lower:]') is required"
    exit 1
  fi
done

mkdir -p "$OUTDIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ============================================================
# Working directory — local NVMe when available
# ============================================================
WORK="${TMPDIR:-/tmp}/${SAMPLE_ID}_$$"
mkdir -p "$WORK"

# ============================================================
# Detect SE/PE and set flags
# ============================================================
IS_PAIRED=false
FORMAT_SE_FLAG=""
LAYOUT_FLAG=0
if [ -n "$FASTQ_REV" ] && [ -e "$FASTQ_REV" ]; then
  IS_PAIRED=true
  LAYOUT_FLAG=1
else
  FORMAT_SE_FLAG="-single-end"
fi

# ============================================================
# Thread allocation
# ============================================================
# Every step that scales with threads gets all of them. samtools sort and
# the dnmtools tools all use thread pools internally.
SORT_MEM="2G"

# ============================================================
# CpG count lookup for coverage calculation
# ============================================================
declare -A CPG_COUNTS=(
  [hg38]=61959486
  [mm10]=43816016
  [rn6]=53698106
  [ce11]=6263050
  [dm6]=11787346
  [sacCer3]=710598
)
CPG_COUNT="${CPG_COUNTS[$GENOME]:-0}"
if [ "$CPG_COUNT" -eq 0 ]; then
  log "WARNING: Unknown genome '$GENOME', CpG coverage will be 0"
fi

# ============================================================
# FASTP output paths
# ============================================================
FASTP_JSON="$WORK/fastp.json"

# ============================================================
# Step 0: fastp QC/trimming
# ============================================================
# PE: fastp writes trimmed FASTQs to NVMe scratch (two output streams
#     can't be piped through a single process substitution).
# SE: abismal reads directly from process substitution (no disk write).
log "Step 0: fastp QC/trimming"
T0=$(date +%s)

if [ "$IS_PAIRED" = true ]; then
  fastp --in1 "$FASTQ_FWD" --in2 "$FASTQ_REV" \
    --out1 "$WORK/trim_1.fq.gz" --out2 "$WORK/trim_2.fq.gz" \
    --json "$FASTP_JSON" --thread 2 2>"$WORK/fastp.stderr"
  FASTQ_ARGS=("$WORK/trim_1.fq.gz" "$WORK/trim_2.fq.gz")
  TRIM_CLEANUP=true
  log "  fastp: $(($(date +%s) - T0))s (PE, trimmed to NVMe)"
else
  # SE: fastp streams directly into abismal via process substitution
  # FASTQ_ARGS is set as a single-element array; the actual process
  # substitution is constructed at the abismal invocation below.
  TRIM_CLEANUP=false
  log "  fastp: will stream via process substitution (SE)"
fi

# ============================================================
# Step 1: Sequential alignment + format + sort + dedup
# ============================================================
# Each intermediate lives on local NVMe ($WORK) and is deleted as soon as
# the next step has consumed it. This keeps peak disk usage low and avoids
# Lustre I/O entirely.
log "Step 1: abismal → format → sort → uniq → dedup BAM"
DEDUP_BAM="$WORK/${SAMPLE_ID}.dedup.bam"

STEP1_START=$(date +%s)

# 1a. abismal alignment
T0=$(date +%s)
if [ "$IS_PAIRED" = true ]; then
  # PE: read trimmed FASTQs from NVMe scratch
  dnmtools abismal \
      -i "$ABISMAL_IDX" \
      -t "$THREADS" \
      -B \
      -o "$WORK/mapped.bam" \
      -s "$WORK/abismal.stats" \
      "${FASTQ_ARGS[@]}" 2>"$WORK/abismal.stderr"
  rm -f "$WORK"/trim*.fq.gz
else
  # SE: fastp streams directly into abismal via process substitution
  dnmtools abismal \
      -i "$ABISMAL_IDX" \
      -t "$THREADS" \
      -B \
      -o "$WORK/mapped.bam" \
      -s "$WORK/abismal.stats" \
      <(fastp --in1 "$FASTQ_FWD" --stdout \
          --json "$FASTP_JSON" --thread 2 2>"$WORK/fastp.stderr") \
      2>"$WORK/abismal.stderr"
fi
log "  abismal: $(($(date +%s) - T0))s"

STEP0_END=$(date +%s)
log "Step 0+1a done: fastp+abismal $((STEP0_END - STEP0_START))s"

# 1b. dnmtools format → drop mapped.bam
T0=$(date +%s)
dnmtools format \
    -t "$THREADS" \
    -f abismal \
    -B \
    $FORMAT_SE_FLAG \
    "$WORK/mapped.bam" \
    "$WORK/formatted.bam" 2>"$WORK/format.stderr"
rm -f "$WORK/mapped.bam"
log "  format:  $(($(date +%s) - T0))s"

# 1c. samtools sort → drop formatted.bam
T0=$(date +%s)
samtools sort \
    -@ "$THREADS" \
    -m "$SORT_MEM" \
    -T "$WORK/sort" \
    -o "$WORK/sorted.bam" \
    "$WORK/formatted.bam" 2>"$WORK/sort.stderr"
rm -f "$WORK/formatted.bam"
log "  sort:    $(($(date +%s) - T0))s"

# 1d. dnmtools uniq (dedup) → drop sorted.bam
T0=$(date +%s)
dnmtools uniq \
    -t "$THREADS" \
    "$WORK/sorted.bam" \
    "$DEDUP_BAM" 2>"$WORK/uniq.stderr"
rm -f "$WORK/sorted.bam"
log "  uniq:    $(($(date +%s) - T0))s"

STEP1_END=$(date +%s)
log "Step 1 done: $((STEP1_END - STEP1_START))s"

# ============================================================
# Step 2: Per-CpG methylation counts
# ============================================================
log "Step 2: dnmtools counts"
STEP2_START=$(date +%s)

dnmtools counts \
    -t "$THREADS" \
    -c "$GENOME_FA" \
    "$DEDUP_BAM" \
  | awk -F'\t' -v OFS='\t' '$6 > 0' > "$WORK/counts.tsv"

# Capture dedup BAM size for stats, then delete
DEDUP_BAM_SIZE=$(wc -c < "$DEDUP_BAM" 2>/dev/null || echo 0)
rm -f "$DEDUP_BAM"

STEP2_END=$(date +%s)
log "Step 2 done: $((STEP2_END - STEP2_START))s"

# ============================================================
# Step 3: Per-context fan-out (plant fork — replaces upstream Step 3)
#
# Context classification (dnmtools 1.5.1): column 4 is the methylation context.
# Verified empirically against DRX162964 (Marchantia BS-seq, 2026-04-25):
#   CpG → "CpG"   CHG → "CXG"   CHH → "CHH"
# (note CXG, not CHG — dnmtools' 3-letter encoding for the H-context on the +
# strand of CHG sites; the symmetric pair appears as CXG on both strands.)
# Output filenames use the user-facing CHG label.
# ============================================================
log "Step 3 (plant): split by context, per-context hmr/hypermr/pmd/BigWig"
STEP3_START=$(date +%s)

# Split counts.tsv into per-context files (all three must exist before parallel jobs)
awk -F'\t' '$4 == "CpG"' "$WORK/counts.tsv" > "$WORK/counts.CpG.tsv"
awk -F'\t' '$4 == "CXG"' "$WORK/counts.tsv" > "$WORK/counts.CHG.tsv"
awk -F'\t' '$4 == "CHH"' "$WORK/counts.tsv" > "$WORK/counts.CHH.tsv"
log "  context split: CpG=$(wc -l < "$WORK/counts.CpG.tsv") CHG=$(wc -l < "$WORK/counts.CHG.tsv") CHH=$(wc -l < "$WORK/counts.CHH.tsv") sites"

# ---- CpG: sym → hmr + hypermr + pmd + BigWig (parallel) ----
# CpG requires symmetric (strand-merged) counts for HMR/PMD calls.
dnmtools sym -t 2 -o "$WORK/counts.CpG.sym.tsv" "$WORK/counts.CpG.tsv" \
  2>"$WORK/sym.CpG.stderr"

(dnmtools hmr \
    -o "$OUTDIR/${SAMPLE_ID}.CpG.hmr.bed" \
    "$WORK/counts.CpG.sym.tsv" \
    2>"$WORK/hmr.CpG.stderr") &
PID_CpG_HMR=$!

(dnmtools hypermr \
    -o "$OUTDIR/${SAMPLE_ID}.CpG.hypermr.bed" \
    "$WORK/counts.CpG.sym.tsv" \
    2>"$WORK/hypermr.CpG.stderr") &
PID_CpG_HYPERMR=$!

(dnmtools pmd \
    -o "$OUTDIR/${SAMPLE_ID}.CpG.pmd.bed" \
    "$WORK/counts.CpG.sym.tsv" \
    2>"$WORK/pmd.CpG.stderr") &
PID_CpG_PMD=$!

(
  sort -k1,1 -k2,2n "$WORK/counts.CpG.tsv" \
    | awk -F'\t' -v OFS='\t' \
          -v M="$WORK/CpG.methyl.bg" -v C="$WORK/CpG.cover.bg" '{
        print $1, $2, $2+1, $5 > M
        print $1, $2, $2+1, $6 > C
      }'
  bedGraphToBigWig "$WORK/CpG.methyl.bg" "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CpG.methyl.bw" &
  bedGraphToBigWig "$WORK/CpG.cover.bg"  "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CpG.cover.bw" &
  wait
) &
PID_CpG_BIGWIG=$!

# ---- CHG: hmr + hypermr + BigWig (parallel; no sym, no pmd) ----
# CHG per-strand counts feed directly into hmr/hypermr.
(dnmtools hmr \
    -o "$OUTDIR/${SAMPLE_ID}.CHG.hmr.bed" \
    "$WORK/counts.CHG.tsv" \
    2>"$WORK/hmr.CHG.stderr") &
PID_CHG_HMR=$!

(dnmtools hypermr \
    -o "$OUTDIR/${SAMPLE_ID}.CHG.hypermr.bed" \
    "$WORK/counts.CHG.tsv" \
    2>"$WORK/hypermr.CHG.stderr") &
PID_CHG_HYPERMR=$!

(
  sort -k1,1 -k2,2n "$WORK/counts.CHG.tsv" \
    | awk -F'\t' -v OFS='\t' \
          -v M="$WORK/CHG.methyl.bg" -v C="$WORK/CHG.cover.bg" '{
        print $1, $2, $2+1, $5 > M
        print $1, $2, $2+1, $6 > C
      }'
  bedGraphToBigWig "$WORK/CHG.methyl.bg" "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CHG.methyl.bw" &
  bedGraphToBigWig "$WORK/CHG.cover.bg"  "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CHG.cover.bw" &
  wait
) &
PID_CHG_BIGWIG=$!

# ---- CHH: BigWig pair only (HMR/hyperMR unreliable at plant-typical low CHH coverage) ----
(
  sort -k1,1 -k2,2n "$WORK/counts.CHH.tsv" \
    | awk -F'\t' -v OFS='\t' \
          -v M="$WORK/CHH.methyl.bg" -v C="$WORK/CHH.cover.bg" '{
        print $1, $2, $2+1, $5 > M
        print $1, $2, $2+1, $6 > C
      }'
  bedGraphToBigWig "$WORK/CHH.methyl.bg" "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CHH.methyl.bw" &
  bedGraphToBigWig "$WORK/CHH.cover.bg"  "$CHROM_SIZES" \
    "$OUTDIR/${SAMPLE_ID}.CHH.cover.bw" &
  wait
) &
PID_CHH_BIGWIG=$!

# Wait for all parallel jobs
wait $PID_CpG_HMR     || log "WARNING: CpG hmr failed"
wait $PID_CpG_HYPERMR || log "WARNING: CpG hypermr failed"
wait $PID_CpG_PMD     || log "WARNING: CpG pmd failed"
wait $PID_CpG_BIGWIG  || log "WARNING: CpG BigWig failed"
wait $PID_CHG_HMR     || log "WARNING: CHG hmr failed"
wait $PID_CHG_HYPERMR || log "WARNING: CHG hypermr failed"
wait $PID_CHG_BIGWIG  || log "WARNING: CHG BigWig failed"
wait $PID_CHH_BIGWIG  || log "WARNING: CHH BigWig failed"

STEP3_END=$(date +%s)
log "Step 3 done: $((STEP3_END - STEP3_START))s"

# ============================================================
# Move diagnostic files to output and cleanup
# ============================================================
cp "$WORK/abismal.stats" "$OUTDIR/${SAMPLE_ID}.abismal.stats" 2>/dev/null || true
cp "$FASTP_JSON" "$OUTDIR/${SAMPLE_ID}_fastp.json" 2>/dev/null || true

# ============================================================
# Collect statistics for v1-compatible stats TSV
# ============================================================
TOTAL_MIN=$(( ($(date +%s) - STEP1_START) / 60 ))

# Read count and mapping rate from abismal stats YAML
if [ "$IS_PAIRED" = true ]; then
  READ_COUNT=$(grep "total_pairs:" "$WORK/abismal.stats" | head -1 | awk '{print $2}')
else
  READ_COUNT=$(grep "total_reads:" "$WORK/abismal.stats" | head -1 | awk '{print $2}')
fi
MAP_RATE=$(grep "percent_mapped:" "$WORK/abismal.stats" | head -1 | awk '{print $2}')

# Methylation rate and CpG coverage from CpG-context sites only
# (counts.tsv now includes CHG+CHH; CpG.tsv is pre-filtered)
METH_STATS=$(awk -F'\t' -v cpg="$CPG_COUNT" '{
  met += $6 * $5; total += $6
} END {
  if (total > 0) printf "%.1f\t%.1f", met/total*100, total/cpg
  else printf "0.0\t0.0"
}' "$WORK/counts.CpG.tsv")
METH_RATE=$(echo "$METH_STATS" | cut -f1)
CPG_COVERAGE=$(echo "$METH_STATS" | cut -f2)

# Region counts (CpG context only — matches upstream column semantics)
HMR_N=0; PMD_N=0; HYPERMR_N=0
[ -f "$OUTDIR/${SAMPLE_ID}.CpG.hmr.bed" ]     && HMR_N=$(wc -l     < "$OUTDIR/${SAMPLE_ID}.CpG.hmr.bed")
[ -f "$OUTDIR/${SAMPLE_ID}.CpG.pmd.bed" ]     && PMD_N=$(wc -l     < "$OUTDIR/${SAMPLE_ID}.CpG.pmd.bed")
[ -f "$OUTDIR/${SAMPLE_ID}.CpG.hypermr.bed" ] && HYPERMR_N=$(wc -l < "$OUTDIR/${SAMPLE_ID}.CpG.hypermr.bed")

# Per-context mean methylation (arithmetic mean of meth_fraction col 5,
# computed from context-split files which already have coverage > 0 filter).
mean_meth() {
  awk -F'\t' 'NR > 0 {s += $5; n++} END {if (n > 0) printf "%.4f", s/n; else print "NA"}' "$1"
}
MEAN_CPG=$(mean_meth "$WORK/counts.CpG.tsv")
MEAN_CHG=$(mean_meth "$WORK/counts.CHG.tsv")
MEAN_CHH=$(mean_meth "$WORK/counts.CHH.tsv")

# FASTQ file size (bytes), sum for PE
FASTQ_SIZE=$(du -sb "$FASTQ_FWD" ${FASTQ_REV:+"$FASTQ_REV"} 2>/dev/null | awk '{s+=$1} END {print s+0}')

# Write stats TSV: 15 contiguous columns matching the report generator's key list.
# Cols 1-11:  sample layout fastq_size dedup_bam_size read_count mapping_rate
#             meth_rate cpg_coverage hmr_count pmd_count hypermr_count
# Col  12:    elapsed_min
# Cols 13-15: mean_cpg mean_chg mean_chh  (plant-fork additions; replace the
#             three empty padding cols that upstream BS-seq leaves here)
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$SAMPLE_ID" "$LAYOUT_FLAG" "$FASTQ_SIZE" "$DEDUP_BAM_SIZE" \
  "$READ_COUNT" "$MAP_RATE" "$METH_RATE" "$CPG_COVERAGE" \
  "$HMR_N" "$PMD_N" "$HYPERMR_N" "$TOTAL_MIN" \
  "$MEAN_CPG" "$MEAN_CHG" "$MEAN_CHH" \
  > "$OUTDIR/${SAMPLE_ID}.stats.tsv"

# Report summary
echo ""
echo "=== Pipeline v2 BS-seq (plant): $SAMPLE_ID ==="
if [ -f "$WORK/abismal.stats" ]; then
  grep "percent_mapped" "$WORK/abismal.stats" | head -1
fi
echo "Sites with coverage: $(wc -l < "$WORK/counts.tsv") total  CpG=$(wc -l < "$WORK/counts.CpG.tsv")  CHG=$(wc -l < "$WORK/counts.CHG.tsv")  CHH=$(wc -l < "$WORK/counts.CHH.tsv")"
echo "Mean methylation:    CpG=$MEAN_CPG  CHG=$MEAN_CHG  CHH=$MEAN_CHH"
for ctx in CpG CHG; do
  for tool in hmr hypermr pmd; do
    f="$OUTDIR/${SAMPLE_ID}.${ctx}.${tool}.bed"
    [ -f "$f" ] && echo "  ${ctx}.${tool} regions: $(wc -l < "$f")"
  done
done

rm -rf "$WORK"

TOTAL=$(($(date +%s) - STEP1_START))
log "Pipeline complete: ${TOTAL}s ($(( TOTAL / 60 ))m)"
log "Output: $OUTDIR/"
