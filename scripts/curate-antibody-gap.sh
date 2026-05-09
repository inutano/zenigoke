#!/usr/bin/env bash
# curate-antibody-gap.sh — Phase 2B gap-recovery: re-run antibody_target extraction
# for the 17 ChIP-Seq samples that returned null in Phase 2A.
#
# Mirrors curate-antibody.sh in structure.  Key differences:
#  - Operates ONLY on the 17 gap accessions (hardcoded or via ACCS_FILE).
#  - Skips Stage 1 (XML fetch) — XMLs are already cached.
#  - Uses configs/select-config-antibody-gap.json (improved prompt).
#  - Uses --run-name zenigoke_antibody_gap (separate output file).
#  - In Stage 4, overwrites ONLY the "extract_experiment" key (never "extract").
#  - Atomic write (.tmp + os.replace).
#  - Prints a summary: X/17 recovered, Y still null, Z newly populated.
#
set -euo pipefail

# ── configuration ──────────────────────────────────────────────────────────────
EXPERIMENTS_DIR="${EXPERIMENTS_DIR:-metadata/experiments}"
CURATED_DIR="${CURATED_DIR:-metadata/curated}"
LOG="${LOG:-metadata/curation-antibody-gap.log}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:27b}"
BSLLMNER_REPO="${BSLLMNER_REPO:-$HOME/repos/bsllmner-mk2}"
RUN_NAME="${RUN_NAME:-zenigoke_antibody_gap}"

# File containing one accession per line (optional override)
ACCS_FILE="${ACCS_FILE:-}"

# Paths inside the bsllmner-mk2 container (repo dir is mounted at /app)
CONTAINER_INPUT="/app/tmp_zenigoke_gap_input.json"
CONTAINER_CONFIG="/app/tmp_zenigoke_gap_config.json"

mkdir -p "$EXPERIMENTS_DIR" "$CURATED_DIR" "$(dirname "$LOG")"
: > "$LOG"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# ── Hardcoded gap accessions ──────────────────────────────────────────────────
DEFAULT_GAP_ACCS=(
  SRX15503703 SRX15503704 SRX15503705 SRX15503706
  SRX29617453 SRX29617454 SRX29617455 SRX29617463
  SRX29617467 SRX29617468 SRX29617469 SRX29617476
  SRX29617483 SRX8604213  SRX8604214  SRX8604217
  SRX9800285
)

# Build the list of accessions to process
if [ -n "$ACCS_FILE" ] && [ -f "$ACCS_FILE" ]; then
  mapfile -t GAP_ACCS < <(grep -v '^\s*$' "$ACCS_FILE")
  log "  loaded ${#GAP_ACCS[@]} accessions from $ACCS_FILE"
else
  GAP_ACCS=("${DEFAULT_GAP_ACCS[@]}")
  log "  using hardcoded list of ${#GAP_ACCS[@]} gap accessions"
fi

TOTAL="${#GAP_ACCS[@]}"
log "  processing $TOTAL gap accessions"

# ── Stage 1: SKIPPED — XMLs are already cached ────────────────────────────────
log "== stage 1: skipped (XMLs already cached in $EXPERIMENTS_DIR) =="

# Verify all expected XMLs exist
missing=0
for acc in "${GAP_ACCS[@]}"; do
  xml="${EXPERIMENTS_DIR}/${acc}.xml"
  if [ ! -s "$xml" ]; then
    log "  WARN: XML not found for $acc — $xml"
    missing=$((missing + 1))
  fi
done
if [ "$missing" -gt 0 ]; then
  log "  WARN: $missing XML file(s) missing — those accessions will be skipped in bundling"
fi

# ── Stage 2: convert gap Experiment XMLs to bsllmner-mk2 input format ─────────
log "== stage 2: convert gap XMLs to bsllmner-mk2 input JSON =="

bundle_host="${BSLLMNER_REPO}/tmp_zenigoke_gap_input.json"

# Build a JSON-safe list of accessions for Python
accs_json="$(printf '"%s",' "${GAP_ACCS[@]}" | sed 's/,$//')"

python3 - "$EXPERIMENTS_DIR" "$bundle_host" "$accs_json" <<'PY'
import sys, json, os
import xml.etree.ElementTree as ET

experiments_dir, out_path, accs_json_str = sys.argv[1], sys.argv[2], sys.argv[3]
gap_accs = json.loads("[" + accs_json_str + "]")

items = []

for accession in gap_accs:
    xml_file = os.path.join(experiments_dir, f"{accession}.xml")
    if not os.path.exists(xml_file):
        print(f"WARN: XML not found for {accession}, skipping", file=sys.stderr)
        continue
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"WARN: XML parse error for {accession}: {e}", file=sys.stderr)
        continue

    # Extract key fields from Experiment XML
    exp_elem = root.find(".//EXPERIMENT")
    exp_alias = exp_elem.get("alias", "") if exp_elem is not None else ""

    exp_title = root.findtext(".//TITLE") or ""
    library_name = root.findtext(".//LIBRARY_NAME") or ""
    library_strategy = root.findtext(".//LIBRARY_STRATEGY") or ""
    library_selection = root.findtext(".//LIBRARY_SELECTION") or ""
    design_description = root.findtext(".//DESIGN_DESCRIPTION") or ""
    library_construction_protocol = root.findtext(".//LIBRARY_CONSTRUCTION_PROTOCOL") or ""

    # Study title from STUDY_REF (not always available in experiment XML)
    study_title = root.findtext(".//STUDY_REF/IDENTIFIERS/PRIMARY_ID") or ""

    # Build characteristics dict in bsllmner-mk2 format: {key: [{text: value}]}
    characteristics = {}

    def add_char(key, value):
        if value:
            characteristics[key] = [{"text": value}]

    add_char("experiment_title", exp_title)
    add_char("experiment_alias", exp_alias)
    add_char("library_name", library_name)
    add_char("library_strategy", library_strategy)
    add_char("library_selection", library_selection)
    add_char("design_description", design_description)
    add_char("library_construction_protocol", library_construction_protocol)
    if study_title:
        add_char("study_title", study_title)

    # Add EXPERIMENT_ATTRIBUTE key-value pairs (if any are populated)
    for attr in root.findall(".//EXPERIMENT_ATTRIBUTE"):
        tag = attr.findtext("TAG") or ""
        val = attr.findtext("VALUE") or ""
        # Skip purely administrative attributes
        if tag and val and tag not in ("ENA-STATUS", "ENA-LAST-UPDATE", "ENA-FIRST-PUBLIC"):
            characteristics.setdefault(tag, []).append({"text": val})

    entry = {
        "accession": accession,
        "name": accession,
        "title": exp_title,
        "description": design_description,
        "characteristics": characteristics,
    }
    items.append(entry)

with open(out_path, "w") as fh:
    json.dump(items, fh)
print(f"bundled {len(items)} entries -> {out_path}")
PY

bundled_count=$(python3 -c "import json; d=json.load(open('${bundle_host}')); print(len(d))")
log "  bundled $bundled_count entries into $bundle_host"

# ── Stage 3: run bsllmner-mk2 Select ─────────────────────────────────────────
log "== stage 3: run bsllmner-mk2 Select =="

# Verify bsllmner-mk2 repo
test -d "$BSLLMNER_REPO" || {
  log "ERROR: bsllmner-mk2 not found at $BSLLMNER_REPO"
  log "       run: git clone https://github.com/dbcls/bsllmner-mk2.git $BSLLMNER_REPO"
  exit 1
}

# Copy gap config to bsllmner repo dir (bind-mounted as /app in container)
gap_config="$(cd "$(dirname "$0")/.." && pwd)/configs/select-config-antibody-gap.json"
config_host="${BSLLMNER_REPO}/tmp_zenigoke_gap_config.json"
cp "$gap_config" "$config_host"
log "  gap config copied to $config_host"

# Stand up bsllmner-mk2 via docker compose
log "  starting bsllmner-mk2 container ..."
(cd "$BSLLMNER_REPO" && docker compose up -d --build) 2>>"$LOG"

# Run bsllmner2_select inside the container.
# The repo dir is bind-mounted at /app, so our tmp files are accessible there.
# Results land at: /app/bsllmner2-results/select/select_${RUN_NAME}.json
log "  running bsllmner2_select (model=$OLLAMA_MODEL, run=$RUN_NAME) ..."
(cd "$BSLLMNER_REPO" && docker compose exec -T \
  -e OLLAMA_HOST="http://172.17.0.1:11434" \
  app \
  bsllmner2_select \
    --bs-entries "$CONTAINER_INPUT" \
    --select-config "$CONTAINER_CONFIG" \
    --model "$OLLAMA_MODEL" \
    --run-name "$RUN_NAME" \
    --no-reasoning \
) 2>&1 | tee -a "$LOG"

# Result file path (on host, inside bsllmner repo)
result_file="${BSLLMNER_REPO}/bsllmner2-results/select/select_${RUN_NAME}.json"

if [ ! -f "$result_file" ]; then
  log "ERROR: expected result file not found: $result_file"
  log "       check docker compose logs for bsllmner2_select errors"
  exit 1
fi

log "  result file: $result_file"

# ── Stage 4: merge results into existing curated JSONs ────────────────────────
log "== stage 4: merge antibody_target into $CURATED_DIR =="

python3 - "$result_file" "$CURATED_DIR" "$TOTAL" <<'PY'
import json, os, sys

result_file, curated_dir, total_str = sys.argv[1], sys.argv[2], sys.argv[3]
total = int(total_str)
os.makedirs(curated_dir, exist_ok=True)

data = json.load(open(result_file))
entries = data.get("entries", [])

merged = 0
created = 0
skipped = 0
recovered = 0   # non-null antibody_target values written
still_null = 0  # null antibody_target values written

for entry in entries:
    # SelectResult shape: entries[].extract.accession holds the experiment accession
    extract = entry.get("extract", {})
    accession = extract.get("accession") or entry.get("accession")
    if not accession:
        print(f"WARN: entry has no accession, skipping", file=sys.stderr)
        skipped += 1
        continue

    curated_path = os.path.join(curated_dir, f"{accession}.json")

    # Load existing curated JSON if present, else start fresh
    if os.path.exists(curated_path):
        try:
            existing = json.load(open(curated_path))
        except json.JSONDecodeError as e:
            print(f"WARN: cannot parse existing {curated_path}: {e}", file=sys.stderr)
            skipped += 1
            continue
        is_new = False
    else:
        existing = {}
        is_new = True

    # Track the extracted antibody_target value
    # SelectResult structure: entry["extract"]["extracted"]["antibody_target"]
    antibody_val = extract.get("extracted", {}).get("antibody_target") if isinstance(extract.get("extracted"), dict) else None

    # Add/overwrite ONLY the "extract_experiment" key — never touch "extract" etc.
    existing["extract_experiment"] = entry

    # Atomic write: write to .tmp then rename
    tmp_path = curated_path + ".tmp"
    try:
        with open(tmp_path, "w") as fh:
            json.dump(existing, fh, indent=2)
        os.replace(tmp_path, curated_path)
    except Exception as e:
        print(f"WARN: write failed for {curated_path}: {e}", file=sys.stderr)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        skipped += 1
        continue

    if is_new:
        created += 1
    else:
        merged += 1

    if antibody_val is not None:
        recovered += 1
        print(f"  RECOVERED {accession}: {antibody_val}")
    else:
        still_null += 1
        print(f"  STILL_NULL {accession}")

print()
print(f"merged {merged} existing + created {created} new curated files ({skipped} skipped)")
print()
print("=" * 60)
print(f"SUMMARY: {recovered} / {total} recovered, {still_null} still null, {recovered} newly populated")
print("=" * 60)
PY

log "== done =="

# Cleanup temp files from bsllmner repo dir
rm -f "${BSLLMNER_REPO}/tmp_zenigoke_gap_input.json" \
      "${BSLLMNER_REPO}/tmp_zenigoke_gap_config.json"
