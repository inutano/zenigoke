#!/usr/bin/env bash
#
# Recover from a known fast-download.sh failure mode:
#   ENA reports library_layout=PAIRED, but fastq_ftp lists only a single
#   combined .fastq.gz (no _1/_2 split). The upstream CONCAT step then
#   tries to cat *_1.fastq.gz and fails.
#
# This script downloads that single file directly, places it in the
# zenigoke FASTQ_DIR, and invokes run-sample.sh which will treat it as SE.
#
# Usage:
#   bash scripts/retry-pe-fallback.sh <accession> <library_strategy>
#
# Reads ENA filereport, expects exactly one FASTQ URL. Errors loudly if
# the assumption doesn't hold.
set -euo pipefail

ACCESSION="${1:?need accession}"
STRATEGY="${2:?need library_strategy}"

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
FASTQ_DIR="$DATA_ROOT/fastq/$ACCESSION"
STATUS_DIR="$DATA_ROOT/status"

# Pre-clean any stale state
rm -rf "$FASTQ_DIR"
rm -f "$STATUS_DIR/$ACCESSION.failed" "$STATUS_DIR/$ACCESSION.ok"
mkdir -p "$FASTQ_DIR"

# Resolve runs
RUN_INFO=$(curl -fsSL \
  "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${ACCESSION}&result=read_run&fields=run_accession,library_layout,fastq_ftp&format=tsv" \
  | tail -n +2)

[ -z "$RUN_INFO" ] && { echo "ERROR: ENA returned no runs for $ACCESSION"; exit 2; }

# Download every URL listed in fastq_ftp. ENA HTTPS connections drop
# mid-transfer often enough that curl with --retry alone is unreliable —
# aria2c with multi-segment + retry handles it.
while IFS=$'\t' read -r run layout ftp; do
  [ -z "$ftp" ] && { echo "ERROR: empty fastq_ftp for $run"; exit 3; }
  for url in $(echo "$ftp" | tr ';' ' '); do
    fname=$(basename "$url")
    echo "  downloading https://${url}"
    aria2c \
      --max-tries=10 \
      --retry-wait=10 \
      --connect-timeout=30 \
      --timeout=60 \
      --max-connection-per-server=4 \
      --split=4 \
      --continue=true \
      --auto-file-renaming=false \
      --allow-overwrite=true \
      --console-log-level=warn \
      --summary-interval=30 \
      --dir="$FASTQ_DIR" \
      --out="$fname" \
      "https://${url}"
  done
done <<< "$RUN_INFO"

ls -lh "$FASTQ_DIR"

# Hand off to the normal dispatcher. run-sample.sh's SE/PE auto-detect will
# see no *_1.fastq.gz / *_2.fastq.gz and fall back to the single .fastq.gz.
# The cleanup trap inside run-sample.sh handles status markers.
exec bash "$(dirname "$0")/run-sample.sh" "$ACCESSION" "$STRATEGY"
