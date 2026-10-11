#!/usr/bin/env bash
# Process a single SRA experiment: download FASTQ, dispatch to the right
# pipeline, record status.
#
# Usage: run-sample.sh <accession> <library_strategy>
#   library_strategy ∈ {ChIP-Seq, ATAC-Seq, Bisulfite-Seq}
#
# Runs come from the ENA filereport, else from NCBI E-utilities runinfo. Each run
# is fetched from ENA (fast-download.sh), else as a .sra from the DDBJ mirror,
# else by fasterq-dump from NCBI; the log says which ("download source: RUN src").
# SKIP_ENA=1 skips both ENA steps (use it while the ENA portal API is down).
set -euo pipefail

ACCESSION="${1:?need accession}"
STRATEGY="${2:?need library_strategy}"

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

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="${REF_DIR:-$DATA_ROOT/references/MpTak_v7.1_org}"
REF_NAME="${REF_NAME:-MpTak_v7.1_org}"
# OUTPUT_ROOT, STATUS_DIR and LOG_DIR must live under DATA_ROOT: only DATA_ROOT
# is bind-mounted into the pipeline containers.
OUTPUT_ROOT="${OUTPUT_ROOT:-$DATA_ROOT/output}"
STATUS_DIR="${STATUS_DIR:-$DATA_ROOT/status}"
LOG_DIR="${LOG_DIR:-$DATA_ROOT/logs}"
PIPELINES_REPO="${PIPELINES_REPO:-$HOME/repos/chip-atlas-pipeline-v2}"
ZENIGOKE_REPO="${ZENIGOKE_REPO:-$HOME/work/zenigoke}"
BLACKLIST="$ZENIGOKE_REPO/configs/organelle_like_regions.MpTak_v7.1.bed"
# 32 threads reproduced a bwa-mem2 SEQ/QUAL mismatch on SRX29617452; 16 did not.
THREADS="${THREADS:-16}"

SKIP_ENA="${SKIP_ENA:-0}"
# fasterq-dump threads, bounded: conversion is I/O-bound past a few threads.
SRA_THREADS=$(( THREADS < 8 ? THREADS : 8 ))

CHIP_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0"
BS_IMG="ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0"
SRA_IMG="quay.io/biocontainers/sra-tools:3.0.10--h9f5acd7_0"

FASTQ_DIR="$DATA_ROOT/fastq/$ACCESSION"
TMP_DIR="$DATA_ROOT/tmp/$ACCESSION"
LOG="$LOG_DIR/$ACCESSION.log"

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

# verify_md5 FILE EXPECTED: remove FILE and fail unless its md5 equals EXPECTED.
verify_md5() {
  local got
  got=$(md5sum "$1" | cut -d' ' -f1)
  if [ -z "$2" ] || [ "$got" != "$2" ]; then
    echo "ERROR: md5 mismatch for $1 (got $got, ENA ${2:-<none>}); file removed"
    rm -f "$1"
    return 1
  fi
  echo "   md5 ok: $(basename "$1") $got"
}

# The download helpers below are called from `if`, where set -e is off, so every
# step that can fail carries its own `|| return 1`.

# ena_runs: the experiment's run accessions from the ENA filereport. Fails unless
# the reply is a TSV with a run_accession header and a run accession under it
# (since 2026-10-03 the portal answers 400/500, or 200 with an error text).
ena_runs() {
  local body
  body=$(curl -fsSL --retry 5 --retry-delay 10 --max-time 120 \
    "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${ACCESSION}&result=read_run&fields=run_accession&format=tsv") \
    || return 1
  printf '%s\n' "$body" | awk -F'\t' '
    NR==1 {for (i=1;i<=NF;i++) if ($i=="run_accession") c=i; next}
    c && $c ~ /^[SED]RR[0-9]+$/ {print $c; n++}
    END {exit n ? 0 : 1}'
}

# ncbi_runs: "<run>\t<LibraryLayout>" per run of the experiment, from NCBI
# E-utilities runinfo. The CSV quotes fields that contain commas, so it is read
# with Python's csv module, by header name.
ncbi_runs() {
  local ids
  ids=$(curl -fsSL --retry 5 --retry-delay 10 --max-time 120 \
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=sra&term=${ACCESSION}&retmode=json" \
    | python3 -c 'import json, sys; print(",".join(json.load(sys.stdin)["esearchresult"]["idlist"]))') \
    || return 1
  [ -n "$ids" ] || return 1
  curl -fsSL --retry 5 --retry-delay 10 --max-time 120 \
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=sra&id=${ids}&rettype=runinfo&retmode=text" \
    | python3 -c '
import csv, re, sys
exp, n = sys.argv[1], 0
for row in csv.DictReader(sys.stdin):
    run = (row.get("Run") or "").strip()
    if re.fullmatch(r"[SED]RR[0-9]+", run) and (row.get("Experiment") or exp) == exp:
        print(run + "\t" + (row.get("LibraryLayout") or "").strip())
        n += 1
sys.exit(0 if n else 1)' "$ACCESSION"
}

# clean_run RUN: remove RUN's FASTQs and partials (.aria2, .part) from FASTQ_DIR.
# The names are exact, so SRR1 never matches SRR10.
clean_run() {
  local f
  for f in "$FASTQ_DIR/$1".fastq* "$FASTQ_DIR/$1"_*.fastq*; do rm -f "$f"; done
}

# has_fastq RUN: FASTQ_DIR holds a non-empty FASTQ for RUN.
has_fastq() {
  local f
  for f in "$FASTQ_DIR/$1".fastq.gz "$FASTQ_DIR/$1"_1.fastq.gz "$FASTQ_DIR/$1".fastq "$FASTQ_DIR/$1"_1.fastq; do
    [ -s "$f" ] && return 0
  done
  return 1
}

# ena_download RUN: the ENA path. ENA lists a single FASTQ for some runs labelled
# PAIRED (SRX29617444-455): fetch that file directly (md5-checked) and run it as
# SE; anything else goes through fast-download.sh.
ena_download() {
  local run=$1 report LAYOUT REST URLS MD5
  # ENA always prepends run_accession to the requested fields, so pick the
  # columns by header name rather than position.
  report=$(curl -fsSL --retry 5 --retry-delay 10 --max-time 120 \
        "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${run}&result=read_run&fields=library_layout,fastq_ftp,fastq_md5&format=tsv" \
        | awk -F'\t' -v run="$run" '
            NR==1 {for (i=1;i<=NF;i++) c[$i]=i}
            NR==2 && c["run_accession"] && c["library_layout"] && c["fastq_ftp"] && c["fastq_md5"] &&
              $c["run_accession"]==run {print $c["library_layout"]"\t"$c["fastq_ftp"]"\t"$c["fastq_md5"]; ok=1}
            END {exit ok ? 0 : 1}') \
    || { echo "   $run: no usable ENA filereport"; return 1; }
  LAYOUT=${report%%$'\t'*}; REST=${report#*$'\t'}; URLS=${REST%%$'\t'*}; MD5=${REST#*$'\t'}
  if [ "$LAYOUT" = "PAIRED" ] && [ "$(echo "$URLS" | tr ';' '\n' | grep -c .)" = "1" ]; then
    echo "   $run: PAIRED with a single FASTQ on ENA → direct download, run as SE"
    aria2c --max-tries=10 --retry-wait=10 --split=4 --max-connection-per-server=4 \
      --continue=true --allow-overwrite=true --auto-file-renaming=false \
      --console-log-level=warn --dir="$FASTQ_DIR" --out="$(basename "$URLS")" "https://$URLS" \
      || return 1
    verify_md5 "$FASTQ_DIR/$(basename "$URLS")" "$MD5" || return 1
  else
    bash "$PIPELINES_REPO/scripts/fast-download.sh" "$run" "$FASTQ_DIR" || return 1
  fi
}

# sra_tools CMD...: run an sra-tools command with only TMP_DIR mounted (its HOME,
# for the vdb config, is there too).
sra_tools() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME="$TMP_DIR" \
    -v "$TMP_DIR:$TMP_DIR" -w "$TMP_DIR" "$SRA_IMG" "$@"
}

# gz_into SRC DEST: compress SRC to DEST through DEST.part, then remove SRC.
gz_into() {
  if command -v pigz >/dev/null; then pigz -p "$THREADS" -c "$1"; else gzip -c "$1"; fi > "$2.part" \
    && mv "$2.part" "$2" && rm -f "$1"
}

# dump_fastq RUN SRC: fasterq-dump SRC (a local .sra, or RUN itself to fetch it
# from NCBI) and gzip the reads into FASTQ_DIR under fast-download.sh's names:
# <RUN>_1/_2.fastq.gz (PE) or <RUN>.fastq.gz (SE). Unpaired leftovers of a PE run
# are dropped, so the SE/PE detection below sees exactly one pair. --split-files
# writes a read whose mate is empty without that mate, so _1 and _2 must agree.
dump_fastq() {
  local run=$1 src=$2 out="$TMP_DIR/fasterq-$1" f n1 n2
  local -a fq
  rm -rf "$out"; mkdir -p "$out/tmp"
  sra_tools fasterq-dump "$src" --split-files --skip-technical --threads "$SRA_THREADS" \
    --temp "$out/tmp" --outdir "$out" || { echo "   $run: fasterq-dump failed"; return 1; }
  rm -rf "$out/tmp"
  if [ -s "$out/${run}_1.fastq" ] && [ -s "$out/${run}_2.fastq" ]; then
    n1=$(wc -l < "$out/${run}_1.fastq"); n2=$(wc -l < "$out/${run}_2.fastq")
    if [ "$n1" != "$n2" ]; then
      echo "   $run: read counts differ between _1 ($((n1 / 4))) and _2 ($((n2 / 4))); not using it"
      return 1
    fi
    for f in "$out"/*.fastq; do
      case "$f" in
        */"${run}"_[12].fastq) ;;
        *) echo "   $run: dropping unpaired $(basename "$f") ($(( $(wc -l < "$f") / 4 )) reads)"; rm -f "$f" ;;
      esac
    done
    gz_into "$out/${run}_1.fastq" "$FASTQ_DIR/${run}_1.fastq.gz" || return 1
    gz_into "$out/${run}_2.fastq" "$FASTQ_DIR/${run}_2.fastq.gz" || return 1
    echo "   $run: $((n1 / 4)) read pairs"
  else
    fq=( "$out"/*.fastq )
    if [ "${#fq[@]}" -ne 1 ] || [ ! -s "${fq[0]}" ]; then
      echo "   $run: unexpected fasterq-dump output: $(ls "$out" | tr '\n' ' ')"
      return 1
    fi
    echo "   $run: $(( $(wc -l < "${fq[0]}") / 4 )) single reads"
    gz_into "${fq[0]}" "$FASTQ_DIR/$run.fastq.gz" || return 1
    if [ "${LAYOUT_OF[$run]:-}" = PAIRED ]; then
      echo "   WARN: $run is PAIRED in NCBI runinfo but has one read file; running it as SE"
    fi
  fi
  rm -rf "$out"
}

# ddbj_download RUN: the run's .sra (original quality scores) from the DDBJ
# mirror into TMP_DIR, validated, converted, deleted. Fails when DDBJ lacks the
# run (HTTP 404) or any step fails.
ddbj_download() {
  local run=$1 url code sra="$TMP_DIR/$1.sra" rc=0
  url="https://ddbj.nig.ac.jp/public/ddbj_database/dra/sralite/ByExp/litesra/${ACCESSION:0:3}/${ACCESSION:0:6}/${ACCESSION}/${run}/${run}.sra"
  code=$(curl -sS -o /dev/null -I -w '%{http_code}' --retry 3 --retry-delay 10 --max-time 60 "$url" || true)
  if [ "$code" != 200 ]; then
    echo "   $run: not on DDBJ (HTTP ${code:-none}) $url"
    return 1
  fi
  echo "   $run: DDBJ $url"
  aria2c --max-tries=10 --retry-wait=10 --split=8 --max-connection-per-server=8 \
    --continue=true --allow-overwrite=true --auto-file-renaming=false \
    --console-log-level=warn --dir="$TMP_DIR" --out="$run.sra" "$url" \
    || { echo "   $run: DDBJ download failed"; rc=1; }
  if [ $rc = 0 ]; then
    sra_tools vdb-validate "$sra" || { echo "   $run: vdb-validate failed"; rc=1; }
  fi
  if [ $rc = 0 ]; then
    dump_fastq "$run" "$sra" || rc=1
  fi
  rm -f "$sra" "$sra.aria2"
  return $rc
}

# ncbi_download RUN: fasterq-dump fetches the run from NCBI itself.
ncbi_download() {
  echo "   $1: fetching from NCBI with fasterq-dump"
  dump_fastq "$1" "$1"
}

exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Iseconds) start $ACCESSION ($STRATEGY) ==="

# --- FASTQ download ---
declare -A LAYOUT_OF=()   # run → NCBI runinfo LibraryLayout (NCBI-resolved runs only)
RUN_ACCS=""
if [ "$SKIP_ENA" = 1 ]; then
  echo "   SKIP_ENA=1: runs from NCBI, downloads from DDBJ/NCBI"
elif ! RUN_ACCS=$(ena_runs); then
  echo "   ENA filereport gave no run list for $ACCESSION; asking NCBI"
  RUN_ACCS=""
fi
if [ -z "$RUN_ACCS" ] && RUNINFO=$(ncbi_runs); then
  RUN_ACCS=$(cut -f1 <<< "$RUNINFO")
  while IFS=$'\t' read -r r l; do LAYOUT_OF[$r]=$l; done <<< "$RUNINFO"
  echo "   NCBI runinfo: $(tr '\t\n' ': ' <<< "$RUNINFO")"
fi
[ -z "$RUN_ACCS" ] && { echo "ERROR: no runs for $ACCESSION from ENA or NCBI"; exit 2; }

for run in $RUN_ACCS; do
  SRC=""
  if [ "$SKIP_ENA" != 1 ]; then
    if ena_download "$run" && has_fastq "$run"; then
      SRC=ena
    else
      echo "   $run: ENA download failed; trying DDBJ, then NCBI"
      clean_run "$run"
    fi
  fi
  if [ -z "$SRC" ]; then
    if ddbj_download "$run"; then
      SRC=ddbj
    else
      clean_run "$run"
      if ncbi_download "$run"; then
        SRC=ncbi
      else
        clean_run "$run"
        echo "ERROR: could not download $run from DDBJ or NCBI"
        exit 3
      fi
    fi
  fi
  echo "download source: $run $SRC"
done

FASTQS=( "$FASTQ_DIR"/*.fastq.gz )
[ ! -e "${FASTQS[0]}" ] && FASTQS=( "$FASTQ_DIR"/*.fastq )
[ ! -e "${FASTQS[0]}" ] && { echo "ERROR: no FASTQs downloaded"; exit 3; }

# Detect SE vs PE from filenames (fast-download.sh emits _1/_2 for PE)
FWD=$(ls "$FASTQ_DIR"/*_1.fastq.gz "$FASTQ_DIR"/*_1.fastq 2>/dev/null | head -1 || true)
REV=$(ls "$FASTQ_DIR"/*_2.fastq.gz "$FASTQ_DIR"/*_2.fastq 2>/dev/null | head -1 || true)
if [ -z "$FWD" ]; then
  FWD=$(ls "$FASTQ_DIR"/*.fastq.gz "$FASTQ_DIR"/*.fastq 2>/dev/null | head -1 || true)
fi
echo "   fwd=$FWD  rev=${REV:-<SE>}"

# --- Dispatch ---
case "$STRATEGY" in
  ChIP-Seq|ATAC-Seq)
    OUT_SUBDIR=$([ "$STRATEGY" = "ChIP-Seq" ] && echo chipseq || echo atacseq)
    OUT_DIR="$OUTPUT_ROOT/$OUT_SUBDIR/$ACCESSION"
    mkdir -p "$OUT_DIR"
    GSIZE=$(cat "$REF_DIR/macs_gsize.txt")
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome-fasta "$REF_DIR/$REF_NAME.fa"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --genome-size "$GSIZE"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
      --blacklist "$BLACKLIST"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    docker run --rm \
      -v "$DATA_ROOT:$DATA_ROOT" \
      -v "$ZENIGOKE_REPO:$ZENIGOKE_REPO:ro" \
      -e TMPDIR="$TMP_DIR" \
      -v "$TMP_DIR:$TMP_DIR" \
      --user "$(id -u):$(id -g)" \
      "$CHIP_IMG" \
      bash "$ZENIGOKE_REPO/scripts/pipeline-v2-plant.sh" "${ARGS[@]}"
    ;;

  Bisulfite-Seq)
    OUT_DIR="$OUTPUT_ROOT/bsseq/$ACCESSION"
    mkdir -p "$OUT_DIR"
    ARGS=(
      --sample-id "$ACCESSION"
      --fastq-fwd "$FWD"
      --genome MpTak_v7.1
      --genome-fasta "$REF_DIR/$REF_NAME.fa"
      --abismal-index "$REF_DIR/$REF_NAME.abismal.idx"
      --chrom-sizes "$REF_DIR/chrom.sizes"
      --outdir "$OUT_DIR"
      --threads "$THREADS"
    )
    [ -n "$REV" ] && ARGS+=( --fastq-rev "$REV" )
    docker run --rm \
      -v "$DATA_ROOT:$DATA_ROOT" \
      -v "$ZENIGOKE_REPO:$ZENIGOKE_REPO:ro" \
      -e TMPDIR="$TMP_DIR" \
      -v "$TMP_DIR:$TMP_DIR" \
      --user "$(id -u):$(id -g)" \
      "$BS_IMG" \
      bash "$ZENIGOKE_REPO/scripts/pipeline-v2-bs-plant.sh" "${ARGS[@]}"
    ;;

  *)
    echo "ERROR: unknown library_strategy: $STRATEGY"
    exit 4
    ;;
esac

echo "=== $(date -Iseconds) end $ACCESSION ok ==="
