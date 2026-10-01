#!/usr/bin/env bash
# Drive run-sample.sh across the zenigoke SRA experiment list.
#
# Flags:
#   --resume                      skip accessions with a .ok marker
#   --only <accession>            run only this accession
#   --library-strategy <s>        only rows with this library_strategy
set -euo pipefail

CSV="${CSV:-zenigoke_sra_experiments.csv}"
DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"

# OUTPUT_ROOT, STATUS_DIR and LOG_DIR are all-or-none: a half-set trio would
# silently send the unset ones to the published Phase-1 dirs under $DATA_ROOT.
given=""; missing=""
for v in OUTPUT_ROOT STATUS_DIR LOG_DIR; do
  if [ -n "${!v:-}" ]; then given="$given $v"; else missing="$missing $v"; fi
done
if [ -n "$given" ] && [ -n "$missing" ]; then
  echo "ERROR: set all of OUTPUT_ROOT STATUS_DIR LOG_DIR or none (set:$given; missing:$missing)" >&2
  exit 2
fi
OUTPUT_ROOT="${OUTPUT_ROOT:-$DATA_ROOT/output}"
STATUS_DIR="${STATUS_DIR:-$DATA_ROOT/status}"
LOG_DIR="${LOG_DIR:-$DATA_ROOT/logs}"
export OUTPUT_ROOT STATUS_DIR LOG_DIR
RESUME=0
ONLY=""
FILTER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --only) ONLY="$2"; shift 2 ;;
    --library-strategy) FILTER="$2"; shift 2 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
done

total=0; skipped=0; ran=0; failed=0

# Expected CSV header: library_strategy,experiment_accession
while IFS=, read -r strategy accession; do
  [ "$strategy" = "library_strategy" ] && continue
  [ -z "$accession" ] && continue
  total=$((total + 1))

  [ -n "$ONLY" ] && [ "$accession" != "$ONLY" ] && continue
  [ -n "$FILTER" ] && [ "$strategy" != "$FILTER" ] && continue
  [ "$RESUME" = "1" ] && [ -f "$STATUS_DIR/$accession.ok" ] && {
    skipped=$((skipped + 1))
    continue
  }

  echo ">>> $(date -Iseconds) $accession ($strategy)"
  if bash scripts/run-sample.sh "$accession" "$strategy"; then
    ran=$((ran + 1))
  else
    failed=$((failed + 1))
    echo "    FAILED $accession (continuing)"
  fi
done < "$CSV"

echo "=== run-all summary ==="
echo "total=$total ran=$ran failed=$failed skipped=$skipped"
