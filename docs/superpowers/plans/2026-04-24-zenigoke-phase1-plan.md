# Zenigoke Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process 157 Marchantia polymorpha SRA experiments (90 ChIP-Seq, 36 ATAC-Seq, 31 Bisulfite-Seq) against `MpTak_v7.1_standard_genome`, curate BioSample metadata with bsllmner-mk2, and produce a single HTML summary report.

**Architecture:** Wrapper scripts around the containerized `inutano/chip-atlas-pipeline-v2` pipelines (no upstream edits). Local fork of the BS-seq pipeline adds plant-context (CpG + CHG + CHH) output. Bash dispatchers orchestrate per-sample runs; Python stdlib generates the report. All large binary data lives on `/data1/zenigoke/`; code + small metadata live in `~/work/zenigoke/` (git-tracked).

**Tech Stack:** Bash 5, Docker (pipelines + bsllmner-mk2 via `docker compose`), Python 3 (stdlib only), Ollama (qwen3.5:27b), chip-atlas-pipeline-v2 containers (`ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0` and `-bs:v1.1.0`).

**Reference spec:** `docs/superpowers/specs/2026-04-24-zenigoke-phase1-design.md`

**Execution guidance:** The user said "move fast and iterate." TDD applies to `build-report.py` (Python, has real logic); bash scripts are thin dispatchers — we validate them by running them, not by unit-testing them. Commits are per-task, not per-step.

---

## File structure

**Files created by this plan:**

| Path | Responsibility |
|--|--|
| `scripts/prepare-marchantia.sh` | Download MpTak v7.1 FASTA/GFF3 + build samtools/bwa-mem2/abismal indexes + compute MACS3 genome size. Idempotent. |
| `scripts/pipeline-v2-bs-plant.sh` | Local fork of upstream `pipeline-v2-bs.sh`. Drops `-cpg-only` and produces parallel CpG / CHG / CHH outputs. |
| `scripts/run-sample.sh` | Per-sample dispatcher: downloads FASTQ, picks pipeline by library_strategy, writes `.ok`/`.failed` marker. |
| `scripts/run-all.sh` | Top-level driver over the CSV with `--resume`, `--only`, `--library-strategy` flags. |
| `scripts/curate-metadata.sh` | Fetches BioSamples from ENA/NCBI and runs bsllmner-mk2 Select mode. |
| `configs/select-config-zenigoke.json` | Custom bsllmner-mk2 config for extract-only plant fields (stage/strain/treatment/antibody). |
| `scripts/build-report.py` | Walks output + metadata trees → `report/phase1-summary.html` (Python stdlib only). |
| `tests/test_build_report.py` | pytest tests for the report generator. Only Python file that gets TDD. |

**Directories created on disk (not in git):**

```
/data1/zenigoke/{references,fastq,tmp,status,logs,output/{chipseq,atacseq,bsseq}}/
~/work/zenigoke/{metadata/biosamples,metadata/curated,report}/
```

---

## Task 1: Bootstrap data directories and verify environment

**Goal:** Ensure `/data1/zenigoke/` exists with the right subdirs, Docker can run the pipeline images, and Ollama + qwen3.5:27b are reachable. Fail early if anything is off.

**Files:**
- Create: `scripts/bootstrap.sh`

- [ ] **Step 1: Write `scripts/bootstrap.sh`**

```bash
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
```

- [ ] **Step 2: Make executable and run**

```bash
chmod +x scripts/bootstrap.sh
bash scripts/bootstrap.sh
```

Expected: ends with `== ok ==` and prints free disk. If Ollama is missing, a WARN is fine for this task — we fix it before Task 6. Docker pull takes 1-2 minutes on first run.

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "scripts: add bootstrap.sh for data layout + prerequisite checks"
```

---

## Task 2: Prepare the Marchantia reference genome

**Goal:** Write `prepare-marchantia.sh` that downloads `MpTak_v7.1.fa` and `MpTak_v7.1.gff3` from MarpolBase, builds the three indexes, and computes the MACS3 effective genome size.

**Complication:** The MarpolBase `/data/` path serves a JBrowse SPA for most subpaths, so we cannot hardcode the FASTA filename from documentation — the script must discover it from the directory listing.

**Files:**
- Create: `scripts/prepare-marchantia.sh`

- [ ] **Step 1: Discover the actual filenames in the MpTak_v7.1_standard_genome directory**

Run from a terminal (ad-hoc, not part of the script):

```bash
curl -sL https://marchantia.info/download/MpTak_v7.1/ | grep -iE 'href' | head
# Confirm MpTak_v7.1_standard_genome/ is listed, then:
curl -sL "https://marchantia.info/download/MpTak_v7.1/MpTak_v7.1_standard_genome/" \
     -H 'Accept: text/html' | grep -oE 'href="[^"]+"' | head -20
```

If the subdirectory listing fails (JBrowse SPA returned), fall back to browsing the site manually to get the exact filenames, then paste them into `EXPECTED_FASTA` and `EXPECTED_GFF` below. The whole point of pinning the URL in code (vs hardcoding blindly) is that the script then fetches it deterministically.

- [ ] **Step 2: Write `scripts/prepare-marchantia.sh`**

```bash
#!/usr/bin/env bash
# Download MpTak v7.1 standard genome from MarpolBase and build indexes.
# Idempotent — every step is skipped if its output already exists.
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="$DATA_ROOT/references/MpTak_v7.1"
BASE_URL="https://marchantia.info/download/MpTak_v7.1/MpTak_v7.1_standard_genome"

# Expected filenames — confirm after Step 1 of this task. These are the
# committed defaults; override via env var if MarpolBase renames.
EXPECTED_FASTA="${EXPECTED_FASTA:-MpTak_v7.1.genome.fa.gz}"
EXPECTED_GFF="${EXPECTED_GFF:-MpTak_v7.1.gff3.gz}"

CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"

mkdir -p "$REF_DIR"
cd "$REF_DIR"

echo "== step 1: download FASTA =="
if [ ! -f MpTak_v7.1.fa ]; then
  echo "   fetching $BASE_URL/$EXPECTED_FASTA"
  curl -fsSL -o "$EXPECTED_FASTA" "$BASE_URL/$EXPECTED_FASTA" || {
    echo "ERROR: download failed. Check $BASE_URL for current filenames,"
    echo "       then re-run with EXPECTED_FASTA=<filename> prepare-marchantia.sh"
    exit 1
  }
  gunzip -k "$EXPECTED_FASTA"
  # Normalize to MpTak_v7.1.fa (strip possible .genome suffix)
  uncompressed="${EXPECTED_FASTA%.gz}"
  [ "$uncompressed" != "MpTak_v7.1.fa" ] && mv "$uncompressed" MpTak_v7.1.fa
else
  echo "   MpTak_v7.1.fa present — skip"
fi

echo "== step 2: download GFF3 =="
if [ ! -f MpTak_v7.1.gff3 ]; then
  curl -fsSL -o "$EXPECTED_GFF" "$BASE_URL/$EXPECTED_GFF" || {
    echo "WARN: GFF3 download failed — continuing; Phase 2 may need it later"
  }
  [ -f "$EXPECTED_GFF" ] && gunzip -k "$EXPECTED_GFF" && \
    mv "${EXPECTED_GFF%.gz}" MpTak_v7.1.gff3 2>/dev/null || true
else
  echo "   MpTak_v7.1.gff3 present — skip"
fi

DOCKER="docker run --rm -u $(id -u):$(id -g) -v $REF_DIR:/ref -w /ref"

echo "== step 3: samtools faidx + chrom.sizes =="
if [ ! -f MpTak_v7.1.fa.fai ]; then
  $DOCKER "$CHIP_IMG" samtools faidx MpTak_v7.1.fa
fi
if [ ! -f chrom.sizes ]; then
  cut -f1,2 MpTak_v7.1.fa.fai > chrom.sizes
fi
echo "   chromosomes: $(wc -l < chrom.sizes)"

echo "== step 4: bwa-mem2 index =="
if [ ! -f MpTak_v7.1.fa.bwt.2bit.64 ]; then
  $DOCKER "$CHIP_IMG" bwa-mem2 index MpTak_v7.1.fa
fi

echo "== step 5: abismal index =="
if [ ! -f MpTak_v7.1.abismal.idx ]; then
  docker run --rm -u "$(id -u):$(id -g)" -v "$REF_DIR":/ref -w /ref \
    "$BS_IMG" dnmtools abismalidx MpTak_v7.1.fa MpTak_v7.1.abismal.idx
fi

echo "== step 6: MACS3 effective genome size =="
if [ ! -f macs_gsize.txt ]; then
  # Count non-N bases across the FASTA (pure bash + awk)
  awk 'BEGIN{n=0}
       /^>/ {next}
       {
         s=toupper($0);
         for(i=1;i<=length(s);i++){
           c=substr(s,i,1);
           if(c!="N") n++
         }
       } END{print n}' MpTak_v7.1.fa > macs_gsize.txt
  echo "   MACS3 genome size: $(cat macs_gsize.txt)"
fi

echo "== done =="
ls -lh "$REF_DIR"
```

- [ ] **Step 3: Make executable and run**

```bash
chmod +x scripts/prepare-marchantia.sh
bash scripts/prepare-marchantia.sh
```

Expected: Ends with `== done ==` and a listing showing `MpTak_v7.1.fa`, `MpTak_v7.1.fa.fai`, `chrom.sizes`, `MpTak_v7.1.fa.bwt.2bit.64` (plus companion files), `MpTak_v7.1.abismal.idx`, `macs_gsize.txt`. Total size ~2-5 GB. Runtime ~10-20 minutes (bwa-mem2 index is the long step).

If the download step fails, check the MarpolBase listing, set `EXPECTED_FASTA=<actual>` and `EXPECTED_GFF=<actual>` env vars, and re-run.

- [ ] **Step 4: Sanity check the outputs**

```bash
ls -lh /data1/zenigoke/references/MpTak_v7.1/
head -1 /data1/zenigoke/references/MpTak_v7.1/MpTak_v7.1.fa
wc -l /data1/zenigoke/references/MpTak_v7.1/chrom.sizes
cat /data1/zenigoke/references/MpTak_v7.1/macs_gsize.txt
```

Expected: FASTA first line is `>chr...` or `>Mp...`. chrom.sizes has 8-10 lines (8 autosomes + U + V or similar). macs_gsize is ~2.1-2.3e8 (between 210M and 230M).

- [ ] **Step 5: Commit**

```bash
git add scripts/prepare-marchantia.sh
git commit -m "scripts: add prepare-marchantia.sh (MpTak v7.1 standard + all indexes)"
```

---

## Task 3: Fork the BS-seq pipeline with plant-context outputs

**Goal:** Create `scripts/pipeline-v2-bs-plant.sh`, a verbatim copy of upstream `pipeline-v2-bs.sh` with two functional changes: drop `-cpg-only` and fan out per-context (CpG / CHG / CHH) outputs. Extended stats TSV adds per-context mean methylation columns.

**Files:**
- Create: `scripts/pipeline-v2-bs-plant.sh`

- [ ] **Step 1: Copy the upstream script as the starting point**

```bash
cp ~/repos/chip-atlas-pipeline-v2/scripts/pipeline-v2-bs.sh scripts/pipeline-v2-bs-plant.sh
chmod +x scripts/pipeline-v2-bs-plant.sh
```

- [ ] **Step 2: Modify the `dnmtools counts` invocation**

Open `scripts/pipeline-v2-bs-plant.sh` and find the line that runs `dnmtools counts`. Upstream currently passes `-cpg-only`:

```bash
dnmtools counts -t $THREADS -cpg-only -c "$GENOME_FASTA" "$DEDUP_BAM" | awk '$6 > 0' > "$TMP/counts.tsv"
```

Remove `-cpg-only`:

```bash
dnmtools counts -t $THREADS -c "$GENOME_FASTA" "$DEDUP_BAM" | awk '$6 > 0' > "$TMP/counts.tsv"
```

The resulting `counts.tsv` now has CpG, CHG, and CHH rows (column 4 = context string — exact value depends on dnmtools version; expect `CpG`, `CHG`, `CHH` or similar). Confirm on first run.

- [ ] **Step 3: Replace Step 3 (parallel fan-out) with the per-context block**

In upstream, Step 3 runs `dnmtools sym` once and then `hmr`, `hypermr`, `pmd`, and `bedGraphToBigWig` in parallel. Replace that entire block with:

```bash
log "=== Step 3 (plant): per-context fan-out ==="

# Split counts.tsv by context (column 4)
awk -v c="CpG" '$4 ~ c {print}' "$TMP/counts.tsv" > "$TMP/counts.CpG.tsv"
awk -v c="CHG" '$4 ~ c {print}' "$TMP/counts.tsv" > "$TMP/counts.CHG.tsv"
awk -v c="CHH" '$4 ~ c {print}' "$TMP/counts.tsv" > "$TMP/counts.CHH.tsv"

# CpG: sym → hmr + hypermr + pmd + BigWig
$DOCKER "$BS_IMG" dnmtools sym -t 2 \
  -o "$TMP/counts.CpG.sym.tsv" "$TMP/counts.CpG.tsv"

run_hmm() {
  local ctx="$1" tool="$2" inp="$3"
  $DOCKER "$BS_IMG" dnmtools "$tool" \
    -o "$OUT/${SAMPLE}.${ctx}.${tool}.bed" "$inp"
}

run_bigwig() {
  local ctx="$1" inp="$2"
  sort -k1,1 -k2,2n "$inp" | awk -v M="$TMP/${ctx}.methyl.bg" -v C="$TMP/${ctx}.cover.bg" '{
    print $1"\t"$2"\t"$2+1"\t"$5 > M
    print $1"\t"$2"\t"$2+1"\t"$6 > C
  }'
  $DOCKER "$BIGWIG_IMG" bedGraphToBigWig \
    "/work/$(basename "$TMP")/${ctx}.methyl.bg" /work/hg38/chrom.sizes \
    "/work/$(basename "$OUT")/${SAMPLE}.${ctx}.methyl.bw"
  # NOTE: adapt bind path to match the upstream script's mount layout
}

# Run in parallel
run_hmm CpG hmr "$TMP/counts.CpG.sym.tsv" &
run_hmm CpG hypermr "$TMP/counts.CpG.sym.tsv" &
run_hmm CpG pmd "$TMP/counts.CpG.tsv" &
run_bigwig CpG "$TMP/counts.CpG.tsv" &

run_hmm CHG hmr "$TMP/counts.CHG.tsv" &
run_hmm CHG hypermr "$TMP/counts.CHG.tsv" &
run_bigwig CHG "$TMP/counts.CHG.tsv" &

run_bigwig CHH "$TMP/counts.CHH.tsv" &

wait
```

**Note to the implementer:** The exact variable names and mount paths (`$DOCKER`, `$BS_IMG`, `$BIGWIG_IMG`, `$TMP`, `$OUT`, `$SAMPLE`) must match what the upstream script already defines — don't rewrite the whole script, only replace the body of the existing Step 3. Preserve the `DOCKER=...` setup and bind arguments already at the top of the script.

If upstream mounts are inconsistent with the new output filenames, adjust the bind paths once and verify the first BS-seq sample's outputs land correctly. This is the kind of thing that "moves fast and iterates" — don't perfectionize in the plan; fix on first real run.

- [ ] **Step 4: Extend the stats TSV with per-context means**

After Step 3 completes, before the script's existing stats-writing block, add:

```bash
# Per-context mean methylation (arithmetic mean of meth_fraction across sites with coverage > 0)
mean_meth() {
  awk '{s+=$5; n++} END{if(n>0) printf "%.4f", s/n; else printf "NA"}' "$1"
}
MEAN_CPG=$(mean_meth "$TMP/counts.CpG.tsv")
MEAN_CHG=$(mean_meth "$TMP/counts.CHG.tsv")
MEAN_CHH=$(mean_meth "$TMP/counts.CHH.tsv")
```

Append `MEAN_CPG`, `MEAN_CHG`, `MEAN_CHH` as three new columns to the stats TSV that the upstream script already writes. The final stats format is documented in `docs/superpowers/specs/2026-04-24-zenigoke-phase1-design.md` §5.3.

- [ ] **Step 5: Run on one BS-seq sample (deferred validation — done in Task 8)**

The actual validation run happens in Task 8 (end-to-end validation with one sample per strategy). For now, just commit.

- [ ] **Step 6: Commit**

```bash
git add scripts/pipeline-v2-bs-plant.sh
git commit -m "scripts: fork pipeline-v2-bs.sh as plant-aware CpG/CHG/CHH pipeline"
```

---

## Task 4: Per-sample dispatcher

**Goal:** `run-sample.sh` downloads FASTQ for one accession, routes to the right pipeline, writes status markers, cleans scratch on exit. Isolation: one sample never affects another.

**Files:**
- Create: `scripts/run-sample.sh`

- [ ] **Step 1: Write `scripts/run-sample.sh`**

```bash
#!/usr/bin/env bash
# Process a single SRA experiment: download FASTQ, dispatch to the right
# pipeline, record status.
#
# Usage: run-sample.sh <accession> <library_strategy>
#   library_strategy ∈ {ChIP-Seq, ATAC-Seq, Bisulfite-Seq}
set -euo pipefail

ACCESSION="${1:?need accession}"
STRATEGY="${2:?need library_strategy}"

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="$DATA_ROOT/references/MpTak_v7.1"
PIPELINES_REPO="${PIPELINES_REPO:-$HOME/repos/chip-atlas-pipeline-v2}"
ZENIGOKE_REPO="${ZENIGOKE_REPO:-$HOME/work/zenigoke}"
THREADS="${THREADS:-32}"

CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"

FASTQ_DIR="$DATA_ROOT/fastq/$ACCESSION"
TMP_DIR="$DATA_ROOT/tmp/$ACCESSION"
LOG="$DATA_ROOT/logs/$ACCESSION.log"
STATUS_DIR="$DATA_ROOT/status"

mkdir -p "$FASTQ_DIR" "$TMP_DIR" "$STATUS_DIR" "$(dirname "$LOG")"

cleanup() {
  local rc=$?
  rm -rf "$TMP_DIR"
  if [ $rc -eq 0 ]; then
    rm -rf "$FASTQ_DIR"
    : > "$STATUS_DIR/$ACCESSION.ok"
    rm -f "$STATUS_DIR/$ACCESSION.failed"
  else
    {
      echo "exit_code=$rc"
      echo "strategy=$STRATEGY"
      echo "--- last 50 lines of log ---"
      tail -n 50 "$LOG" 2>/dev/null || echo "(no log)"
    } > "$STATUS_DIR/$ACCESSION.failed"
  fi
}
trap cleanup EXIT

exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Iseconds) start $ACCESSION ($STRATEGY) ==="

# --- FASTQ download ---
# Use the upstream fast-download.sh. It handles SRR/ERR/DRR routing.
# The ENA filereport API tells us run accession(s) for the given experiment.
RUN_ACCS=$(curl -fsSL \
  "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${ACCESSION}&result=read_run&fields=run_accession&format=tsv" \
  | awk 'NR>1 {print $1}')
[ -z "$RUN_ACCS" ] && { echo "ERROR: no runs for $ACCESSION"; exit 2; }

for run in $RUN_ACCS; do
  bash "$PIPELINES_REPO/scripts/fast-download.sh" "$run" "$FASTQ_DIR"
done

FASTQS=( "$FASTQ_DIR"/*.fastq.gz )
[ ! -e "${FASTQS[0]}" ] && FASTQS=( "$FASTQ_DIR"/*.fastq )
[ ! -e "${FASTQS[0]}" ] && { echo "ERROR: no FASTQs downloaded"; exit 3; }

# Detect SE vs PE from filenames (fast-download.sh emits _1/_2 for PE)
FWD=$(ls "$FASTQ_DIR"/*_1.fastq.gz "$FASTQ_DIR"/*_1.fastq 2>/dev/null | head -1 || true)
REV=$(ls "$FASTQ_DIR"/*_2.fastq.gz "$FASTQ_DIR"/*_2.fastq 2>/dev/null | head -1 || true)
if [ -z "$FWD" ]; then
  FWD=$(ls "$FASTQ_DIR"/*.fastq.gz "$FASTQ_DIR"/*.fastq 2>/dev/null | head -1)
fi
echo "   fwd=$FWD  rev=${REV:-<SE>}"

# --- Dispatch ---
case "$STRATEGY" in
  ChIP-Seq|ATAC-Seq)
    OUT_SUBDIR=$([ "$STRATEGY" = "ChIP-Seq" ] && echo chipseq || echo atacseq)
    OUT_DIR="$DATA_ROOT/output/$OUT_SUBDIR/$ACCESSION"
    mkdir -p "$OUT_DIR"
    GSIZE=$(cat "$REF_DIR/macs_gsize.txt")
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome-fasta "$REF_DIR/MpTak_v7.1.fa"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --genome-size "$GSIZE"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    bash "$PIPELINES_REPO/scripts/pipeline-v2.sh" "${ARGS[@]}"
    ;;

  Bisulfite-Seq)
    OUT_DIR="$DATA_ROOT/output/bsseq/$ACCESSION"
    mkdir -p "$OUT_DIR"
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome MpTak_v7.1
      --genome-fasta "$REF_DIR/MpTak_v7.1.fa"
      --abismal-index "$REF_DIR/MpTak_v7.1.abismal.idx"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    bash "$ZENIGOKE_REPO/scripts/pipeline-v2-bs-plant.sh" "${ARGS[@]}"
    ;;

  *)
    echo "ERROR: unknown library_strategy: $STRATEGY"
    exit 4
    ;;
esac

echo "=== $(date -Iseconds) end $ACCESSION ok ==="
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/run-sample.sh
```

- [ ] **Step 3: Smoke test (dry-ish — accession validation only)**

```bash
# Just verify argument parsing and that it reaches the curl call
bash -n scripts/run-sample.sh && echo "syntax ok"
# Actual run waits for Task 8.
```

Expected: `syntax ok`.

- [ ] **Step 4: Commit**

```bash
git add scripts/run-sample.sh
git commit -m "scripts: add run-sample.sh dispatcher with PE/SE detection and cleanup trap"
```

---

## Task 5: Top-level driver

**Goal:** `run-all.sh` iterates the CSV and calls `run-sample.sh` for each row, with `--resume`, `--only`, and `--library-strategy` flags. Failures never stop the loop.

**Files:**
- Create: `scripts/run-all.sh`

- [ ] **Step 1: Write `scripts/run-all.sh`**

```bash
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
STATUS_DIR="$DATA_ROOT/status"
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
```

- [ ] **Step 2: Make executable and test the argument parser**

```bash
chmod +x scripts/run-all.sh
bash -n scripts/run-all.sh && echo "syntax ok"
```

Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add scripts/run-all.sh
git commit -m "scripts: add run-all.sh with --resume/--only/--library-strategy"
```

---

## Task 6: Metadata curation

**Goal:** Fetch BioSamples for every accession and run bsllmner-mk2 Select mode with the merged plant config + our custom extract-only config.

**Files:**
- Create: `configs/select-config-zenigoke.json`
- Create: `scripts/curate-metadata.sh`

- [ ] **Step 1: Write `configs/select-config-zenigoke.json`**

```bash
mkdir -p configs
```

```json
{
  "fields": {
    "developmental_stage": {
      "prompt_description": "The developmental stage of the Marchantia polymorpha sample. Typical values include: thallus (vegetative body), gemma (asexual propagule), gemmaling (young thallus from gemma), antheridiophore (male reproductive organ), archegoniophore (female reproductive organ), sporophyte, spore, protonema. Use the most specific stage described; return null if not stated."
    },
    "genotype_strain": {
      "prompt_description": "The genotype or strain identifier of the Marchantia polymorpha sample. Common strains: Tak-1 (male), Tak-2 (female), Cam-1, Cam-2, BR5, SA2. Mutant lines often include a gene name and allele number (e.g., Mpgs1-1, Mparf1-ko). Return the most specific strain or genotype stated; null if not present."
    },
    "treatment": {
      "prompt_description": "The experimental treatment applied to the sample. Common categories: hormone (auxin, cytokinin, ABA, GA), light (far-red, blue, red, darkness), stress (heat, cold, drought, osmotic), mock/control, untreated. Return a short phrase summarizing the treatment or 'control' if explicitly a control; null otherwise."
    },
    "antibody_target": {
      "prompt_description": "For ChIP-Seq experiments only: the target protein or histone modification of the antibody used. Examples: H3K4me3, H3K27me3, H3K9me2, MpGCAM1, MpTCP1, RNA Pol II, input (for input controls). Return the target name verbatim; null for non-ChIP samples or when the target is not stated."
    }
  }
}
```

- [ ] **Step 2: Write `scripts/curate-metadata.sh`**

```bash
#!/usr/bin/env bash
# Fetch BioSample records for each SRA experiment, then run bsllmner-mk2
# Select mode using the plant config + our local extract-only config.
set -euo pipefail

CSV="${CSV:-zenigoke_sra_experiments.csv}"
BIOSAMPLES_DIR="metadata/biosamples"
CURATED_DIR="metadata/curated"
LOG="metadata/curation.log"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:27b}"

# Where bsllmner-mk2 is cloned. Defaults to ~/repos/bsllmner-mk2.
BSLLMNER_REPO="${BSLLMNER_REPO:-$HOME/repos/bsllmner-mk2}"

mkdir -p "$BIOSAMPLES_DIR" "$CURATED_DIR"
: > "$LOG"

# --- Stage 1: fetch BioSample records ---
echo "== stage 1: fetch BioSample records =="
while IFS=, read -r strategy accession; do
  [ "$strategy" = "library_strategy" ] && continue
  [ -z "$accession" ] && continue
  out="$BIOSAMPLES_DIR/${accession}.json"
  [ -s "$out" ] && continue

  # SRX/DRX/ERX → sample_accession (SAMN/SAMEA/SAMD)
  biosample=$(curl -fsSL \
    "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${accession}&result=read_run&fields=sample_accession&format=tsv" \
    | awk 'NR==2{print $1}' || true)
  [ -z "$biosample" ] && {
    echo "WARN: no biosample for $accession" | tee -a "$LOG"
    continue
  }

  # Fetch BioSample JSON from ENA (pragmatically: ENA returns a TSV; NCBI's
  # biosample endpoint returns XML. bsllmner-mk2 expects a JSON blob — we
  # wrap whatever ENA gives into a JSON with the sample_accession and the
  # raw attributes).
  curl -fsSL \
    "https://www.ebi.ac.uk/ena/browser/api/xml/${biosample}" \
    -o "${BIOSAMPLES_DIR}/${accession}.xml" || {
      echo "WARN: fetch failed for $biosample ($accession)" | tee -a "$LOG"
      continue
    }
  # Convert XML → JSON (minimal — bsllmner2_extract accepts the raw XML string
  # as an 'attributes' field; confirm on first run and adjust if needed)
  python3 - "$biosample" "${BIOSAMPLES_DIR}/${accession}.xml" "$out" <<'PY'
import sys, json, xml.etree.ElementTree as ET
acc, xml_path, out_path = sys.argv[1:4]
tree = ET.parse(xml_path)
root = tree.getroot()
attrs = {}
for a in root.iter("SAMPLE_ATTRIBUTE"):
    tag = a.findtext("TAG") or ""
    val = a.findtext("VALUE") or ""
    if tag: attrs[tag] = val
title = root.findtext(".//TITLE") or ""
desc = root.findtext(".//DESCRIPTION") or ""
json.dump(
  {"accession": acc, "title": title, "description": desc, "attributes": attrs},
  open(out_path, "w"), indent=2
)
PY
done < "$CSV"

# --- Stage 2: run bsllmner-mk2 Select mode ---
echo "== stage 2: run bsllmner-mk2 Select =="
test -d "$BSLLMNER_REPO" || {
  echo "ERROR: bsllmner-mk2 not cloned at $BSLLMNER_REPO. Clone it:"
  echo "   git clone https://github.com/dbcls/bsllmner-mk2.git $BSLLMNER_REPO"
  exit 1
}

# Stand up bsllmner-mk2 via docker compose
(cd "$BSLLMNER_REPO" && docker compose up -d --build)

# Bundle all biosample JSONs into a single input file (bsllmner2 format)
python3 - > /tmp/bs_input.json <<PY
import json, glob, os
items = []
for f in sorted(glob.glob("$BIOSAMPLES_DIR/*.json")):
    items.append(json.load(open(f)))
json.dump(items, open("/tmp/bs_input.json", "w"))
PY

# Run Select mode with BOTH configs (plant + our custom)
# The exact CLI mirrors the bsllmner-mk2 README. Adjust on first run.
docker compose -f "$BSLLMNER_REPO/docker-compose.yml" exec -T app \
  bsllmner2_select \
    --bs-entries /tmp/bs_input.json \
    --select-config "$BSLLMNER_REPO/scripts/select-config-plants.json" \
    --extra-config /configs/select-config-zenigoke.json \
    --model "$OLLAMA_MODEL" \
    --output /tmp/bs_output.json

# Split the monolithic output back into per-sample files
python3 - <<PY
import json, os
data = json.load(open("/tmp/bs_output.json"))
outdir = "$CURATED_DIR"
os.makedirs(outdir, exist_ok=True)
for item in data:
    acc = item.get("accession")
    if not acc: continue
    json.dump(item, open(f"{outdir}/{acc}.json", "w"), indent=2)
print(f"wrote {len(data)} curated files to {outdir}")
PY
```

- [ ] **Step 3: Make executable**

```bash
chmod +x scripts/curate-metadata.sh
```

**Note:** This script has pragmatic uncertainty in two places that the first run will resolve:

1. The exact `bsllmner2_select` CLI flags — confirm against bsllmner-mk2's latest `docs/select-mode.md`. The flags for `--select-config`, `--extra-config`, `--bs-entries`, and `--output` may differ; `--bs-entries-json` vs `--bs-entries`, etc.
2. The BioSample JSON structure bsllmner-mk2 expects — confirm against their `tests/data/example_biosample.json` and reshape the converter if needed.

These are the "move fast and iterate" bits — don't block here, fix during the first real curation run.

- [ ] **Step 4: Clone bsllmner-mk2 if not present**

```bash
test -d ~/repos/bsllmner-mk2 || \
  git clone https://github.com/dbcls/bsllmner-mk2.git ~/repos/bsllmner-mk2
```

- [ ] **Step 5: Commit**

```bash
git add scripts/curate-metadata.sh configs/select-config-zenigoke.json
git commit -m "scripts+configs: add metadata curation (BioSample fetch + bsllmner-mk2 Select)"
```

---

## Task 7: Summary report generator (Python, TDD)

**Goal:** `build-report.py` walks the output and metadata trees, writes a single self-contained `report/phase1-summary.html`. Python stdlib only. This is the one component that gets real tests.

**Files:**
- Create: `scripts/build_report.py` (underscore, for importability in tests)
- Create: `scripts/build-report.py` (thin CLI wrapper that imports build_report)
- Create: `tests/test_build_report.py`

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p tests scripts
```

`tests/test_build_report.py`:

```python
"""Tests for build_report. We build a tiny fake output tree and check the
generated HTML contains expected rows + gracefully handles missing data."""
from __future__ import annotations
import json
import pathlib
import sys
import tempfile
import textwrap
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from build_report import collect_samples, render_html  # noqa: E402


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_collect_samples_finds_ok_and_failed(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")
    (status / "SRX2.failed").write_text("exit_code=3\nstrategy=ChIP-Seq\n--- last 50 lines of log ---\nboom")

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    accs = {s["accession"] for s in samples}
    assert accs == {"SRX1", "SRX2"}
    by = {s["accession"]: s for s in samples}
    assert by["SRX1"]["status"] == "ok"
    assert by["SRX2"]["status"] == "failed"
    assert by["SRX2"]["exit_code"] == "3"


def test_collect_samples_reads_chipseq_stats(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")

    # Fake 15-column chipseq stats TSV from pipeline-v2.sh
    stats_dir = tmp_path / "output" / "chipseq" / "SRX1"
    _write(
        stats_dir / "SRX1.stats.tsv",
        "SRX1\tPE\t1.2G\t10000000\t9500000\t0.92\t0.11\t500M\t200M\t50M\t1200\t300\t80\t5.3\n",
    )

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    s = samples[0]
    assert s["mapping_rate"] == "0.92"
    assert s["peaks_q10"] == "300"


def test_collect_samples_merges_curated_metadata(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")

    curated_dir = tmp_path / "metadata" / "curated"
    _write(
        curated_dir / "SRX1.json",
        json.dumps({"accession": "SRX1", "tissue": "thallus",
                    "developmental_stage": "thallus", "antibody_target": "H3K4me3"}),
    )

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    s = samples[0]
    assert s["curated"]["tissue"] == "thallus"
    assert s["curated"]["antibody_target"] == "H3K4me3"


def test_render_html_produces_valid_html_skeleton(tmp_path):
    samples = [
        {"accession": "SRX1", "status": "ok", "strategy": "ChIP-Seq",
         "mapping_rate": "0.92", "peaks_q10": "300", "curated": {"tissue": "thallus"}},
        {"accession": "SRX2", "status": "failed", "strategy": "Bisulfite-Seq",
         "exit_code": "3", "log_snippet": "boom", "curated": {}},
    ]
    html = render_html(samples)
    assert html.startswith("<!DOCTYPE html>")
    assert "SRX1" in html and "SRX2" in html
    assert "thallus" in html
    assert "boom" in html  # failure snippet appears
    assert "</html>" in html
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd ~/work/zenigoke
python3 -m pytest tests/test_build_report.py -v
```

Expected: ModuleNotFoundError or similar — `build_report` doesn't exist yet.

- [ ] **Step 3: Write `scripts/build_report.py`**

```python
"""Collect zenigoke pipeline outputs + curated metadata, render an HTML report.

Stdlib only. Two public entry points:
  collect_samples(...)  -> list[dict]
  render_html(samples)  -> str
"""
from __future__ import annotations

import csv
import html
import json
import pathlib
from typing import Any

STRATEGY_DIRS = {
    "ChIP-Seq": "chipseq",
    "ATAC-Seq": "atacseq",
    "Bisulfite-Seq": "bsseq",
}


def _read_stats_tsv(path: pathlib.Path) -> list[str]:
    """Return the single data row split into fields, or empty list if missing."""
    if not path.exists():
        return []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            return line.split("\t")
    return []


def _read_failed(path: pathlib.Path) -> dict[str, str]:
    info: dict[str, str] = {"exit_code": "", "strategy": "", "log_snippet": ""}
    if not path.exists():
        return info
    text = path.read_text()
    snippet_lines: list[str] = []
    in_snippet = False
    for line in text.splitlines():
        if line.startswith("exit_code="):
            info["exit_code"] = line.split("=", 1)[1].strip()
        elif line.startswith("strategy="):
            info["strategy"] = line.split("=", 1)[1].strip()
        elif "last 50 lines of log" in line:
            in_snippet = True
        elif in_snippet:
            snippet_lines.append(line)
    info["log_snippet"] = "\n".join(snippet_lines).strip()
    return info


def _strategy_from_output(output_root: pathlib.Path, acc: str) -> str | None:
    for strat, sub in STRATEGY_DIRS.items():
        if (output_root / sub / acc).exists():
            return strat
    return None


def _chipseq_stats_fields(row: list[str]) -> dict[str, str]:
    # Upstream pipeline-v2.sh stats.tsv is 15 columns; see pipeline README.
    keys = [
        "sample", "layout", "fastq_size", "reads_raw", "reads_filt",
        "mapping_rate", "duplication_rate", "dedup_bam_size",
        "bedgraph_size", "bigwig_size", "peaks_q5", "peaks_q10",
        "peaks_q20", "elapsed_min", "extra",
    ]
    return {k: v for k, v in zip(keys, row)}


def _bsseq_stats_fields(row: list[str]) -> dict[str, str]:
    # Upstream 11 columns + our 3 extended columns (MEAN_CPG/CHG/CHH).
    keys = [
        "sample", "layout", "fastq_size", "dedup_bam_size", "read_count",
        "mapping_rate", "methylation_rate", "cpg_coverage",
        "hmr_count", "pmd_count", "hypermr_count", "elapsed_min",
        "mean_cpg", "mean_chg", "mean_chh",
    ]
    return {k: v for k, v in zip(keys, row)}


def collect_samples(
    data_root: pathlib.Path,
    output_root: pathlib.Path,
    metadata_root: pathlib.Path,
    csv_path: pathlib.Path | None,
) -> list[dict[str, Any]]:
    status_dir = data_root / "status"
    curated_dir = metadata_root / "curated"

    # Seed strategy from CSV when provided (so failed samples still know type)
    csv_strategies: dict[str, str] = {}
    if csv_path and csv_path.exists():
        with csv_path.open() as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                csv_strategies[row["experiment_accession"]] = row["library_strategy"]

    samples: list[dict[str, Any]] = []
    if not status_dir.exists():
        return samples

    for marker in sorted(status_dir.iterdir()):
        acc = marker.stem
        if marker.suffix == ".ok":
            status = "ok"
        elif marker.suffix == ".failed":
            status = "failed"
        else:
            continue

        sample: dict[str, Any] = {"accession": acc, "status": status}
        strat = _strategy_from_output(output_root, acc) or csv_strategies.get(acc, "")

        if status == "failed":
            sample.update(_read_failed(marker))
            sample.setdefault("strategy", strat)
        else:
            sample["strategy"] = strat
            if strat in ("ChIP-Seq", "ATAC-Seq"):
                stats = _read_stats_tsv(
                    output_root / STRATEGY_DIRS[strat] / acc / f"{acc}.stats.tsv"
                )
                sample.update(_chipseq_stats_fields(stats))
            elif strat == "Bisulfite-Seq":
                stats = _read_stats_tsv(
                    output_root / "bsseq" / acc / f"{acc}.stats.tsv"
                )
                sample.update(_bsseq_stats_fields(stats))

        curated_path = curated_dir / f"{acc}.json"
        sample["curated"] = (
            json.loads(curated_path.read_text()) if curated_path.exists() else {}
        )
        samples.append(sample)

    return samples


def _esc(x: Any) -> str:
    return html.escape(str(x)) if x is not None else ""


def render_html(samples: list[dict[str, Any]]) -> str:
    n_total = len(samples)
    n_ok = sum(1 for s in samples if s["status"] == "ok")
    n_failed = n_total - n_ok

    by_strat: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        by_strat.setdefault(s.get("strategy") or "unknown", []).append(s)

    parts: list[str] = []
    parts.append(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>zenigoke phase 1 summary</title>"
        "<style>"
        "body{font:14px/1.4 system-ui,sans-serif;margin:2rem;color:#222}"
        "h1{font-size:1.5rem}h2{margin-top:2rem;font-size:1.2rem}"
        "table{border-collapse:collapse;margin:0.5rem 0;font-size:12px}"
        "th,td{border:1px solid #ccc;padding:0.25rem 0.5rem;text-align:left}"
        "th{background:#f4f4f4}.failed{background:#fee}.ok{background:#efe}"
        "pre{font:11px monospace;background:#f8f8f8;padding:0.25rem;max-width:40em;white-space:pre-wrap}"
        "</style></head><body>"
    )
    parts.append(
        f"<h1>zenigoke phase 1 summary</h1>"
        f"<p>{n_total} samples — {n_ok} ok, {n_failed} failed.</p>"
    )

    if n_failed:
        parts.append("<h2>Failed samples</h2>")
        parts.append("<table><tr><th>accession</th><th>strategy</th><th>exit</th><th>log snippet</th></tr>")
        for s in samples:
            if s["status"] != "failed":
                continue
            parts.append(
                f"<tr class='failed'><td>{_esc(s['accession'])}</td>"
                f"<td>{_esc(s.get('strategy'))}</td>"
                f"<td>{_esc(s.get('exit_code'))}</td>"
                f"<td><pre>{_esc(s.get('log_snippet'))}</pre></td></tr>"
            )
        parts.append("</table>")

    for strat, rows in by_strat.items():
        ok_rows = [r for r in rows if r["status"] == "ok"]
        if not ok_rows:
            continue
        parts.append(f"<h2>{_esc(strat)} ({len(ok_rows)} samples)</h2>")
        # Column set picked per strategy
        if strat in ("ChIP-Seq", "ATAC-Seq"):
            cols = ["accession", "mapping_rate", "duplication_rate",
                    "peaks_q5", "peaks_q10", "peaks_q20",
                    "antibody_target", "tissue", "developmental_stage",
                    "genotype_strain", "elapsed_min"]
        elif strat == "Bisulfite-Seq":
            cols = ["accession", "mapping_rate", "mean_cpg", "mean_chg", "mean_chh",
                    "hmr_count", "pmd_count", "tissue", "developmental_stage",
                    "genotype_strain", "elapsed_min"]
        else:
            cols = ["accession", "strategy"]
        parts.append("<table><tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>")
        for r in ok_rows:
            cur = r.get("curated") or {}
            vals = []
            for c in cols:
                if c in ("tissue", "developmental_stage", "genotype_strain",
                         "treatment", "antibody_target"):
                    vals.append(cur.get(c, "") or "")
                else:
                    vals.append(r.get(c, "") or "")
            parts.append("<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in vals) + "</tr>")
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", default="/data1/zenigoke")
    p.add_argument("--metadata-root", default="metadata")
    p.add_argument("--csv", default="zenigoke_sra_experiments.csv")
    p.add_argument("--output", default="report/phase1-summary.html")
    args = p.parse_args(argv)

    data_root = pathlib.Path(args.data_root)
    metadata_root = pathlib.Path(args.metadata_root)
    csv_path = pathlib.Path(args.csv) if args.csv else None
    output = pathlib.Path(args.output)

    samples = collect_samples(data_root, data_root / "output", metadata_root, csv_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(samples))
    print(f"wrote {output} with {len(samples)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write thin CLI wrapper `scripts/build-report.py`**

```python
#!/usr/bin/env python3
"""CLI wrapper (dashes in the name mean this isn't importable; the underscored
module is the real code; this just forwards argv)."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_report import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests**

```bash
python3 -m pytest tests/test_build_report.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
chmod +x scripts/build-report.py
git add scripts/build_report.py scripts/build-report.py tests/test_build_report.py
git commit -m "scripts+tests: add build_report (HTML summary, stdlib only, TDD)"
```

---

## Task 8: End-to-end validation on three samples

**Goal:** Prove each dispatch path works end-to-end on one sample per strategy before launching the full 157-sample run. This is the gate before Task 9.

**Samples** (picked from `zenigoke_sra_experiments.csv`, first of each kind):
- ChIP-Seq: `SRX7548553`
- ATAC-Seq: `SRX7548538`
- Bisulfite-Seq: `DRX162964`

- [ ] **Step 1: Run one ChIP-Seq sample**

```bash
cd ~/work/zenigoke
bash scripts/run-all.sh --only SRX7548553
ls /data1/zenigoke/output/chipseq/SRX7548553/
cat /data1/zenigoke/status/SRX7548553.ok || cat /data1/zenigoke/status/SRX7548553.failed
```

Expected: `.ok` marker. Outputs: `{id}.bw`, `{id}.05_peaks.narrowPeak`, `{id}.10_peaks.narrowPeak`, `{id}.20_peaks.narrowPeak`, `.bb` files, `{id}_fastp.json`, `{id}.stats.tsv`. If `.failed`, read the log at `/data1/zenigoke/logs/SRX7548553.log` and fix before proceeding.

- [ ] **Step 2: Run one ATAC-Seq sample**

```bash
bash scripts/run-all.sh --only SRX7548538
ls /data1/zenigoke/output/atacseq/SRX7548538/
```

Expected: same output structure as ChIP-Seq.

- [ ] **Step 3: Run one Bisulfite-Seq sample**

```bash
bash scripts/run-all.sh --only DRX162964
ls /data1/zenigoke/output/bsseq/DRX162964/
```

Expected: `.ok` marker and these outputs:
- `DRX162964.CpG.methyl.bw`, `DRX162964.CpG.cover.bw`
- `DRX162964.CpG.hmr.bed`, `DRX162964.CpG.hypermr.bed`, `DRX162964.CpG.pmd.bed`
- `DRX162964.CHG.methyl.bw`, `DRX162964.CHG.cover.bw`
- `DRX162964.CHG.hmr.bed`, `DRX162964.CHG.hypermr.bed`
- `DRX162964.CHH.methyl.bw`, `DRX162964.CHH.cover.bw`
- `DRX162964.stats.tsv` (extended with `mean_cpg`, `mean_chg`, `mean_chh`)

**Expected sanity check on CHH mean:** Marchantia CHH methylation is typically 5-15%. If `mean_chh` is ~0 or ~1.0, the context filtering in `pipeline-v2-bs-plant.sh` is wrong — check the `awk $4 ~ c` splits.

- [ ] **Step 4: Run curation on the three BioSamples**

```bash
# Shrink the CSV temporarily to the three samples (one-liner, don't commit)
head -1 zenigoke_sra_experiments.csv > /tmp/three.csv
grep -E "SRX7548553|SRX7548538|DRX162964" zenigoke_sra_experiments.csv >> /tmp/three.csv
CSV=/tmp/three.csv bash scripts/curate-metadata.sh
ls metadata/curated/
cat metadata/curated/SRX7548553.json
```

Expected: three JSONs in `metadata/curated/`. SRX7548553 (a ChIP-Seq) should have `antibody_target` populated; others null.

- [ ] **Step 5: Build the report on the three-sample run**

```bash
python3 scripts/build-report.py
open report/phase1-summary.html 2>/dev/null || echo "open manually at report/phase1-summary.html"
```

Expected: HTML report with three rows (one per strategy section). No crashes on missing fields.

- [ ] **Step 6: Commit the three-sample report (artifact reference)**

No code changes — the artifacts live under `/data1/` and aren't committed. Just commit the design confirmation note:

```bash
# No-op commit unless the validation surfaced script changes.
git status
# If there are fix-up edits: add + commit them with "fix(v2-plant): <what>".
```

---

## Task 9: Full 157-sample run + report

**Goal:** Kick off the full run, monitor, handle failures, rebuild report.

- [ ] **Step 1: Verify the 3-sample outputs are clean (gate from Task 8)**

Don't proceed until Task 8's report shows all three sample rows without "failed".

- [ ] **Step 2: Clear the three validation `.ok` markers OR use `--resume`**

Use `--resume`: it skips the three already-done samples and processes the remaining 154.

```bash
bash scripts/run-all.sh --resume
```

This will run for ~12-16 hours. Use `tmux` or `nohup` if you need to disconnect.

- [ ] **Step 3: In parallel (or after), run curation for all 157**

```bash
# Assumes bsllmner-mk2 + qwen3.5:27b healthy; may take hours on CPU
bash scripts/curate-metadata.sh
```

- [ ] **Step 4: Build the final report**

```bash
python3 scripts/build-report.py
ls -lh report/phase1-summary.html
```

Expected: single HTML file, likely 200-500 KB.

- [ ] **Step 5: Inspect, triage failures, iterate**

Open the report. Failed rows show exit code + log tail. Common fixes:
- MACS3 peak model fails on low-signal samples → accept as-is, not a bug.
- abismal OOM on large paired-end → re-run with a smaller `--threads` for that one sample.
- ENA download 404 → older accession, usually needs SRA fasterq-dump fallback.

Re-run just the failed: `bash scripts/run-all.sh --only <accession>`.

- [ ] **Step 6: Commit the spec-to-reality delta**

If Tasks 3/6/7 needed fixes during the full run, they should already be committed. If anything else changed (e.g. new script, tweaked config), commit now:

```bash
git status
git add -A
git commit -m "phase1: fixes from full-run validation"
```

- [ ] **Step 7: Tag the Phase-1 completion point**

```bash
git tag -a phase1-complete -m "Phase 1: 157 samples processed, curated, reported"
git log --oneline | head -15
```

End state: all succeeded samples visible in the report, failures triaged (either fixed or documented as out-of-scope for Phase 1), spec/plan ready for Phase 2 discussion.

---

## Post-run: what Phase 2 discussion needs

(Not a task — a handoff note.) After Phase 1 lands:

- Inspect the report for which axes (antibody × stage × strain × tissue) have enough samples to make DB filtering useful.
- Decide the query shape (genome-range queries? by antibody? by methylation region overlap?) — drives the DB schema.
- Confirm whether the DB is a local SQLite (simple, one-machine) or a server (Postgres + API + web UI).

None of that is in scope for this plan.
