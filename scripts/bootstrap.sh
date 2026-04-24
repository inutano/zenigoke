#!/usr/bin/env bash
# Bootstrap the zenigoke data layout and verify runtime prerequisites.
# Idempotent — safe to re-run.
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:27b}"

echo "== creating data directories =="
mkdir -p "$DATA_ROOT"/{references,fastq,tmp,status,logs}
mkdir -p "$DATA_ROOT"/output/{chipseq,atacseq,bsseq}
mkdir -p metadata/{biosamples,curated} report

echo "== verifying docker =="
command -v docker >/dev/null || { echo "ERROR: docker not installed"; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon not reachable"; exit 1; }

echo "== pulling pipeline images =="
docker pull "$CHIP_IMG"
docker pull "$BS_IMG"

echo "== verifying pipeline scripts upstream =="
test -f "$HOME/repos/chip-atlas-pipeline-v2/scripts/pipeline-v2.sh" || {
  echo "ERROR: upstream pipeline scripts missing at ~/repos/chip-atlas-pipeline-v2"
  exit 1
}
test -f "$HOME/repos/chip-atlas-pipeline-v2/scripts/pipeline-v2-bs.sh"
test -f "$HOME/repos/chip-atlas-pipeline-v2/scripts/fast-download.sh"

echo "== verifying ollama and model =="
command -v ollama >/dev/null && {
  ollama list | grep -q "$OLLAMA_MODEL" || {
    echo "WARN: $OLLAMA_MODEL not pulled locally."
    echo "      run: ollama pull $OLLAMA_MODEL   (needed before curate-metadata.sh)"
  }
} || echo "WARN: ollama not installed; curate-metadata.sh will fail until fixed."

echo "== ok =="
echo "   DATA_ROOT = $DATA_ROOT"
echo "   free on /data1: $(df -h "$DATA_ROOT" | awk 'NR==2{print $4}')"
