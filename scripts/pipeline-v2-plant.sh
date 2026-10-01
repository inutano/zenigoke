#!/bin/bash
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
#   4. stats.tsv gains cols 16-21 (see Step 4 below).
#
# Upstream description follows (usage updated for --blacklist).
#
# ChIP-Atlas Pipeline v2: Production pipeline
#
# Runs entirely inside one container with all tools. Optimized for throughput:
#   1. Pipe-through: fastp → bwa-mem2 → collate → fixmate → sort → markdup
#   2. All intermediates on local TMPDIR (NVMe) — only final outputs to Lustre
#   3. Single MACS3 + awk filter for 3 q-value thresholds
#   4. No intermediate files on shared filesystem, no container restart overhead
#
# Container: ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0
#
# Usage:
#   apptainer exec --bind /data1/tmp:/tmp pipeline-v2.sif bash pipeline-v2.sh \
#     --sample-id SRX12345678 \
#     --fastq-fwd reads_1.fastq \
#     [--fastq-rev reads_2.fastq] \
#     --genome-fasta hg38.fa \
#     --chrom-sizes chrom.sizes \
#     --genome-size hs \
#     --outdir ./output \
#     [--threads 8] \
#     [--blacklist regions.bed]
#
set -eo pipefail

# ============================================================
# Parse arguments
# ============================================================
THREADS=8

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample-id)    SAMPLE_ID="$2"; shift 2 ;;
    --fastq-fwd)    FASTQ_FWD="$2"; shift 2 ;;
    --fastq-rev)    FASTQ_REV="$2"; shift 2 ;;
    --genome-fasta) GENOME_FA="$2"; shift 2 ;;
    --chrom-sizes)  CHROM_SIZES="$2"; shift 2 ;;
    --genome-size)  GENOME_SIZE="$2"; shift 2 ;;
    --outdir)       OUTDIR="$2"; shift 2 ;;
    --threads)      THREADS="$2"; shift 2 ;;
    --blacklist)    BLACKLIST="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

for var in SAMPLE_ID FASTQ_FWD GENOME_FA CHROM_SIZES GENOME_SIZE OUTDIR; do
  if [ -z "${!var}" ]; then
    echo "ERROR: --$(echo $var | tr '_' '-' | tr '[:upper:]' '[:lower:]') is required"
    exit 1
  fi
done

mkdir -p "$OUTDIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ============================================================
# Working directory — use local NVMe when available
# ============================================================
# All intermediates (dedup BAM, BedGraph, samtools temps, logs) go here.
# Only final outputs (BigWig, peaks, BigBed, fastp JSON) are copied to $OUTDIR.
WORK="${TMPDIR:-/tmp}/${SAMPLE_ID}_$$"
mkdir -p "$WORK"

# ============================================================
# Thread allocation
# ============================================================
# bwa-mem2 is the CPU bottleneck — give it almost all threads.
# collate/fixmate/markdup are I/O-bound, single-threaded is fine.
# samtools sort needs a few threads for merge.
FASTP_T=2
ALIGN_T=$((THREADS > 3 ? THREADS - 1 : 2))
SORT_T=$((THREADS > 8 ? 3 : 2))
SORT_MEM="4G"

# ============================================================
# Step 1: Piped alignment
# ============================================================
DEDUP_BAM="$WORK/${SAMPLE_ID}.dedup.bam"
RG="@RG\\tID:${SAMPLE_ID}\\tSM:${SAMPLE_ID}\\tPL:ILLUMINA"

# Detect paired-end vs single-end
if [ -n "$FASTQ_REV" ] && [ -e "$FASTQ_REV" ]; then
  IS_PAIRED=true
else
  IS_PAIRED=false
fi

STEP1_START=$(date +%s)

if [ "$IS_PAIRED" = true ]; then
  log "Step 1: fastp → bwa-mem2 -p → collate → fixmate → sort → markdup (paired-end)"
  fastp --in1 "$FASTQ_FWD" --in2 "$FASTQ_REV" \
      --stdout --json "$WORK/${SAMPLE_ID}_fastp.json" --thread $FASTP_T \
      2>"$WORK/fastp.stderr" \
    | bwa-mem2 mem -t "$ALIGN_T" -R "$RG" -p "$GENOME_FA" - 2>"$WORK/bwamem2.stderr" \
    | samtools collate -O -T "$WORK/collate" - \
    | samtools fixmate -m - - \
    | samtools sort -@ "$SORT_T" -m "$SORT_MEM" -T "$WORK/sort" - \
    | samtools markdup -r -f "$WORK/markdup.stats" - "$DEDUP_BAM"
else
  log "Step 1: fastp → bwa-mem2 → sort → markdup (single-end)"
  fastp --in1 "$FASTQ_FWD" \
      --stdout --json "$WORK/${SAMPLE_ID}_fastp.json" --thread $FASTP_T \
      2>"$WORK/fastp.stderr" \
    | bwa-mem2 mem -t "$ALIGN_T" -R "$RG" "$GENOME_FA" - 2>"$WORK/bwamem2.stderr" \
    | samtools sort -@ "$SORT_T" -m "$SORT_MEM" -T "$WORK/sort" - \
    | samtools markdup -r -f "$WORK/markdup.stats" - "$DEDUP_BAM"
fi

STEP1_END=$(date +%s)
log "Step 1 done: $((STEP1_END - STEP1_START))s"

# Capture dedup BAM size for stats (before any step deletes it)
DEDUP_BAM_SIZE=$(wc -c < "$DEDUP_BAM" 2>/dev/null || echo 0)

# ============================================================
# Step 2: BigWig + MACS3 in parallel (both read dedup BAM from NVMe)
# ============================================================
log "Step 2: BigWig + MACS3 (parallel)"
STEP2_START=$(date +%s)

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

# BigWig via bedtools genomecov → RPM normalize → bedGraphToBigWig
# BAM is coordinate-sorted → output is already sorted → no re-sort needed
BEDGRAPH="$WORK/${SAMPLE_ID}.bedGraph"
(bedtools genomecov -bg -ibam "$NUC_BAM" \
  | awk -v total="$MAPPED" -v OFS='\t' '{print $1, $2, $3, $4*1000000/total}' \
  > "$BEDGRAPH" \
  && wc -c < "$BEDGRAPH" > "$WORK/.bedgraph_size" \
  && bedGraphToBigWig "$BEDGRAPH" "$CHROM_SIZES" "$OUTDIR/${SAMPLE_ID}.bw" \
  && rm -f "$BEDGRAPH") &
PID_BIGWIG=$!

# MACS3 peak calling — use BAMPE for paired-end, BAM for single-end
if [ "$IS_PAIRED" = true ]; then
  MACS3_FMT="BAMPE"
else
  MACS3_FMT="BAM"
fi

macs3 callpeak \
  -t "$NUC_BAM" -n "${SAMPLE_ID}" -g "$GENOME_SIZE" \
  -q 1e-05 -f "$MACS3_FMT" --outdir "$WORK" \
  2>"$WORK/macs3.stderr" || true

wait $PID_BIGWIG || log "WARNING: BigWig generation failed"

STEP2_END=$(date +%s)
log "Step 2 done: $((STEP2_END - STEP2_START))s"

# ============================================================
# Step 3: Filter peaks + BigBed conversion
# ============================================================
log "Step 3: Filter peaks + BigBed"
PEAKS_05="$WORK/${SAMPLE_ID}_peaks.narrowPeak"

if [ -f "$PEAKS_05" ]; then
  mv "$PEAKS_05" "$OUTDIR/${SAMPLE_ID}.05_peaks.narrowPeak"
  mv "$WORK/${SAMPLE_ID}_peaks.xls" "$OUTDIR/${SAMPLE_ID}.05_peaks.xls" 2>/dev/null || true
  PEAKS_05="$OUTDIR/${SAMPLE_ID}.05_peaks.narrowPeak"
  if [ -n "$BLACKLIST" ] && [ -s "$BLACKLIST" ]; then
    N_BEFORE=$(wc -l < "$PEAKS_05")
    bedtools intersect -v -a "$PEAKS_05" -b "$BLACKLIST" > "$WORK/.peaks05.filtered"
    mv "$WORK/.peaks05.filtered" "$PEAKS_05"
    log "  Blacklist: removed $((N_BEFORE - $(wc -l < "$PEAKS_05"))) of $N_BEFORE q05 peaks"
  fi

  awk '$9 >= 10' "$PEAKS_05" > "$OUTDIR/${SAMPLE_ID}.10_peaks.narrowPeak"
  awk '$9 >= 20' "$PEAKS_05" > "$OUTDIR/${SAMPLE_ID}.20_peaks.narrowPeak"

  for q in 05 10 20; do
    BED="$OUTDIR/${SAMPLE_ID}.${q}_peaks.narrowPeak"
    if [ -s "$BED" ]; then
      (cut -f1-4 "$BED" | sort -k1,1 -k2,2n > "$WORK/.tmp_${q}.bed" \
        && bedToBigBed "$WORK/.tmp_${q}.bed" "$CHROM_SIZES" "$OUTDIR/${SAMPLE_ID}.${q}.bb" \
        && rm -f "$WORK/.tmp_${q}.bed") &
    fi
  done
  wait

  log "Peaks: q05=$(wc -l < "$PEAKS_05") q10=$(wc -l < "$OUTDIR/${SAMPLE_ID}.10_peaks.narrowPeak") q20=$(wc -l < "$OUTDIR/${SAMPLE_ID}.20_peaks.narrowPeak")"
else
  log "No peaks found (MACS3 model building may have failed — expected for low-signal samples)"
fi

# ============================================================
# Step 4: Statistics TSV (v1-compatible cols 1-15 + zenigoke cols 16-21)
# ============================================================
log "Step 4: Writing statistics TSV"

FASTP_JSON="$WORK/${SAMPLE_ID}_fastp.json"

# Read counts from fastp JSON
READS_BEFORE=$(python3 -c "import json; d=json.load(open('$FASTP_JSON')); print(d['summary']['before_filtering']['total_reads'])" 2>/dev/null || echo 0)
READS_AFTER=$(python3 -c "import json; d=json.load(open('$FASTP_JSON')); print(d['summary']['after_filtering']['total_reads'])" 2>/dev/null || echo 0)

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

# File sizes
FASTQ_SIZE=$(wc -c < "$FASTQ_FWD" 2>/dev/null || echo 0)
[ "$IS_PAIRED" = true ] && [ -e "$FASTQ_REV" ] && FASTQ_SIZE=$((FASTQ_SIZE + $(wc -c < "$FASTQ_REV")))
BW_SIZE=$(wc -c < "$OUTDIR/${SAMPLE_ID}.bw" 2>/dev/null || echo 0)
BEDGRAPH_SIZE=$(cat "$WORK/.bedgraph_size" 2>/dev/null || echo 0)

# Peak counts
PEAKS_05_N=$(wc -l < "$OUTDIR/${SAMPLE_ID}.05_peaks.narrowPeak" 2>/dev/null || echo 0)
PEAKS_10_N=$(wc -l < "$OUTDIR/${SAMPLE_ID}.10_peaks.narrowPeak" 2>/dev/null || echo 0)
PEAKS_20_N=$(wc -l < "$OUTDIR/${SAMPLE_ID}.20_peaks.narrowPeak" 2>/dev/null || echo 0)

# Elapsed time in minutes
TOTAL_MIN=$(awk "BEGIN {printf \"%.2f\", ($(date +%s) - $STEP1_START) / 60}")

# SE=0, PE=1
PE_FLAG=0
[ "$IS_PAIRED" = true ] && PE_FLAG=1

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$SAMPLE_ID" \
  "$PE_FLAG" \
  "$FASTQ_SIZE" \
  "0" \
  "$READS_BEFORE" \
  "$READS_AFTER" \
  "$MAP_RATE" \
  "$DUP_RATE" \
  "$DEDUP_BAM_SIZE" \
  "$BEDGRAPH_SIZE" \
  "$BW_SIZE" \
  "$PEAKS_05_N" \
  "$PEAKS_10_N" \
  "$PEAKS_20_N" \
  "$TOTAL_MIN" \
  "zenigoke-v2" "$ALIGNED" "$DUPS" "$MAPPED_ALL" "$ORG_USABLE" "$MAPPED" \
  > "$OUTDIR/${SAMPLE_ID}.stats.tsv"

log "Stats: reads_before=$READS_BEFORE reads_after=$READS_AFTER mapped=$MAPPED aligned=$ALIGNED dup_rate=${DUP_RATE}% map_rate=${MAP_RATE}% peaks_q05=$PEAKS_05_N time=${TOTAL_MIN}m"

# ============================================================
# Move fastp report to final output, cleanup work dir
# ============================================================
mv "$WORK/${SAMPLE_ID}_fastp.json" "$OUTDIR/" 2>/dev/null || true
rm -rf "$WORK"

TOTAL=$(($(date +%s) - STEP1_START))
log "Pipeline complete: ${TOTAL}s ($(( TOTAL / 60 ))m)"
log "Output: $OUTDIR/"
