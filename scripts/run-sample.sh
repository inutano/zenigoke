#!/usr/bin/env bash
# Process a single SRA experiment: download FASTQ, dispatch to the right
# pipeline, record status.
#
# Usage: run-sample.sh <accession> <library_strategy>
#   library_strategy ∈ {ChIP-Seq, ATAC-Seq, Bisulfite-Seq}
set -euo pipefail

ACCESSION="${1:?need accession}"
STRATEGY="${2:?need library_strategy}"

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="$DATA_ROOT/references/MpTak_v7.1"
PIPELINES_REPO="${PIPELINES_REPO:-$HOME/repos/chip-atlas-pipeline-v2}"
ZENIGOKE_REPO="${ZENIGOKE_REPO:-$HOME/work/zenigoke}"
THREADS="${THREADS:-32}"

CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"

FASTQ_DIR="$DATA_ROOT/fastq/$ACCESSION"
TMP_DIR="$DATA_ROOT/tmp/$ACCESSION"
LOG="$DATA_ROOT/logs/$ACCESSION.log"
STATUS_DIR="$DATA_ROOT/status"

mkdir -p "$FASTQ_DIR" "$TMP_DIR" "$STATUS_DIR" "$(dirname "$LOG")"

cleanup() {
  local rc=$?
  rm -rf "$TMP_DIR"
  if [ $rc -eq 0 ]; then
    rm -rf "$FASTQ_DIR"
    : > "$STATUS_DIR/$ACCESSION.ok"
    rm -f "$STATUS_DIR/$ACCESSION.failed"
  else
    {
      echo "exit_code=$rc"
      echo "strategy=$STRATEGY"
      echo "--- last 50 lines of log ---"
      tail -n 50 "$LOG" 2>/dev/null || echo "(no log)"
    } > "$STATUS_DIR/$ACCESSION.failed"
  fi
}
trap cleanup EXIT

exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Iseconds) start $ACCESSION ($STRATEGY) ==="

# --- FASTQ download ---
RUN_ACCS=$(curl -fsSL \
  "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${ACCESSION}&result=read_run&fields=run_accession&format=tsv" \
  | awk 'NR>1 {print $1}')
[ -z "$RUN_ACCS" ] && { echo "ERROR: no runs for $ACCESSION"; exit 2; }

for run in $RUN_ACCS; do
  bash "$PIPELINES_REPO/scripts/fast-download.sh" "$run" "$FASTQ_DIR"
done

FASTQS=( "$FASTQ_DIR"/*.fastq.gz )
[ ! -e "${FASTQS[0]}" ] && FASTQS=( "$FASTQ_DIR"/*.fastq )
[ ! -e "${FASTQS[0]}" ] && { echo "ERROR: no FASTQs downloaded"; exit 3; }

# Detect SE vs PE from filenames (fast-download.sh emits _1/_2 for PE)
FWD=$(ls "$FASTQ_DIR"/*_1.fastq.gz "$FASTQ_DIR"/*_1.fastq 2>/dev/null | head -1 || true)
REV=$(ls "$FASTQ_DIR"/*_2.fastq.gz "$FASTQ_DIR"/*_2.fastq 2>/dev/null | head -1 || true)
if [ -z "$FWD" ]; then
  FWD=$(ls "$FASTQ_DIR"/*.fastq.gz "$FASTQ_DIR"/*.fastq 2>/dev/null | head -1)
fi
echo "   fwd=$FWD  rev=${REV:-<SE>}"

# --- Dispatch ---
case "$STRATEGY" in
  ChIP-Seq|ATAC-Seq)
    OUT_SUBDIR=$([ "$STRATEGY" = "ChIP-Seq" ] && echo chipseq || echo atacseq)
    OUT_DIR="$DATA_ROOT/output/$OUT_SUBDIR/$ACCESSION"
    mkdir -p "$OUT_DIR"
    GSIZE=$(cat "$REF_DIR/macs_gsize.txt")
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome-fasta "$REF_DIR/MpTak_v7.1.fa"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --genome-size "$GSIZE"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    docker run --rm \
      -v "$DATA_ROOT:$DATA_ROOT" \
      -v "$PIPELINES_REPO:$PIPELINES_REPO:ro" \
      -e TMPDIR="$TMP_DIR" \
      -v "$TMP_DIR:$TMP_DIR" \
      --user "$(id -u):$(id -g)" \
      "$CHIP_IMG" \
      bash "$PIPELINES_REPO/scripts/pipeline-v2.sh" "${ARGS[@]}"
    ;;

  Bisulfite-Seq)
    OUT_DIR="$DATA_ROOT/output/bsseq/$ACCESSION"
    mkdir -p "$OUT_DIR"
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome MpTak_v7.1
      --genome-fasta "$REF_DIR/MpTak_v7.1.fa"
      --abismal-index "$REF_DIR/MpTak_v7.1.abismal.idx"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    docker run --rm \
      -v "$DATA_ROOT:$DATA_ROOT" \
      -v "$ZENIGOKE_REPO:$ZENIGOKE_REPO:ro" \
      -e TMPDIR="$TMP_DIR" \
      -v "$TMP_DIR:$TMP_DIR" \
      --user "$(id -u):$(id -g)" \
      "$BS_IMG" \
      bash "$ZENIGOKE_REPO/scripts/pipeline-v2-bs-plant.sh" "${ARGS[@]}"
    ;;

  *)
    echo "ERROR: unknown library_strategy: $STRATEGY"
    exit 4
    ;;
esac

echo "=== $(date -Iseconds) end $ACCESSION ok ==="
