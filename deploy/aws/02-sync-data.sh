#!/usr/bin/env bash
# Sync pipeline outputs to S3.
# Idempotent: aws s3 sync only uploads changed/new files.
#
# Usage:
#   bash deploy/aws/02-sync-data.sh [BUCKET_NAME]
#
# Defaults: BUCKET_NAME=zenigoke-catalog  SOURCE=/data1/zenigoke/output
set -euo pipefail

BUCKET="${1:-${ZENIGOKE_BUCKET:-zenigoke-catalog}}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
SOURCE="${ZENIGOKE_OUTPUT_DIR:-/data1/zenigoke/output}"

[ -d "$SOURCE" ] || {
  echo "ERROR: source dir $SOURCE does not exist"
  exit 1
}

echo "== syncing $SOURCE → s3://$BUCKET/output (region: $REGION) =="
echo "   first run: ~37 GB, can take an hour over residential link"
echo "   subsequent runs: only changed files"

aws s3 sync "$SOURCE" "s3://${BUCKET}/output" \
  --region "$REGION" \
  --exclude "*.tmp" \
  --exclude "tmp/*" \
  --size-only

echo "== uploading catalog DB =="
DB_PATH="${ZENIGOKE_DB_PATH:-$(dirname "$0")/../../db/kknmsmd.db}"
if [ -f "$DB_PATH" ]; then
  aws s3 cp "$DB_PATH" "s3://${BUCKET}/db/kknmsmd.db" --region "$REGION"
else
  echo "  WARN: $DB_PATH not found — GitHub Actions workflow will fail to fetch the DB"
fi

echo "== done =="
echo "   Sample track URL:"
echo "   https://${BUCKET}.s3.${REGION}.amazonaws.com/output/chipseq/SRX22603368/SRX22603368.bw"
