#!/usr/bin/env python3
"""Build data/catalog.json for the Zenigoke Catalog web UI (web/).

Reads the catalog DB (db/kknmsmd.db, read-only; QC flags from its sample_qc
table), the cached study metadata in meta/*.tsv (ENA + NCBI, refreshable with
--refresh-meta), institution coordinates from configs/institutions.tsv,
BioSample titles from metadata/biosamples/*.json, and -- when the pipeline
output tree is mounted -- file sizes from /data1/zenigoke/output. Writes:

  data/catalog.json          one JSON index the whole UI runs on
  data/MpTak_v7.1.igv.json   the genome definition we plan to host on S3

Stdlib only. Nothing outside web/ is written.

Usage (from anywhere):
  python3 make_index.py                    # build from cached meta/*.tsv
  python3 make_index.py --refresh-meta     # re-fetch ENA/NCBI study metadata first
  python3 make_index.py --no-probe         # skip HEAD requests to S3

View:
  cd web && python3 -m http.server 8765
  open http://localhost:8765/
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

DATA_BASE = "https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.com/"
GENOME_ID = "MpTak_v7.1"
REF_PREFIX = f"references/{GENOME_ID}/"
DEFAULT_LOCUS = "chr6:2440000-2490000"

ASSAY_LABEL = {"ChIP-Seq": "ChIP-seq", "ATAC-Seq": "ATAC-seq", "Bisulfite-Seq": "BS-seq"}
ASSAY_DIR = {"ChIP-Seq": "chipseq", "ATAC-Seq": "atacseq", "Bisulfite-Seq": "bsseq"}

# Target classes for the "target" facet. Values not listed fall to "other".
TARGET_CLASS = {
    "H3K27me3": "histone modification", "H3K4me3": "histone modification",
    "H3K27me1": "histone modification", "H3K9me1": "histone modification",
    "H2Aub": "histone modification", "H2AK119ub": "histone modification",
    "H2A": "histone / variant", "H2A.X": "histone / variant",
    "H2A.Z": "histone / variant", "H3": "histone / variant",
    "GLK": "transcription factor", "MpTCP1": "transcription factor",
    "WRKY1": "transcription factor", "WRKY6": "transcription factor",
    "WRKY12": "transcription factor", "WRKY14": "transcription factor",
    "HSFA1V": "transcription factor", "tagRFP": "tag only (control)",
    "input": "control (input / IgG)", "IgG": "control (input / IgG)",
    # Curation smell: H2AK119R is a point-mutant genotype, not an antibody.
    "H2AK119R": "needs curation",
}
CONTROL_TARGETS = {"input", "IgG", "tagRFP"}


def classify_genotype(strain: str | None) -> str:
    """Same rule as scripts/api_axes.py::_classify_strain (copied, not imported:
    that module pulls in FastAPI)."""
    if strain is None:
        return "unknown"
    s = strain.lower()
    wt = ("tak-1", "tak-2", "wt", "wild type", "tak1/tak2", "tak-1_bc")
    if any(m == s or s.startswith(m) for m in wt):
        return "wild type"
    if "overexpression" in s or "tagrfp" in s:
        return "overexpression"
    return "mutant"


# ---------------------------------------------------------------------------
# Study metadata (ENA portal API + NCBI E-utilities), cached as TSV
# ---------------------------------------------------------------------------

ENA_SEARCH = "https://www.ebi.ac.uk/ena/portal/api/search"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _http(url: str, data: dict | None = None, timeout: int = 90) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "zenigoke-catalog/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def refresh_meta(accessions: list[str]) -> None:
    meta = HERE / "meta"
    meta.mkdir(exist_ok=True)
    exp_tsv = _http(ENA_SEARCH, {
        "result": "read_experiment", "includeAccessions": ",".join(accessions),
        "fields": "experiment_accession,study_accession,secondary_study_accession,"
                  "experiment_title,sample_accession,instrument_model,library_layout,first_public",
        "format": "tsv", "limit": "0",
    }).decode()
    (meta / "ena_experiments.tsv").write_text(exp_tsv)
    projects = sorted({r["study_accession"] for r in csv.DictReader(exp_tsv.splitlines(), delimiter="\t")})
    study_tsv = _http(ENA_SEARCH, {
        "result": "study", "includeAccessions": ",".join(projects),
        "fields": "study_accession,secondary_study_accession,study_title,center_name,first_public",
        "format": "tsv",
    }).decode()
    studies = list(csv.DictReader(study_tsv.splitlines(), delimiter="\t"))
    # BioProject -> PubMed via NCBI elink, then esummary for citation text.
    for st in studies:
        acc = st["study_accession"]
        ids = json.loads(_http(f"{EUTILS}esearch.fcgi?db=bioproject&retmode=json&term="
                               + urllib.parse.quote(f"{acc}[Project Accession]")))["esearchresult"]["idlist"]
        time.sleep(0.4)
        pmids: list[str] = []
        if ids:
            lk = json.loads(_http(f"{EUTILS}elink.fcgi?dbfrom=bioproject&db=pubmed&retmode=json&id={ids[0]}"))
            for ls in lk.get("linksets", []):
                for db in ls.get("linksetdbs", []):
                    pmids += db.get("links", [])
            time.sleep(0.4)
        st["pmid"] = ",".join(pmids)
        st["citation"] = ""
        if pmids:
            sm = json.loads(_http(f"{EUTILS}esummary.fcgi?db=pubmed&retmode=json&id={pmids[0]}"))["result"][pmids[0]]
            first = (sm.get("authors") or [{}])[0].get("name", "")
            st["citation"] = f"{first} et al. ({sm.get('pubdate', '')[:4]}) {sm.get('source', '')}. {sm.get('title', '')}"
            time.sleep(0.4)
    cols = ["study_accession", "secondary_study_accession", "study_title", "center_name",
            "first_public", "pmid", "citation"]
    with open(meta / "studies.tsv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(studies)
    print(f"refreshed meta/: {len(exp_tsv.splitlines()) - 1} experiments, {len(studies)} studies")


def load_meta() -> tuple[dict, dict]:
    exps, studies = {}, {}
    p = HERE / "meta" / "ena_experiments.tsv"
    if p.exists():
        for r in csv.DictReader(p.open(), delimiter="\t"):
            exps[r["experiment_accession"]] = r
    p = HERE / "meta" / "studies.tsv"
    if p.exists():
        for r in csv.DictReader(p.open(), delimiter="\t"):
            studies[r["study_accession"]] = r
    return exps, studies


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def file_specs(strategy: str, acc: str) -> list[dict]:
    """Every published file for one experiment, as S3 keys relative to DATA_BASE.

    cat = download category the cart filters on; igv = default IGV track role.
    """
    d = f"output/{ASSAY_DIR[strategy]}/{acc}/"
    if strategy in ("ChIP-Seq", "ATAC-Seq"):
        out = [dict(cat="signal", key=f"{d}{acc}.bw", fmt="bigWig", label="Signal coverage", igv="signal")]
        for q in ("05", "10", "20"):
            out.append(dict(cat="peaks_bb", q=q, key=f"{d}{acc}.{q}.bb", fmt="bigBed",
                            label=f"Peaks q<1e-{int(q)}", igv="peaks"))
            out.append(dict(cat="peaks_np", q=q, key=f"{d}{acc}.{q}_peaks.narrowPeak", fmt="narrowPeak",
                            label=f"Peaks q<1e-{int(q)}"))
        out.append(dict(cat="qc", key=f"{d}{acc}_fastp.json", fmt="JSON", label="fastp QC report"))
        out.append(dict(cat="qc", key=f"{d}{acc}.stats.tsv", fmt="TSV", label="Pipeline stats"))
        return out
    out = []
    for ctx in ("CpG", "CHG", "CHH"):
        out.append(dict(cat="meth", ctx=ctx, key=f"{d}{acc}.{ctx}.methyl.bw", fmt="bigWig",
                        label=f"{ctx} methylation level", igv="meth"))
    for ctx in ("CpG", "CHG", "CHH"):
        out.append(dict(cat="meth_cov", ctx=ctx, key=f"{d}{acc}.{ctx}.cover.bw", fmt="bigWig",
                        label=f"{ctx} read coverage"))
    out.append(dict(cat="meth_regions", key=f"{d}{acc}.CpG.hmr.bed", fmt="BED", label="CpG hypo-methylated regions (HMR)", igv="regions"))
    out.append(dict(cat="meth_regions", key=f"{d}{acc}.CpG.hypermr.bed", fmt="BED", label="CpG hyper-methylated regions"))
    out.append(dict(cat="meth_regions", key=f"{d}{acc}.CpG.pmd.bed", fmt="BED", label="CpG partially methylated domains (PMD)"))
    out.append(dict(cat="meth_regions", key=f"{d}{acc}.CHG.hypermr.bed", fmt="BED", label="CHG hyper-methylated regions"))
    out.append(dict(cat="qc", key=f"{d}{acc}_fastp.json", fmt="JSON", label="fastp QC report"))
    out.append(dict(cat="qc", key=f"{d}{acc}.stats.tsv", fmt="TSV", label="Pipeline stats"))
    out.append(dict(cat="qc", key=f"{d}{acc}.abismal.stats", fmt="YAML", label="Aligner (abismal) stats"))
    return out


def attach_sizes(files: list[dict], output_root: pathlib.Path | None) -> list[dict]:
    """Keep only files that exist (when the output tree is mounted) and record sizes."""
    if output_root is None or not output_root.exists():
        for f in files:
            f["size"] = None
        return files
    kept = []
    for f in files:
        p = output_root / f["key"][len("output/"):]
        if p.exists():
            f["size"] = p.stat().st_size
            kept.append(f)
    return kept


# ---------------------------------------------------------------------------
# QC (provisional rules; the sample_qc table wins when present)
# ---------------------------------------------------------------------------

QC_RULES_DOC = [
    "FAIL: pipeline did not complete",
    "FAIL: mapping rate < 20%",
    "FAIL: ChIP/ATAC (non-control) with 0 peaks at q<1e-5",
    "WARN: mapping rate < 50%",
    "WARN: duplication rate > 60% (ChIP/ATAC)",
    "WARN: ChIP (non-control) < 1,000 peaks at q<1e-10",
    "WARN: ATAC < 10,000 peaks at q<1e-10",
    "WARN: target not curated / needs curation",
]


def provisional_qc(s: dict) -> tuple[str, list[str]]:
    fails, warns = [], []
    strat, m, dup = s["library_strategy"], s["mapping_rate"], s["duplication_rate"]
    target = s.get("antibody_target")
    if s["status"] != "ok":
        return "FAIL", ["pipeline did not complete"]
    if m is not None and m < 20:
        fails.append(f"mapping rate {m:.1f}% < 20%")
    elif m is not None and m < 50:
        warns.append(f"mapping rate {m:.1f}% < 50%")
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        if dup is not None and dup > 60:
            warns.append(f"duplication {dup:.1f}% > 60%")
        is_ctrl = strat == "ChIP-Seq" and target in CONTROL_TARGETS
        p5, p10 = s.get("peaks_q5"), s.get("peaks_q10")
        if not is_ctrl and p5 == 0:
            fails.append("no peaks at q<1e-5")
        elif strat == "ChIP-Seq" and not is_ctrl and p10 is not None and p10 < 1000:
            warns.append(f"{p10:,} peaks at q<1e-10 (< 1,000)")
        elif strat == "ATAC-Seq" and p10 is not None and p10 < 10000:
            warns.append(f"{p10:,} peaks at q<1e-10 (< 10,000)")
        if strat == "ChIP-Seq" and (target is None or TARGET_CLASS.get(target) == "needs curation"):
            warns.append("ChIP target not curated" if target is None else f"target '{target}' needs curation")
    if fails:
        return "FAIL", fails + warns
    if warns:
        return "WARN", warns
    return "PASS", []


def split_reasons(s: str) -> list[str]:
    """Split a sample_qc.qc_reasons string on ';' outside parentheses."""
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def load_qc_from_db(db: pathlib.Path) -> dict:
    """QC flag, reasons and corrected rates per accession from sample_qc.

    A DB without sample_qc (built before 2026-10) falls back to the
    provisional rules above for every experiment.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT accession, qc_flag, qc_reasons, aligned_pct, dup_pct FROM sample_qc").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    out = {}
    for acc, flag, reasons, aligned, dup in rows:
        metrics = {k: v for k, v in (("aligned_pct", aligned), ("dup_pct", dup)) if v is not None}
        out[acc] = (flag, split_reasons(reasons or ""), metrics)
    return out


def load_institutions() -> dict:
    """study accession -> submitting institution with coordinates (for the map)."""
    p = REPO / "configs" / "institutions.tsv"
    out = {}
    if p.exists():
        for r in csv.DictReader(p.open(), delimiter="\t"):
            out[r["study"]] = {
                "institution": r["institution"], "inst_short": r["short"], "city": r["city"],
                "country": r["country"], "lat": float(r["lat"]), "lon": float(r["lon"]),
            }
    return out


# ---------------------------------------------------------------------------
# Genome definition (what we propose to host on S3)
# ---------------------------------------------------------------------------

def genome_block(probe: bool) -> dict:
    ref = DATA_BASE + REF_PREFIX
    g = {
        "id": GENOME_ID,
        "name": "Marchantia polymorpha MpTak v7.1 (standard genome, MarpolBase)",
        "source": "https://marchantia.info/data/v7.1_reference_genomes/MpTak_v7.1_standard_genome/",
        "json": ref + f"{GENOME_ID}.igv.json",
        "fasta": ref + f"{GENOME_ID}.fa",
        "fai": ref + f"{GENOME_ID}.fa.fai",
        "gff": ref + f"{GENOME_ID}.genes.gff3.gz",   # non-indexed: keeps gene-ID search working
        "chrom_sizes": ref + "chrom.sizes",
        "default_locus": DEFAULT_LOCUS,
        # Interim fallback: UCSC GenArk hosts the same assembly (identical
        # chromosome lengths; chr1..chr8/chrU/chrV available as aliases of the
        # GenBank names AP0313xx.1), with CORS. Gene track there is NCBI's
        # annotation (MPTK1_* locus tags), not MarpolBase Mp*g IDs.
        "genark": {
            "accession": "GCA_039105155.1",
            "hub": "https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/hub.txt",
        },
        "hosted": {},
    }
    for k in ("json", "fasta", "fai", "gff", "chrom_sizes"):
        g["hosted"][k] = None
        if probe:
            try:
                req = urllib.request.Request(g[k], method="HEAD")
                with urllib.request.urlopen(req, timeout=15) as r:
                    g["hosted"][k] = r.status == 200
            except Exception:
                g["hosted"][k] = False  # S3 answers 403 for missing keys (no ListBucket)
    return g


def igv_genome_json(g: dict) -> dict:
    """IGV desktop (genome=) and igv.js reference definition.

    "id" is the JSON's own URL (ChIP-Atlas does the same): IGV compares the
    genome= argument with the loaded genome's id, so an ordinary id would make
    every /load call reload the genome and reset the session.
    """
    return {
        "id": g["json"],
        "name": "M. polymorpha MpTak v7.1",
        "fastaURL": g["fasta"],
        "indexURL": g["fai"],
        "chromosomeOrder": "chr1,chr2,chr3,chr4,chr5,chr6,chr7,chr8,chrV,chrU",
        "defaultPos": g["default_locus"],
        "tracks": [{
            "name": "MpTak v7.1 genes (MarpolBase)",
            "format": "gff3",
            "url": g["gff"],
            "indexed": False,
            "searchable": True,
            "displayMode": "EXPANDED",
        }],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_samples(db: pathlib.Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.*, c.tissue, c.cell_type, c.developmental_stage, c.genotype_strain,
               c.treatment, c.antibody_target,
               ch.peaks_q5, ch.peaks_q10, ch.peaks_q20,
               b.mean_cpg, b.mean_chg, b.mean_chh, b.cpg_hmr_count, b.cpg_hypermr_count,
               b.cpg_pmd_count, b.chg_hypermr_count
        FROM sample s
        LEFT JOIN sample_curation c USING (accession)
        LEFT JOIN sample_chipseq ch USING (accession)
        LEFT JOIN sample_bsseq b USING (accession)
        ORDER BY s.accession""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def biosample_title(acc: str) -> str | None:
    p = REPO / "metadata" / "biosamples" / f"{acc}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("title")
    except Exception:
        return None


def short_study_label(title: str, citation: str | None, max_len: int = 30) -> str:
    """'Yen et al. 2025' when a paper is linked, else a truncated study title."""
    m = re.match(r"^(\S+)\s.*?\((\d{4})\)", citation or "")
    if m:
        return f"{m.group(1)} et al. {m.group(2)}"
    t = re.sub(r"\s*\[[^\]]*\]\s*$", "", title or "").strip()
    return t if len(t) <= max_len else t[: max_len - 1].rstrip() + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(REPO / "db" / "kknmsmd.db"))
    ap.add_argument("--output-root", default="/data1/zenigoke/output",
                    help="pipeline output tree (for file existence + sizes); optional")
    ap.add_argument("--refresh-meta", action="store_true", help="re-fetch ENA/NCBI study metadata")
    ap.add_argument("--no-probe", action="store_true", help="do not HEAD the S3 genome URLs")
    args = ap.parse_args(argv)

    samples = load_samples(pathlib.Path(args.db))
    if args.refresh_meta:
        refresh_meta([s["accession"] for s in samples])
    exps, studies = load_meta()
    overrides = load_qc_from_db(pathlib.Path(args.db))
    institutions = load_institutions()
    out_root = pathlib.Path(args.output_root)

    rows = []
    for s in samples:
        acc, strat = s["accession"], s["library_strategy"]
        e = exps.get(acc, {})
        gsm = re.search(r"\b(GSM\d+)\b", e.get("experiment_title", "") or "")
        target = s["antibody_target"] if strat == "ChIP-Seq" else None
        flag, reasons, qc_metrics = overrides.get(acc) or (*provisional_qc(s), {})
        files = attach_sizes(file_specs(strat, acc), out_root) if s["status"] == "ok" else []
        rows.append({
            "acc": acc,
            "assay": ASSAY_LABEL[strat],
            "target": (target or "not curated") if strat == "ChIP-Seq" else None,
            "target_class": (TARGET_CLASS.get(target, "other") if target else "not curated") if strat == "ChIP-Seq" else None,
            "genotype": s["genotype_strain"] or "not curated",
            "genotype_class": classify_genotype(s["genotype_strain"]),
            "stage": s["developmental_stage"] or "not curated",
            "tissue": s["tissue"] or "not curated",
            "cell_type": s["cell_type"],
            "treatment": s["treatment"] or "none / not stated",
            "title": biosample_title(acc),
            "study": e.get("study_accession"),
            "srp": e.get("secondary_study_accession"),
            "run": e.get("run_accession"),
            "gsm": gsm.group(1) if gsm else None,
            "biosample": s["biosample_accession"],
            "instrument": e.get("instrument_model"),
            "first_public": e.get("first_public"),
            "layout": {"0": "SE", "1": "PE"}.get(s["layout"] or "", None),
            "status": s["status"],
            "qc": {"flag": flag, "reasons": reasons, "source": "catalog" if acc in overrides else "provisional"},
            "metrics": {
                "reads": s["reads_filtered"],
                # sample_qc carries the alignment and duplication rates the QC
                # flags were computed from; the sample table is the fallback.
                "mapping_rate": qc_metrics.get("aligned_pct", s["mapping_rate"]),
                "duplication_rate": qc_metrics.get("dup_pct", s["duplication_rate"]),
                "peaks": {"05": s["peaks_q5"], "10": s["peaks_q10"], "20": s["peaks_q20"]}
                if strat != "Bisulfite-Seq" else None,
                "meth": {"CpG": s["mean_cpg"], "CHG": s["mean_chg"], "CHH": s["mean_chh"],
                         "hmr": s["cpg_hmr_count"], "pmd": s["cpg_pmd_count"]}
                if strat == "Bisulfite-Seq" else None,
            },
            "files": files,
        })

    study_out = {}
    for acc, st in studies.items():
        study_out[acc] = {
            "title": st["study_title"],
            "label": short_study_label(st["study_title"], st.get("citation")),
            "srp": st["secondary_study_accession"],
            "center": st["center_name"],
            "first_public": st["first_public"],
            "pmid": (st.get("pmid") or "").split(",")[0] or None,
            "citation": st.get("citation") or None,
            "n": sum(1 for r in rows if r["study"] == acc),
            **institutions.get(acc, {}),
        }

    g = genome_block(probe=not args.no_probe)
    catalog = {
        "schema": "zenigoke-catalog-index/0.2",
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "release": "2026-10",
        "data_base": DATA_BASE,
        "genome": g,
        "qc_rules": QC_RULES_DOC,
        "studies": study_out,
        "samples": rows,
    }
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    (data / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
    (data / f"{GENOME_ID}.igv.json").write_text(json.dumps(igv_genome_json(g), indent=2) + "\n")

    n_files = sum(len(r["files"]) for r in rows)
    size = sum((f["size"] or 0) for r in rows for f in r["files"])
    qc = {}
    for r in rows:
        qc[r["qc"]["flag"]] = qc.get(r["qc"]["flag"], 0) + 1
    print(f"wrote data/catalog.json: {len(rows)} experiments, {len(study_out)} studies, "
          f"{n_files} files ({size / 1e9:.1f} GB), qc={qc}")
    print(f"genome hosting on S3: {g['hosted']}")
    no_geo = sorted(a for a in study_out if a not in institutions)
    if no_geo:
        print(f"WARNING: studies missing from configs/institutions.tsv (not on the map): {no_geo}", file=sys.stderr)
    missing = [r["acc"] for r in rows if not r["study"]]
    if missing:
        print(f"WARNING: {len(missing)} experiments without study metadata: {missing[:5]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
