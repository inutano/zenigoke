#!/usr/bin/env bash
# Sync pipeline outputs, the catalog DB, the web UI catalog index and the
# organelle-like (NUPT/NUMT) blacklist BED to S3.
# Idempotent: aws s3 sync only uploads new files and files whose size or
# modification time changed.
#
# Usage:
#   bash deploy/aws/02-sync-data.sh [--dryrun] [--delete] [BUCKET_NAME]
#
#   --dryrun  pass --dryrun to every aws call (prints what would change)
#   --delete  also delete S3 objects under output/ that no longer exist locally
#             (e.g. v1 peak files the v2 re-run no longer produces). Run with
#             --dryrun --delete first and read the list.
#
# Defaults: BUCKET_NAME=zenigoke-catalog-dsc  SOURCE=/data1/zenigoke/output
# The web UI index (web/data/catalog.json) is rebuilt from the DB and SOURCE,
# then uploaded to s3://$BUCKET/db/catalog.json, where the Pages workflow
# fetches it. Set ZENIGOKE_SKIP_BUILD=1 to upload an existing index as is
# (ZENIGOKE_CATALOG_PATH, default web/data/catalog.json).
set -euo pipefail

DRYRUN=""
DELETE=""
BUCKET_ARG=""
for arg in "$@"; do
  case "$arg" in
    --dryrun) DRYRUN="--dryrun" ;;
    --delete) DELETE="--delete" ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) echo "ERROR: unknown option $arg (expected --dryrun, --delete)"; exit 1 ;;
    *) BUCKET_ARG="$arg" ;;
  esac
done

BUCKET="${BUCKET_ARG:-${ZENIGOKE_BUCKET:-zenigoke-catalog-dsc}}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
SOURCE="${ZENIGOKE_OUTPUT_DIR:-/data1/zenigoke/output}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

[ -d "$SOURCE" ] || {
  echo "ERROR: source dir $SOURCE does not exist"
  exit 1
}

echo "== syncing $SOURCE → s3://$BUCKET/output (region: $REGION)${DRYRUN:+ [dryrun]}${DELETE:+ [delete]} =="
echo "   first run: ~40 GB, can take an hour over residential link"
echo "   subsequent runs: only changed files"

# No --size-only: a re-run output with the same size as the old file must still upload.
# shellcheck disable=SC2086  # DRYRUN/DELETE are empty or a single flag
aws s3 sync "$SOURCE" "s3://${BUCKET}/output" \
  --region "$REGION" \
  --exclude "*.tmp" \
  --exclude "tmp/*" \
  $DELETE $DRYRUN

echo "== uploading catalog DB =="
DB_PATH="${ZENIGOKE_DB_PATH:-$REPO/db/kknmsmd.db}"
if [ -f "$DB_PATH" ]; then
  # shellcheck disable=SC2086
  aws s3 cp "$DB_PATH" "s3://${BUCKET}/db/kknmsmd.db" --region "$REGION" $DRYRUN
else
  echo "  WARN: $DB_PATH not found — GitHub Actions workflow will fail to fetch the DB"
fi

echo "== building and uploading the web UI catalog index =="
CATALOG="${ZENIGOKE_CATALOG_PATH:-$REPO/web/data/catalog.json}"
if [ -z "${ZENIGOKE_SKIP_BUILD:-}" ]; then
  python3 "$REPO/web/make_index.py" --db "$DB_PATH" --output-root "$SOURCE"
fi
if [ -f "$CATALOG" ]; then
  # shellcheck disable=SC2086
  aws s3 cp "$CATALOG" "s3://${BUCKET}/db/catalog.json" --region "$REGION" $DRYRUN
else
  echo "  WARN: $CATALOG not found — GitHub Actions workflow will fail to fetch the index"
fi

echo "== uploading organelle-like (NUPT/NUMT) blacklist =="
BED="$REPO/configs/organelle_like_regions.MpTak_v7.1.bed"
# shellcheck disable=SC2086
aws s3 cp "$BED" "s3://${BUCKET}/references/MpTak_v7.1/organelle_like_regions.MpTak_v7.1.bed" \
  --region "$REGION" $DRYRUN

echo "== done =="
echo "   Sample track URL:"
echo "   https://${BUCKET}.s3.${REGION}.amazonaws.com/output/chipseq/SRX22603368/SRX22603368.bw"
