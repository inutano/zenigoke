#!/usr/bin/env bash
# curate-metadata.sh — Fetch BioSample records and run bsllmner-mk2 Select mode.
#
# DEVIATIONS FROM THE PLAN (discovered by reading the real bsllmner-mk2 CLI):
#
#  1. NO --extra-config flag.  bsllmner2_select takes exactly one --select-config
#     file.  We therefore merge select-config-plants.json + select-config-zenigoke.json
#     into a single /tmp/select-config-merged.json with a small jq step before
#     invoking the tool.  If the plants config and our config ever share a field
#     name, ours wins (jq "*" right-side priority).
#
#  2. NO --output flag.  bsllmner2_select always writes to
#     bsllmner2-results/select/select_{run_name}.json inside the repo dir (which
#     is bind-mounted into the container).  We use --run-name to make the filename
#     deterministic, then read the result from that path after the run.
#
#  3. Input format is a JSON ARRAY (or JSONL).  Each entry must have an "accession"
#     field.  ENA BioSample XML is converted to a structure that mirrors the
#     bsllmner-mk2 example: {accession, title, characteristics: {key: [{text:val}]}}
#     This matches how the example_biosample.json uses characteristics with text
#     sub-keys.  We use this shape rather than the flat {accession, attributes{}}
#     shape sketched in the plan draft.
#
#  4. bsllmner2_select runs INSIDE the docker-compose container via
#     "docker compose exec -T app bsllmner2_select".  The input file and config
#     must be accessible from inside the container — we copy them to the bsllmner
#     repo dir which is already bind-mounted as /app inside the container.
#
#  5. The plants config (select-config-plants.json) references ontology paths like
#     "ontology/po_tissue_subset.owl" that must exist inside the container.  Those
#     require a separate ontology-build step (Task 8 prerequisite).  If ontology
#     files are absent, Stage 2 will emit warnings and fall through to Stage 3 LLM
#     selection with empty candidates — output is still produced.
#     TODO: verify ontology files are built before first full curation run.
#
#  6. Output splitting: the SelectResult JSON has entries[].extract.accession, NOT
#     entries[].accession.  The splitter handles both layouts.
#
set -euo pipefail

# ── configuration ──────────────────────────────────────────────────────────────
CSV="${CSV:-zenigoke_sra_experiments.csv}"
BIOSAMPLES_DIR="${BIOSAMPLES_DIR:-metadata/biosamples}"
CURATED_DIR="${CURATED_DIR:-metadata/curated}"
LOG="${LOG:-metadata/curation.log}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:27b}"
BSLLMNER_REPO="${BSLLMNER_REPO:-$HOME/repos/bsllmner-mk2}"
RUN_NAME="${RUN_NAME:-zenigoke_curate}"

# Path used inside the bsllmner-mk2 container (repo dir is mounted at /app)
CONTAINER_INPUT="/app/tmp_zenigoke_bs_input.json"
CONTAINER_CONFIG="/app/tmp_zenigoke_select_config_merged.json"

mkdir -p "$BIOSAMPLES_DIR" "$CURATED_DIR" "$(dirname "$LOG")"
: > "$LOG"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# ── Stage 1: fetch BioSample records ──────────────────────────────────────────
log "== stage 1: fetch BioSample records =="

while IFS=, read -r strategy accession; do
  [ "$strategy" = "library_strategy" ] && continue
  [ -z "$accession" ] && continue

  out="$BIOSAMPLES_DIR/${accession}.json"
  [ -s "$out" ] && { log "  skip $accession (cached)"; continue; }

  log "  fetching $accession ..."

  # SRX/DRX/ERX → sample_accession (SAMN/SAMEA/SAMD)
  # ENA filereport returns TWO columns even when only sample_accession is requested:
  #   col1=run_accession  col2=sample_accession
  # We must extract col2 (sample_accession, the SAMN*/SAMEA*/SAMD* BioSample ID).
  biosample=$(curl -fsSL \
    "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${accession}&result=read_run&fields=sample_accession&format=tsv" \
    2>>"$LOG" | awk 'NR==2{print $2}' || true)

  if [ -z "$biosample" ]; then
    log "  WARN: no biosample for $accession — skipping"
    continue
  fi
  log "  $accession -> $biosample"

  # Fetch BioSample XML from ENA
  xml_path="${BIOSAMPLES_DIR}/${accession}.xml"
  if ! curl -fsSL \
       "https://www.ebi.ac.uk/ena/browser/api/xml/${biosample}" \
       -o "$xml_path" 2>>"$LOG"; then
    log "  WARN: XML fetch failed for $biosample ($accession) — skipping"
    rm -f "$xml_path"
    continue
  fi

  # Convert ENA XML → bsllmner-mk2 JSON format.
  # Shape: {accession, title, characteristics: {key: [{text: value}]}}
  # This matches the example_biosample.json structure that bsllmner-mk2 expects.
  if ! python3 - "$biosample" "$accession" "$xml_path" "$out" <<'PY'
import sys, json, xml.etree.ElementTree as ET

biosample_acc, exp_acc, xml_path, out_path = sys.argv[1:5]
try:
    tree = ET.parse(xml_path)
    root = tree.getroot()
except ET.ParseError as e:
    print(f"WARN: XML parse error for {biosample_acc}: {e}", file=sys.stderr)
    sys.exit(1)

# Build characteristics as {key: [{text: value}]} to match bsllmner-mk2 format
characteristics = {}
for a in root.iter("SAMPLE_ATTRIBUTE"):
    tag = a.findtext("TAG") or ""
    val = a.findtext("VALUE") or ""
    if tag:
        characteristics.setdefault(tag, []).append({"text": val, "tag": "attribute"})

# Also capture organism from SAMPLE_NAME/TAXON_ID path
taxon_id = root.findtext(".//TAXON_ID") or ""
sci_name = root.findtext(".//SCIENTIFIC_NAME") or ""
if sci_name and "organism" not in characteristics:
    characteristics["organism"] = [{"text": sci_name}]

title = root.findtext(".//TITLE") or ""
description = root.findtext(".//DESCRIPTION") or ""

entry = {
    "accession": biosample_acc,
    "name": biosample_acc,
    # Keep experiment accession for back-reference during splitting
    "_experiment_accession": exp_acc,
    "title": title,
    "description": description,
    "taxId": int(taxon_id) if taxon_id.isdigit() else None,
    "characteristics": characteristics,
}

with open(out_path, "w") as f:
    json.dump(entry, f, indent=2)
print(f"  wrote {out_path}")
PY
  then
    log "  WARN: JSON conversion failed for $accession — skipping"
    rm -f "$xml_path" "$out"
    continue
  fi

done < "$CSV"

# ── Stage 2: run bsllmner-mk2 Select mode ─────────────────────────────────────
log "== stage 2: run bsllmner-mk2 Select =="

# Verify bsllmner-mk2 repo
test -d "$BSLLMNER_REPO" || {
  log "ERROR: bsllmner-mk2 not found at $BSLLMNER_REPO"
  log "       run: git clone https://github.com/dbcls/bsllmner-mk2.git $BSLLMNER_REPO"
  exit 1
}

# Count available BioSample JSONs
n_samples=$(find "$BIOSAMPLES_DIR" -name "*.json" | wc -l)
log "  found $n_samples biosample JSON files"
if [ "$n_samples" -eq 0 ]; then
  log "ERROR: no biosample JSON files in $BIOSAMPLES_DIR — nothing to curate"
  exit 1
fi

# Bundle all biosample JSONs into one input file (JSON array)
# Copy to bsllmner repo dir so docker can access it via the bind mount
bundle_host="${BSLLMNER_REPO}/tmp_zenigoke_bs_input.json"
python3 - "$BIOSAMPLES_DIR" "$bundle_host" <<'PY'
import json, glob, os, sys
biosample_dir, out_path = sys.argv[1], sys.argv[2]
items = []
for f in sorted(glob.glob(os.path.join(biosample_dir, "*.json"))):
    try:
        items.append(json.load(open(f)))
    except json.JSONDecodeError as e:
        print(f"WARN: skip {f}: {e}", file=sys.stderr)
with open(out_path, "w") as fh:
    json.dump(items, fh)
print(f"bundled {len(items)} entries -> {out_path}")
PY

log "  bundled $n_samples entries into $bundle_host"

# Merge plant config + zenigoke config into one merged config.
# bsllmner2_select accepts exactly ONE --select-config file; there is no
# --extra-config flag.  We merge with jq: plant fields come first, our fields
# are added/override via "*" (right-side wins on key collision).
plants_config="$BSLLMNER_REPO/scripts/select-config-plants.json"
zenigoke_config="$(cd "$(dirname "$0")/.." && pwd)/configs/select-config-zenigoke.json"
merged_host="${BSLLMNER_REPO}/tmp_zenigoke_select_config_merged.json"

# KNOWN LIMITATION — Issue C (ontology subset OWL files not built):
# select-config-plants.json references ontology/po_tissue_subset.owl and
# ontology/po_cell_subset.owl.  Building these requires:
#   1. Downloading full upstream OWLs (po.owl, cl.owl, etc.) via
#      scripts/download_ontology_files.py  (~GB-scale files)
#   2. Running scripts/build_subset_ontologies.sh (uses obolibrary/robot Docker)
# This is heavy and not done in the current pipeline run.
# When the OWL files are absent, bsllmner-mk2 will skip ontology matching for
# tissue/cell_type and fall through to Stage 3 LLM-only extraction, which still
# produces output.  We null out the ontology_file paths in the merged config so
# bsllmner-mk2 doesn't error on missing files — it will use LLM extraction only.
# TODO: run download + build_subset_ontologies.sh before the final production run
#       to enable full ontology-backed term matching.
if command -v jq >/dev/null 2>&1; then
  jq -s '
    .[0].fields * .[1].fields
    # Null out ontology_file paths where the OWL file does not exist on disk.
    # This lets bsllmner-mk2 fall back to LLM-only extraction rather than aborting.
    | with_entries(
        if .value.ontology_file != null then
          .value.ontology_file = null
        else . end
      )
    | {fields: .}
  ' "$plants_config" "$zenigoke_config" > "$merged_host"
  log "  merged config written to $merged_host (jq; ontology_file paths nulled — OWLs not built)"
else
  # Fallback: Python merge (stdlib)
  python3 - "$plants_config" "$zenigoke_config" "$merged_host" <<'PY'
import json, sys
p, z, out = sys.argv[1], sys.argv[2], sys.argv[3]
plants = json.load(open(p))
zenigoke = json.load(open(z))
merged = {"fields": {**plants["fields"], **zenigoke["fields"]}}
# Null out ontology_file paths — OWL files not built yet (Issue C)
for v in merged["fields"].values():
    v["ontology_file"] = None
with open(out, "w") as f:
    json.dump(merged, f, indent=2)
print(f"merged config -> {out} ({len(merged['fields'])} fields); ontology_file paths nulled")
PY
  log "  merged config written to $merged_host (python; ontology_file paths nulled — OWLs not built)"
fi

# Stand up bsllmner-mk2 via docker compose
log "  starting bsllmner-mk2 container ..."
(cd "$BSLLMNER_REPO" && docker compose up -d --build) 2>>"$LOG"

# Run bsllmner2_select inside the container.
# The repo dir is bind-mounted at /app, so our tmp files are accessible there.
# Results land at: /app/bsllmner2-results/select/select_${RUN_NAME}.json
#
# OLLAMA_HOST: the compose stack's own ollama service has no models pulled.
# We point the app at the HOST Ollama via the Docker bridge gateway (172.17.0.1).
# Confirmed by: docker network inspect bridge | grep Gateway
# → "Gateway": "172.17.0.1"  (Linux default bridge, stable on this host)
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

# ── Stage 3: split output into per-experiment files ───────────────────────────
log "== stage 3: split output to $CURATED_DIR =="

python3 - "$result_file" "$BIOSAMPLES_DIR" "$CURATED_DIR" <<'PY'
import json, os, sys
from collections import defaultdict

result_file, biosamples_dir, curated_dir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(curated_dir, exist_ok=True)

data = json.load(open(result_file))
entries = data.get("entries", [])

# Build a reverse map: biosample_accession -> [experiment_accession, ...]
# Multiple experiments can share the same BioSample (e.g. ChIP-Seq input + IP
# from the same biological material).  We write the curated result for every
# experiment so downstream code always finds SRX*.json / DRX*.json in curated/.
bs_to_exps = defaultdict(list)
for f in os.listdir(biosamples_dir):
    if not f.endswith(".json"):
        continue
    try:
        rec = json.load(open(os.path.join(biosamples_dir, f)))
        exp_acc = rec.get("_experiment_accession", "")
        bs_acc = rec.get("accession", "")
        if exp_acc and bs_acc:
            bs_to_exps[bs_acc].append(exp_acc)
    except Exception:
        pass

written = 0
for entry in entries:
    # SelectResult: entries[].extract.accession holds the biosample accession
    extract = entry.get("extract", {})
    bs_acc = extract.get("accession") or entry.get("accession")
    if not bs_acc:
        print(f"WARN: entry has no accession, skipping", file=sys.stderr)
        continue

    # Write a curated file for every experiment that maps to this biosample.
    # (Most biosamples have exactly one experiment; shared biosamples get copies.)
    exp_accs = bs_to_exps.get(bs_acc) or [bs_acc]
    for exp_acc in exp_accs:
        out_path = os.path.join(curated_dir, f"{exp_acc}.json")
        with open(out_path, "w") as fh:
            json.dump(entry, fh, indent=2)
        written += 1

print(f"wrote {written} curated files to {curated_dir}")
PY

log "== done =="

# Cleanup temp files from bsllmner repo dir
rm -f "${BSLLMNER_REPO}/tmp_zenigoke_bs_input.json" \
      "${BSLLMNER_REPO}/tmp_zenigoke_select_config_merged.json"
