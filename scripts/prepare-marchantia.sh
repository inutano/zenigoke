#!/usr/bin/env bash
# Download MpTak v7.1 standard genome from MarpolBase and build indexes.
# Idempotent — every step is skipped if its output already exists.
#
# Confirmed download URLs (verified 2026-04-24):
#   https://marchantia.info/data/MpTak_v7.1_standard_genome/MpTak_v7.1.fa.gz
#   https://marchantia.info/data/MpTak_v7.1_standard_genome/MpTak_v7.1.gff
#
# The /download/MpTak_v7.1/MpTak_v7.1_standard_genome/ redirect path is not
# usable for file downloads (returns 404 for individual files). Use the /data/
# path directly.
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data1/zenigoke}"
REF_DIR="$DATA_ROOT/references/MpTak_v7.1"
BASE_URL="https://marchantia.info/data/MpTak_v7.1_standard_genome"

# Confirmed filenames from directory listing at $BASE_URL
EXPECTED_FASTA="${EXPECTED_FASTA:-MpTak_v7.1.fa.gz}"
EXPECTED_GFF="${EXPECTED_GFF:-MpTak_v7.1.gff}"

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

echo "== step 2: download GFF =="
# Note: MarpolBase provides MpTak_v7.1.gff (uncompressed, GFF format, not GFF3)
if [ ! -f MpTak_v7.1.gff ]; then
  echo "   fetching $BASE_URL/$EXPECTED_GFF"
  curl -fsSL -o "$EXPECTED_GFF" "$BASE_URL/$EXPECTED_GFF" || {
    echo "WARN: GFF download failed — continuing; Phase 2 may need it later"
  }
else
  echo "   MpTak_v7.1.gff present — skip"
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

echo "== step 7: organelle-augmented reference (nuclear + chrC + chrM) =="
# MpTak_v7.1 deliberately omits the organelle genomes. Without them, organelle
# reads land on nuclear NUPT/NUMT copies (spurious peaks, false low methylation).
# Outputs stay nuclear-only: chrom.sizes here is the nuclear list.
ORG_DIR="$DATA_ROOT/references/MpTak_v7.1_org"
mkdir -p "$ORG_DIR"
fetch_org() {  # $1 = NCBI accession, $2 = sequence name to use
  local out="$ORG_DIR/$2.fa"
  [ -s "$out" ] && return 0
  curl -fsSL "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=$1&rettype=fasta&retmode=text" \
    | sed "1s/.*/>$2 $1/" > "$out.tmp"
  mv "$out.tmp" "$out"
}
fetch_org AP025455.1 chrC   # Tak-1 chloroplast, 120,306 bp
fetch_org AP025456.1 chrM   # Tak-1 mitochondrion, 186,197 bp
if [ ! -f "$ORG_DIR/MpTak_v7.1_org.fa" ]; then
  cat MpTak_v7.1.fa "$ORG_DIR/chrC.fa" "$ORG_DIR/chrM.fa" | awk 'NF' > "$ORG_DIR/MpTak_v7.1_org.fa"
fi
ORG_DOCKER="docker run --rm -u $(id -u):$(id -g) -v $ORG_DIR:/ref -w /ref"
[ -f "$ORG_DIR/MpTak_v7.1_org.fa.fai" ] || $ORG_DOCKER "$CHIP_IMG" samtools faidx MpTak_v7.1_org.fa
cut -f1,2 "$ORG_DIR/MpTak_v7.1_org.fa.fai" > "$ORG_DIR/chrom.sizes.all"
cp chrom.sizes "$ORG_DIR/chrom.sizes"
cp macs_gsize.txt "$ORG_DIR/macs_gsize.txt"
[ -f "$ORG_DIR/MpTak_v7.1_org.fa.bwt.2bit.64" ] || $ORG_DOCKER "$CHIP_IMG" bwa-mem2 index MpTak_v7.1_org.fa
[ -f "$ORG_DIR/MpTak_v7.1_org.abismal.idx" ] || \
  $ORG_DOCKER "$BS_IMG" dnmtools abismalidx MpTak_v7.1_org.fa MpTak_v7.1_org.abismal.idx
echo "   org reference: $(wc -l < "$ORG_DIR/chrom.sizes.all") sequences ($(wc -l < "$ORG_DIR/chrom.sizes") nuclear)"

echo "== done =="
ls -lh "$REF_DIR"
