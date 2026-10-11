#!/usr/bin/env bash
# Publish the Zenigoke Catalog web UI (web/) under s3://$BUCKET/beta/.
#
# Rebuilds web/data/catalog.json from the catalog DB first, so run
# 02-sync-data.sh (outputs + DB) before this one. The page is served from the
# same bucket as the data, so the in-browser igv.js preview needs no CORS rule.
#
# Usage:
#   bash deploy/aws/05-sync-beta.sh [--dryrun] [BUCKET_NAME]
#
#   --dryrun  pass --dryrun to the aws call (prints what would change)
#
# Defaults: BUCKET_NAME=zenigoke-catalog-dsc  DB=db/kknmsmd.db  OUTPUT=/data1/zenigoke/output
# Set ZENIGOKE_SKIP_BUILD=1 to publish the existing web/data/catalog.json as is.
set -euo pipefail

DRYRUN=""
BUCKET_ARG=""
for arg in "$@"; do
  case "$arg" in
    --dryrun) DRYRUN="--dryrun" ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    -*) echo "ERROR: unknown option $arg (expected --dryrun)"; exit 1 ;;
    *) BUCKET_ARG="$arg" ;;
  esac
done

BUCKET="${BUCKET_ARG:-${ZENIGOKE_BUCKET:-zenigoke-catalog-dsc}}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WEB="$REPO/web"

if [ -z "${ZENIGOKE_SKIP_BUILD:-}" ]; then
  echo "== building web/data/catalog.json =="
  python3 "$WEB/make_index.py" \
    --db "${ZENIGOKE_DB_PATH:-$REPO/db/kknmsmd.db}" \
    --output-root "${ZENIGOKE_OUTPUT_DIR:-/data1/zenigoke/output}"
fi
[ -f "$WEB/data/catalog.json" ] || {
  echo "ERROR: $WEB/data/catalog.json missing (run web/make_index.py)"
  exit 1
}

echo "== syncing web/ → s3://$BUCKET/beta (region: $REGION)${DRYRUN:+ [dryrun]} =="
# Only the files the page loads; --delete removes stale ones under beta/ only.
# no-cache: browsers revalidate (ETag), so a re-publish shows on the next load.
# shellcheck disable=SC2086  # DRYRUN is empty or a single flag
aws s3 sync "$WEB" "s3://${BUCKET}/beta" \
  --region "$REGION" \
  --exclude "*" \
  --include "index.html" --include "css/*" --include "js/*" --include "data/*" \
  --cache-control "no-cache" \
  --delete $DRYRUN

echo "== done =="
echo "   https://${BUCKET}.s3.${REGION}.amazonaws.com/beta/index.html"
