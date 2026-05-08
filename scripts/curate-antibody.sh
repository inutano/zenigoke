#!/usr/bin/env bash
# curate-antibody.sh — Phase 2A: Fetch SRA Experiment XML for ChIP-Seq samples
# and run a focused bsllmner-mk2 Select pass to extract antibody_target.
#
# Mirrors curate-metadata.sh in structure.  Key differences:
#  - Reads from Experiment XML (not BioSample XML).
#  - Uses configs/select-config-antibody.json (single field: antibody_target).
#  - Merges result into existing metadata/curated/{SRX}.json under a NEW
#    top-level key "extract_experiment" — never touches the existing "extract" key.
#  - Atomic merge via write-to-.tmp-then-rename.
#
set -euo pipefail

# ── configuration ──────────────────────────────────────────────────────────────
CSV="${CSV:-zenigoke_sra_experiments.csv}"
EXPERIMENTS_DIR="${EXPERIMENTS_DIR:-metadata/experiments}"
CURATED_DIR="${CURATED_DIR:-metadata/curated}"
LOG="${LOG:-metadata/curation-antibody.log}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:27b}"
BSLLMNER_REPO="${BSLLMNER_REPO:-$HOME/repos/bsllmner-mk2}"
RUN_NAME="${RUN_NAME:-zenigoke_antibody}"

# Paths inside the bsllmner-mk2 container (repo dir is mounted at /app)
CONTAINER_INPUT="/app/tmp_zenigoke_exp_input.json"
CONTAINER_CONFIG="/app/tmp_zenigoke_antibody_config.json"

mkdir -p "$EXPERIMENTS_DIR" "$CURATED_DIR" "$(dirname "$LOG")"
: > "$LOG"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# ── Stage 1: filter CSV to ChIP-Seq rows and fetch Experiment XML ─────────────
log "== stage 1: fetch SRA Experiment XML =="

while IFS=, read -r strategy accession; do
  [ "$strategy" = "library_strategy" ] && continue
  [ "$strategy" = "ChIP-Seq" ] || continue
  [ -z "$accession" ] && continue

  out="${EXPERIMENTS_DIR}/${accession}.xml"
  if [ -s "$out" ]; then
    log "  skip $accession (cached)"
    continue
  fi

  log "  fetching $accession ..."
  if ! curl -fsSL \
       "https://www.ebi.ac.uk/ena/browser/api/xml/${accession}" \
       -o "$out" 2>>"$LOG"; then
    log "  WARN: XML fetch failed for $accession — skipping"
    rm -f "$out"
    continue
  fi

done < "$CSV"

# ── Stage 2: convert Experiment XMLs to bsllmner-mk2 input format ─────────────
log "== stage 2: convert XMLs to bsllmner-mk2 input JSON =="

n_xmls=$(find "$EXPERIMENTS_DIR" -name "*.xml" | wc -l)
log "  found $n_xmls experiment XML files"

if [ "$n_xmls" -eq 0 ]; then
  log "ERROR: no experiment XML files in $EXPERIMENTS_DIR — nothing to curate"
  exit 1
fi

bundle_host="${BSLLMNER_REPO}/tmp_zenigoke_exp_input.json"

python3 - "$EXPERIMENTS_DIR" "$bundle_host" <<'PY'
import sys, json, glob, os
import xml.etree.ElementTree as ET

experiments_dir, out_path = sys.argv[1], sys.argv[2]
items = []

for xml_file in sorted(glob.glob(os.path.join(experiments_dir, "*.xml"))):
    accession = os.path.basename(xml_file).replace(".xml", "")
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

log "  bundled $n_xmls entries into $bundle_host"

# ── Stage 3: run bsllmner-mk2 Select ─────────────────────────────────────────
log "== stage 3: run bsllmner-mk2 Select =="

# Verify bsllmner-mk2 repo
test -d "$BSLLMNER_REPO" || {
  log "ERROR: bsllmner-mk2 not found at $BSLLMNER_REPO"
  log "       run: git clone https://github.com/dbcls/bsllmner-mk2.git $BSLLMNER_REPO"
  exit 1
}

# Copy antibody config to bsllmner repo dir (bind-mounted as /app in container)
antibody_config="$(cd "$(dirname "$0")/.." && pwd)/configs/select-config-antibody.json"
config_host="${BSLLMNER_REPO}/tmp_zenigoke_antibody_config.json"
cp "$antibody_config" "$config_host"
log "  antibody config copied to $config_host"

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

python3 - "$result_file" "$CURATED_DIR" <<'PY'
import json, os, sys

result_file, curated_dir = sys.argv[1], sys.argv[2]
os.makedirs(curated_dir, exist_ok=True)

data = json.load(open(result_file))
entries = data.get("entries", [])

merged = 0
created = 0
skipped = 0

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
        os.unlink(tmp_path) if os.path.exists(tmp_path) else None
        skipped += 1
        continue

    if is_new:
        created += 1
    else:
        merged += 1

print(f"merged {merged} existing + created {created} new curated files ({skipped} skipped)")
PY

log "== done =="

# Cleanup temp files from bsllmner repo dir
rm -f "${BSLLMNER_REPO}/tmp_zenigoke_exp_input.json" \
      "${BSLLMNER_REPO}/tmp_zenigoke_antibody_config.json"
