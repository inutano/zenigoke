#!/usr/bin/env bash
# Assemble the GitHub Pages site: the web UI (web/) at the root, the generated
# catalog pages (per-sample pages, methods, summary, enrichment, ...) kept at
# their own paths so existing links still work, and the old landing page kept
# as classic.html.
#
# Usage:
#   bash scripts/assemble-site.sh REPORT_DIR CATALOG_JSON OUT_DIR
#
#   REPORT_DIR    output of scripts/build-catalog-pages.py (report/)
#   CATALOG_JSON  web UI index built by web/make_index.py (CI downloads it
#                 from s3://$BUCKET/db/catalog.json, uploaded by 02-sync-data.sh)
#   OUT_DIR       new directory to publish (must not exist yet)
set -euo pipefail

[ $# -eq 3 ] || { sed -n '2,13p' "$0"; exit 1; }
REPORT="$1"
CATALOG="$2"
OUT="$3"
WEB="$(cd "$(dirname "$0")/.." && pwd)/web"

[ -s "$REPORT/index.html" ] || {
  echo "ERROR: $REPORT/index.html missing (run scripts/build-catalog-pages.py)"
  exit 1
}
python3 - "$CATALOG" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
schema = str(d.get("schema", ""))
if not schema.startswith("zenigoke-catalog-index/") or not d.get("samples"):
    sys.exit(f"ERROR: {sys.argv[1]} is not a web UI catalog index (schema {schema!r})")
EOF
[ ! -e "$OUT" ] || { echo "ERROR: $OUT already exists"; exit 1; }

mkdir -p "$OUT"
cp -R "$REPORT"/. "$OUT"/
# Local builds have report/output (a symlink to the pipeline tree) and
# report/bundles (written by the API server); neither belongs on Pages.
rm -rf "$OUT/output" "$OUT/bundles"
mv "$OUT/index.html" "$OUT/classic.html"
cp "$WEB/index.html" "$OUT/index.html"
cp -R "$WEB/css" "$WEB/js" "$OUT"/
mkdir -p "$OUT/data"
cp "$WEB/data/basemap.json" "$WEB/data/MpTak_v7.1.igv.json" "$OUT/data/"
cp "$CATALOG" "$OUT/data/catalog.json"

echo "assembled $OUT: web UI at index.html, previous landing page at classic.html"
