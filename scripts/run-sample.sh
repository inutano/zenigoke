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

CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"

FASTQ_DIR="$DATA_ROOT/fastq/$ACCESSION"
TMP_DIR="$DATA_ROOT/tmp/$ACCESSION"
LOG="$LOG_DIR/$ACCESSION.log"

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
  # ENA sometimes labels a run PAIRED but serves one interleaved-less file
  # (SRX29617444-455). fast-download.sh then fails at CONCAT, so fetch the
  # single file directly and let the SE path below pick it up.
  # ENA always prepends run_accession to the requested fields, so pick the
  # columns by header name rather than position.
  FTP=$(curl -fsSL "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${run}&result=read_run&fields=library_layout,fastq_ftp&format=tsv" \
        | awk -F'\t' 'NR==1 {for (i=1;i<=NF;i++) c[$i]=i}
                      NR==2 {print $c["library_layout"]"\t"$c["fastq_ftp"]}')
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

FASTQS=( "$FASTQ_DIR"/*.fastq.gz )
[ ! -e "${FASTQS[0]}" ] && FASTQS=( "$FASTQ_DIR"/*.fastq )
[ ! -e "${FASTQS[0]}" ] && { echo "ERROR: no FASTQs downloaded"; exit 3; }

# Detect SE vs PE from filenames (fast-download.sh emits _1/_2 for PE)
FWD=$(ls "$FASTQ_DIR"/*_1.fastq.gz "$FASTQ_DIR"/*_1.fastq 2>/dev/null | head -1 || true)
REV=$(ls "$FASTQ_DIR"/*_2.fastq.gz "$FASTQ_DIR"/*_2.fastq 2>/dev/null | head -1 || true)
if [ -z "$FWD" ]; then
  FWD=$(ls "$FASTQ_DIR"/*.fastq.gz "$FASTQ_DIR"/*.fastq 2>/dev/null | head -1 || true)
fi
echo "   fwd=$FWD  rev=${REV:-<SE>}"

# --- Dispatch ---
case "$STRATEGY" in
  ChIP-Seq|ATAC-Seq)
    OUT_SUBDIR=$([ "$STRATEGY" = "ChIP-Seq" ] && echo chipseq || echo atacseq)
    OUT_DIR="$OUTPUT_ROOT/$OUT_SUBDIR/$ACCESSION"
    mkdir -p "$OUT_DIR"
    GSIZE=$(cat "$REF_DIR/macs_gsize.txt")
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome-fasta "$REF_DIR/$REF_NAME.fa"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --genome-size "$GSIZE"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
      --blacklist "$BLACKLIST"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    docker run --rm \
      -v "$DATA_ROOT:$DATA_ROOT" \
      -v "$ZENIGOKE_REPO:$ZENIGOKE_REPO:ro" \
      -e TMPDIR="$TMP_DIR" \
      -v "$TMP_DIR:$TMP_DIR" \
      --user "$(id -u):$(id -g)" \
      "$CHIP_IMG" \
      bash "$ZENIGOKE_REPO/scripts/pipeline-v2-plant.sh" "${ARGS[@]}"
    ;;

  Bisulfite-Seq)
    OUT_DIR="$OUTPUT_ROOT/bsseq/$ACCESSION"
    mkdir -p "$OUT_DIR"
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome MpTak_v7.1
      --genome-fasta "$REF_DIR/$REF_NAME.fa"
      --abismal-index "$REF_DIR/$REF_NAME.abismal.idx"
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
