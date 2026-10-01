"""Build a SQLite catalog DB from zenigoke pipeline outputs + curated metadata.

Stdlib + sqlite3 only.

Public API:
  init_schema(conn)
  parse_chipseq_stats(row)     -> dict
  parse_bsseq_stats(row)       -> dict
  parse_curation_json(d)       -> dict
  load_curation_overrides(path) -> Dict[str, Dict[str, str]]
  apply_curation_overrides(curation, overrides) -> dict
  collect_sample_rows(...)     -> Iterator[dict]
  populate_sample(conn, row)
  populate_curation(conn, accession, curation)
  populate_chipseq(conn, accession, output_dir, stats)
  populate_bsseq(conn, accession, output_dir, stats)
  load_qc_tsvs(qc_dir)         -> Dict[str, dict]
  qc_row(row, tsv)             -> dict
  populate_qc(conn, accession, qc)
  build(data_root, metadata_root, db_path, qc_dir=None, curation_overrides=None)
  main(argv=None)
"""
from __future__ import annotations

import csv
import json
import logging
import os
import pathlib
import sqlite3
import sys
from typing import Any, Dict, Iterator, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qc_flags  # noqa: E402

log = logging.getLogger(__name__)

STRATEGY_DIRS = {
    "ChIP-Seq": "chipseq",
    "ATAC-Seq": "atacseq",
    "Bisulfite-Seq": "bsseq",
}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE sample (
  accession            TEXT PRIMARY KEY,
  library_strategy     TEXT NOT NULL,
  status               TEXT NOT NULL,
  layout               TEXT,
  fastq_size           TEXT,
  reads_raw            INTEGER,
  reads_filtered       INTEGER,
  reads_mapped         INTEGER,
  mapping_rate         REAL,
  duplication_rate     REAL,
  usable_rate          REAL,
  elapsed_min          REAL,
  biosample_accession  TEXT,
  output_dir           TEXT NOT NULL
);
CREATE TABLE sample_curation (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  tissue               TEXT,
  cell_type            TEXT,
  developmental_stage  TEXT,
  genotype_strain      TEXT,
  treatment            TEXT,
  antibody_target      TEXT
);
CREATE TABLE sample_chipseq (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  peaks_q5             INTEGER,
  peaks_q10            INTEGER,
  peaks_q20            INTEGER,
  bigwig_path          TEXT,
  peaks_q5_path        TEXT,
  peaks_q10_path       TEXT,
  peaks_q20_path       TEXT
);
CREATE TABLE sample_bsseq (
  accession            TEXT PRIMARY KEY REFERENCES sample(accession),
  mean_cpg             REAL,
  mean_chg             REAL,
  mean_chh             REAL,
  cpg_hmr_count        INTEGER,
  cpg_hypermr_count    INTEGER,
  cpg_pmd_count        INTEGER,
  chg_hypermr_count    INTEGER,
  cpg_methyl_bw_path   TEXT,
  cpg_cover_bw_path    TEXT,
  cpg_hmr_path         TEXT,
  cpg_hypermr_path     TEXT,
  cpg_pmd_path         TEXT,
  chg_methyl_bw_path   TEXT,
  chg_cover_bw_path    TEXT,
  chg_hypermr_path     TEXT,
  chh_methyl_bw_path   TEXT,
  chh_cover_bw_path    TEXT
);
CREATE TABLE sample_qc (
  accession                    TEXT PRIMARY KEY REFERENCES sample(accession),
  qc_flag                      TEXT NOT NULL,
  qc_reasons                   TEXT,
  peak_flag                    TEXT,
  aligned_pct                  REAL,
  dup_pct                      REAL,
  usable_fragments             INTEGER,
  organelle_pct                REAL,
  foreign_pct                  REAL,
  peaks_in_organelle_like_pct  REAL,
  nonconv_pct                  REAL,
  frac_cpg_cov_ge5             REAL,
  metrics_source               TEXT
);
CREATE INDEX idx_qc_flag ON sample_qc(qc_flag);
CREATE INDEX idx_curation_tissue   ON sample_curation(tissue);
CREATE INDEX idx_curation_stage    ON sample_curation(developmental_stage);
CREATE INDEX idx_curation_strain   ON sample_curation(genotype_strain);
CREATE INDEX idx_curation_antibody ON sample_curation(antibody_target);
CREATE INDEX idx_sample_strategy   ON sample(library_strategy);
CREATE INDEX idx_sample_status     ON sample(status);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indices. Idempotent within a fresh connection."""
    conn.executescript(SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Stats field definitions (imported from build_report to stay in sync)
# ---------------------------------------------------------------------------

def _get_stats_field_functions():
    """Import field-list functions from build_report to avoid duplication."""
    import importlib.util
    import sys

    # Locate build_report.py relative to this file
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "build_report", here / "build_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._chipseq_stats_fields, mod._bsseq_stats_fields


try:
    _chipseq_fields_fn, _bsseq_fields_fn = _get_stats_field_functions()
except Exception as _e:
    log.warning("Could not import stats field functions from build_report.py: %s", _e)

    def _chipseq_fields_fn(row: List[str]) -> Dict[str, str]:  # fallback
        keys = [
            "sample", "layout", "fastq_size", "v1_placeholder", "reads_raw",
            "reads_filt", "rate7", "rate8",
            "dedup_bam_size", "bedgraph_size", "bigwig_size",
            "peaks_q5", "peaks_q10", "peaks_q20", "elapsed_min",
            "format", "aligned_reads", "duplicate_reads", "usable_reads",
            "organelle_usable_reads", "nuclear_usable_reads",
        ]
        return {k: v for k, v in zip(keys, row)}

    def _bsseq_fields_fn(row: List[str]) -> Dict[str, str]:  # fallback
        keys = [
            "sample", "layout", "fastq_size", "dedup_bam_size", "read_count",
            "mapping_rate", "methylation_rate", "cpg_coverage",
            "hmr_count", "pmd_count", "hypermr_count", "elapsed_min",
            "mean_cpg", "mean_chg", "mean_chh",
            "format", "unique_rate", "organelle_pct", "nonconv",
        ]
        return {k: v for k, v in zip(keys, row)}


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Stats parsing
# ---------------------------------------------------------------------------

def parse_chipseq_stats(row: List[str]) -> Dict[str, Any]:
    """Parse a ChIP/ATAC stats.tsv row (legacy 15-col or zenigoke-v2 21-col)."""
    f = _chipseq_fields_fn(row)
    reads_filt = _safe_int(f.get("reads_filt"))
    out: Dict[str, Any] = {
        "layout": f.get("layout"),
        "fastq_size": f.get("fastq_size"),
        "reads_raw": _safe_int(f.get("reads_raw")),
        "reads_filtered": reads_filt,
        "elapsed_min": _safe_float(f.get("elapsed_min")),
        "peaks_q5": _safe_int(f.get("peaks_q5")),
        "peaks_q10": _safe_int(f.get("peaks_q10")),
        "peaks_q20": _safe_int(f.get("peaks_q20")),
    }
    if f.get("format") == "zenigoke-v2":
        usable = _safe_int(f.get("usable_reads"))
        out.update({
            "format": "zenigoke-v2",
            "mapping_rate": _safe_float(f.get("rate7")),
            "duplication_rate": _safe_float(f.get("rate8")),
            "reads_mapped": _safe_int(f.get("aligned_reads")),
            "usable_reads": usable,
            "organelle_usable_reads": _safe_int(f.get("organelle_usable_reads")),
            "nuclear_usable_reads": _safe_int(f.get("nuclear_usable_reads")),
            "usable_rate": (100.0 * usable / reads_filt) if usable and reads_filt else None,
        })
    else:
        # Legacy upstream: col 7 is usable %, not an alignment rate; col 8 is
        # 100 - col 7 and mixes unmapped with duplicates, so it is dropped.
        out.update({
            "format": "legacy",
            "mapping_rate": None,
            "duplication_rate": None,
            "reads_mapped": None,
            "usable_rate": _safe_float(f.get("rate7")),
        })
    return out


def parse_bsseq_stats(row: List[str]) -> Dict[str, Any]:
    """Parse a 15-column BS-seq stats TSV row into a typed dict."""
    fields = _bsseq_fields_fn(row)
    return {
        "layout": fields.get("layout"),
        "fastq_size": fields.get("fastq_size"),
        "reads_filtered": _safe_int(fields.get("read_count")),
        "reads_mapped": None,  # not directly in bsseq stats
        "mapping_rate": _safe_float(fields.get("mapping_rate")),
        "duplication_rate": None,  # not in bsseq stats
        "elapsed_min": _safe_float(fields.get("elapsed_min")),
        "unique_rate": _safe_float(fields.get("unique_rate")) if fields.get("format") == "zenigoke-v2" else None,
        "organelle_pct": _safe_float(fields.get("organelle_pct")) if fields.get("format") == "zenigoke-v2" else None,
        "nonconv": _safe_float(fields.get("nonconv")) if fields.get("format") == "zenigoke-v2" else None,
        "mean_cpg": _safe_float(fields.get("mean_cpg")),
        "mean_chg": _safe_float(fields.get("mean_chg")),
        "mean_chh": _safe_float(fields.get("mean_chh")),
        # hmr_count / pmd_count / hypermr_count are aggregate counts per-sample,
        # not per-context; the per-context counts come from the bed file line counts.
        "hmr_count": _safe_int(fields.get("hmr_count")),
        "pmd_count": _safe_int(fields.get("pmd_count")),
        "hypermr_count": _safe_int(fields.get("hypermr_count")),
    }


# ---------------------------------------------------------------------------
# Curation parsing and overrides
# ---------------------------------------------------------------------------

CURATION_FIELDS = ("tissue", "cell_type", "developmental_stage",
                   "genotype_strain", "treatment", "antibody_target")


def load_curation_overrides(path: Optional[pathlib.Path]) -> Dict[str, Dict[str, str]]:
    """Load curation overrides from TSV file (accession -> {field: value}).

    Returns empty dict if path is None or doesn't exist.
    Format: comment lines (start with #), then header row, then data rows.
    """
    out: Dict[str, Dict[str, str]] = {}
    if path is None or not path.exists():
        return out
    with path.open() as fh:
        rows = csv.DictReader((ln for ln in fh if not ln.startswith("#")), delimiter="\t")
        for r in rows:
            out.setdefault(r["accession"], {})[r["field"]] = r["value"]
    return out


def apply_curation_overrides(curation: Dict[str, Any], overrides: Dict[str, str]) -> Dict[str, Any]:
    """Apply curation overrides to a curation dict.

    Returns a new dict (does not mutate the input).
    Raises ValueError if any override field is unknown.
    """
    bad = set(overrides) - set(CURATION_FIELDS)
    if bad:
        raise ValueError(f"unknown curation field(s): {sorted(bad)}")
    return {**curation, **overrides}


def parse_curation_json(d: Dict[str, Any]) -> Dict[str, Any]:
    """Merge extract + extract_experiment into a flat curation dict.

    Rule:
      - tissue, cell_type, developmental_stage, genotype_strain, treatment
        always come from extract.extracted
      - antibody_target: prefer extract_experiment.extract.extracted.antibody_target
        if non-null; else fall back to extract.extracted.antibody_target
    """
    # Primary source: extract.extracted
    extracted: Dict[str, Any] = {}
    extract_block = d.get("extract", {})
    if isinstance(extract_block, dict):
        raw = extract_block.get("extracted")
        if isinstance(raw, dict):
            extracted = raw

    # Fall back to top-level flat dict (legacy format)
    if not extracted:
        extracted = {k: d.get(k) for k in (
            "tissue", "cell_type", "developmental_stage",
            "genotype_strain", "treatment", "antibody_target"
        )}

    result = {
        "tissue": extracted.get("tissue"),
        "cell_type": extracted.get("cell_type"),
        "developmental_stage": extracted.get("developmental_stage"),
        "genotype_strain": extracted.get("genotype_strain"),
        "treatment": extracted.get("treatment"),
        "antibody_target": extracted.get("antibody_target"),
    }

    # Phase 2A: prefer extract_experiment.extract.extracted.antibody_target
    exp_block = d.get("extract_experiment", {})
    if isinstance(exp_block, dict):
        exp_extracted = (
            exp_block.get("extract", {}).get("extracted", {})
        )
        if isinstance(exp_extracted, dict):
            ab = exp_extracted.get("antibody_target")
            if ab:  # non-null, non-empty: override
                result["antibody_target"] = ab

    return result


# ---------------------------------------------------------------------------
# Stats TSV reader
# ---------------------------------------------------------------------------

def _read_stats_tsv(path: pathlib.Path) -> List[str]:
    if not path.exists():
        return []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                return line.split("\t")
    return []


def _strategy_from_output(output_root: pathlib.Path, acc: str) -> Optional[str]:
    for strat, sub in STRATEGY_DIRS.items():
        if (output_root / sub / acc).exists():
            return strat
    return None


# ---------------------------------------------------------------------------
# Path helpers for pipeline output files
# ---------------------------------------------------------------------------

def _opt_path(p: str) -> Optional[str]:
    """Return absolute path string if file exists, else None."""
    return p if os.path.exists(p) else None


def _chipseq_paths(output_root: pathlib.Path, strat_dir: str, acc: str) -> Dict[str, Any]:
    base = str(output_root / strat_dir / acc / acc)
    return {
        "bigwig_path": _opt_path(f"{base}.bw"),
        "peaks_q5_path": _opt_path(f"{base}.05_peaks.narrowPeak"),
        "peaks_q10_path": _opt_path(f"{base}.10_peaks.narrowPeak"),
        "peaks_q20_path": _opt_path(f"{base}.20_peaks.narrowPeak"),
    }


def _bsseq_paths(output_root: pathlib.Path, acc: str) -> Dict[str, Any]:
    base = str(output_root / "bsseq" / acc / acc)
    return {
        "cpg_methyl_bw_path": _opt_path(f"{base}.CpG.methyl.bw"),
        "cpg_cover_bw_path": _opt_path(f"{base}.CpG.cover.bw"),
        "cpg_hmr_path": _opt_path(f"{base}.CpG.hmr.bed"),
        "cpg_hypermr_path": _opt_path(f"{base}.CpG.hypermr.bed"),
        "cpg_pmd_path": _opt_path(f"{base}.CpG.pmd.bed"),
        "chg_methyl_bw_path": _opt_path(f"{base}.CHG.methyl.bw"),
        "chg_cover_bw_path": _opt_path(f"{base}.CHG.cover.bw"),
        "chg_hypermr_path": _opt_path(f"{base}.CHG.hypermr.bed"),
        "chh_methyl_bw_path": _opt_path(f"{base}.CHH.methyl.bw"),
        "chh_cover_bw_path": _opt_path(f"{base}.CHH.cover.bw"),
    }


def _count_bed_regions(path: Optional[str]) -> Optional[int]:
    """Count lines in a BED file (proxy for region count). Returns None if missing."""
    if not path or not os.path.exists(path):
        return None
    count = 0
    with open(path) as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Biosample accession lookup
# ---------------------------------------------------------------------------

def _biosample_accession(metadata_root: pathlib.Path, acc: str) -> Optional[str]:
    p = metadata_root / "biosamples" / f"{acc}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return d.get("accession")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Row collection
# ---------------------------------------------------------------------------

def collect_sample_rows(
    data_root: pathlib.Path,
    output_root: pathlib.Path,
    metadata_root: pathlib.Path,
) -> Iterator[Dict[str, Any]]:
    """Yield one dict per accession from the status directory."""
    status_dir = data_root / "status"
    if not status_dir.exists():
        return

    for marker in sorted(status_dir.iterdir()):
        acc = marker.stem
        if marker.suffix == ".ok":
            status = "ok"
        elif marker.suffix == ".failed":
            status = "failed"
        else:
            continue

        strat = _strategy_from_output(output_root, acc)
        if not strat and marker.suffix == ".failed":
            # Try to get strategy from the .failed file
            try:
                for line in marker.read_text().splitlines():
                    if line.startswith("strategy="):
                        strat_raw = line.split("=", 1)[1].strip()
                        # Normalise "ChIP-Seq" / "chipseq" etc.
                        for s in STRATEGY_DIRS:
                            if s.lower().replace("-", "") == strat_raw.lower().replace("-", ""):
                                strat = s
                                break
                        break
            except Exception:
                pass

        if not strat:
            log.warning("No strategy found for %s, skipping", acc)
            continue

        strat_dir = STRATEGY_DIRS[strat]
        output_dir = str(output_root / strat_dir / acc)
        biosample = _biosample_accession(metadata_root, acc)

        row: Dict[str, Any] = {
            "accession": acc,
            "library_strategy": strat,
            "status": status,
            "layout": None,
            "fastq_size": None,
            "reads_raw": None,
            "reads_filtered": None,
            "reads_mapped": None,
            "mapping_rate": None,
            "duplication_rate": None,
            "usable_rate": None,
            "elapsed_min": None,
            "biosample_accession": biosample,
            "output_dir": output_dir,
            "_chipseq_stats": {},
            "_bsseq_stats": {},
            "_chipseq_paths": {},
            "_bsseq_paths": {},
        }

        if status == "ok":
            stats_path = output_root / strat_dir / acc / f"{acc}.stats.tsv"
            raw = _read_stats_tsv(stats_path)
            if strat in ("ChIP-Seq", "ATAC-Seq"):
                parsed = parse_chipseq_stats(raw) if raw else {}
                row.update({
                    "layout": parsed.get("layout"),
                    "fastq_size": parsed.get("fastq_size"),
                    "reads_raw": parsed.get("reads_raw"),
                    "reads_filtered": parsed.get("reads_filtered"),
                    "reads_mapped": parsed.get("reads_mapped"),
                    "mapping_rate": parsed.get("mapping_rate"),
                    "duplication_rate": parsed.get("duplication_rate"),
                    "usable_rate": parsed.get("usable_rate"),
                    "elapsed_min": parsed.get("elapsed_min"),
                })
                row["_chipseq_stats"] = parsed
                row["_chipseq_paths"] = _chipseq_paths(output_root, strat_dir, acc)
            elif strat == "Bisulfite-Seq":
                parsed = parse_bsseq_stats(raw) if raw else {}
                row.update({
                    "layout": parsed.get("layout"),
                    "fastq_size": parsed.get("fastq_size"),
                    "reads_filtered": parsed.get("reads_filtered"),
                    "reads_mapped": None,
                    "mapping_rate": parsed.get("mapping_rate"),
                    "duplication_rate": None,
                    "elapsed_min": parsed.get("elapsed_min"),
                })
                row["_bsseq_stats"] = parsed
                row["_bsseq_paths"] = _bsseq_paths(output_root, acc)

        # Curation
        curated_path = metadata_root / "curated" / f"{acc}.json"
        if curated_path.exists():
            try:
                raw_json = json.loads(curated_path.read_text())
                row["_curation"] = parse_curation_json(raw_json)
            except Exception as e:
                log.warning("Could not parse curation for %s: %s", acc, e)
                row["_curation"] = {}
        else:
            row["_curation"] = {}

        yield row


# ---------------------------------------------------------------------------
# DB population
# ---------------------------------------------------------------------------

def populate_sample(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    """Insert (or replace) a row into the sample table."""
    conn.execute(
        """
        INSERT OR REPLACE INTO sample(
          accession, library_strategy, status, layout,
          fastq_size, reads_raw, reads_filtered, reads_mapped,
          mapping_rate, duplication_rate, usable_rate, elapsed_min,
          biosample_accession, output_dir
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["accession"],
            row["library_strategy"],
            row["status"],
            row.get("layout"),
            row.get("fastq_size"),
            row.get("reads_raw"),
            row.get("reads_filtered"),
            row.get("reads_mapped"),
            row.get("mapping_rate"),
            row.get("duplication_rate"),
            row.get("usable_rate"),
            row.get("elapsed_min"),
            row.get("biosample_accession"),
            row["output_dir"],
        ),
    )


def populate_curation(
    conn: sqlite3.Connection,
    accession: str,
    curation: Dict[str, Any],
) -> None:
    """Insert (or replace) a row into the sample_curation table."""
    conn.execute(
        """
        INSERT OR REPLACE INTO sample_curation(
          accession, tissue, cell_type, developmental_stage,
          genotype_strain, treatment, antibody_target
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            accession,
            curation.get("tissue"),
            curation.get("cell_type"),
            curation.get("developmental_stage"),
            curation.get("genotype_strain"),
            curation.get("treatment"),
            curation.get("antibody_target"),
        ),
    )


def populate_chipseq(
    conn: sqlite3.Connection,
    accession: str,
    stats: Dict[str, Any],
    paths: Dict[str, Any],
) -> None:
    """Insert (or replace) a row into the sample_chipseq table."""
    conn.execute(
        """
        INSERT OR REPLACE INTO sample_chipseq(
          accession, peaks_q5, peaks_q10, peaks_q20,
          bigwig_path, peaks_q5_path, peaks_q10_path, peaks_q20_path
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            accession,
            stats.get("peaks_q5"),
            stats.get("peaks_q10"),
            stats.get("peaks_q20"),
            paths.get("bigwig_path"),
            paths.get("peaks_q5_path"),
            paths.get("peaks_q10_path"),
            paths.get("peaks_q20_path"),
        ),
    )


def populate_bsseq(
    conn: sqlite3.Connection,
    accession: str,
    stats: Dict[str, Any],
    paths: Dict[str, Any],
) -> None:
    """Insert (or replace) a row into the sample_bsseq table."""
    # Count regions from BED files for per-context counts
    cpg_hmr_count = _count_bed_regions(paths.get("cpg_hmr_path"))
    cpg_hypermr_count = _count_bed_regions(paths.get("cpg_hypermr_path"))
    cpg_pmd_count = _count_bed_regions(paths.get("cpg_pmd_path"))
    chg_hypermr_count = _count_bed_regions(paths.get("chg_hypermr_path"))

    conn.execute(
        """
        INSERT OR REPLACE INTO sample_bsseq(
          accession,
          mean_cpg, mean_chg, mean_chh,
          cpg_hmr_count, cpg_hypermr_count, cpg_pmd_count, chg_hypermr_count,
          cpg_methyl_bw_path, cpg_cover_bw_path,
          cpg_hmr_path, cpg_hypermr_path, cpg_pmd_path,
          chg_methyl_bw_path, chg_cover_bw_path, chg_hypermr_path,
          chh_methyl_bw_path, chh_cover_bw_path
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            accession,
            stats.get("mean_cpg"),
            stats.get("mean_chg"),
            stats.get("mean_chh"),
            cpg_hmr_count,
            cpg_hypermr_count,
            cpg_pmd_count,
            chg_hypermr_count,
            paths.get("cpg_methyl_bw_path"),
            paths.get("cpg_cover_bw_path"),
            paths.get("cpg_hmr_path"),
            paths.get("cpg_hypermr_path"),
            paths.get("cpg_pmd_path"),
            paths.get("chg_methyl_bw_path"),
            paths.get("chg_cover_bw_path"),
            paths.get("chg_hypermr_path"),
            paths.get("chh_methyl_bw_path"),
            paths.get("chh_cover_bw_path"),
        ),
    )


# ---------------------------------------------------------------------------
# QC flags (sample_qc)
# ---------------------------------------------------------------------------

QC_TSV_NAMES = {"chip": "2026-10-01-chip-atlas-qc.tsv", "bs": "2026-10-01-bsseq-qc.tsv"}


def load_qc_tsvs(qc_dir: Optional[pathlib.Path]) -> Dict[str, Dict[str, str]]:
    """accession -> raw TSV row (with '_kind' = chip|bs). Empty if qc_dir is None."""
    out: Dict[str, Dict[str, str]] = {}
    if qc_dir is None:
        return out
    for kind, name in QC_TSV_NAMES.items():
        p = qc_dir / name
        if not p.exists():
            continue
        with p.open() as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                out[r["accession"]] = {**r, "_kind": kind}
    return out


def _metrics_source(pipeline_used: bool, has_tsv: bool) -> Optional[str]:
    """Provenance of the numbers behind a sample's QC flag."""
    if pipeline_used:
        return "pipeline+subsample" if has_tsv else "pipeline"
    return "subsample-2026-10-01" if has_tsv else None


def qc_row(row: Dict[str, Any], tsv: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """Merge pipeline stats (preferred when zenigoke-v2) with subsample estimates.

    Pipeline values override TSV values only when they are present (not None).
    metrics_source records whether any pipeline-derived value was used.
    """
    strat, status = row["library_strategy"], row["status"]
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        m = qc_flags.chip_metrics_from_tsv(tsv) if tsv else {}
        m["status"] = status
        st = row.get("_chipseq_stats", {})
        pipeline_used = False
        if st.get("format") == "zenigoke-v2":
            pe = row.get("layout") == "1"
            nuc = st.get("nuclear_usable_reads")
            usable = st.get("usable_reads")
            org = st.get("organelle_usable_reads")
            pipeline = {
                "aligned_pct": st.get("mapping_rate"),
                "dup_pct": st.get("duplication_rate"),
                "usable_fragments": None if nuc is None else (nuc / 2 if pe else nuc),
                "organelle_pct": 100.0 * org / usable if org is not None and usable else None,
                "peaks_q05": st.get("peaks_q5"),
                "peaks_in_organelle_like_pct": 0.0,  # v2 peaks are blacklist-filtered
            }
            m.update({k: v for k, v in pipeline.items() if v is not None})
            pipeline_used = True
        source = _metrics_source(pipeline_used, tsv is not None)
        flag, reasons, peak_flag = qc_flags.chip_flag(m)
        hum, bac = _safe_float(m.get("human_pct")), _safe_float(m.get("bacterial_pct"))
        foreign = None if hum is None and bac is None else (hum or 0.0) + (bac or 0.0)
        return {"qc_flag": flag, "qc_reasons": "; ".join(reasons), "peak_flag": peak_flag,
                "aligned_pct": _safe_float(m.get("aligned_pct")), "dup_pct": _safe_float(m.get("dup_pct")),
                "usable_fragments": _safe_int(_safe_float(m.get("usable_fragments"))),
                "organelle_pct": _safe_float(m.get("organelle_pct")), "foreign_pct": foreign,
                "peaks_in_organelle_like_pct": _safe_float(m.get("peaks_in_organelle_like_pct")),
                "nonconv_pct": None, "frac_cpg_cov_ge5": None, "metrics_source": source}
    m = qc_flags.bs_metrics_from_tsv(tsv) if tsv else {}
    m["status"] = status
    st = row.get("_bsseq_stats", {})
    pipeline_used = False
    for k in ("mean_cpg", "mean_chg", "mean_chh", "nonconv"):
        if st.get(k) is not None:
            m[k] = st[k]
            pipeline_used = True
    # organelle_pct has two bases: pipeline col 18 = % of dedup records on non-nuclear sequences;
    # the TSV fallback pct_reads_chrC = % of subsample reads on chrC.
    if st.get("organelle_pct") is not None:
        organelle = _safe_float(st["organelle_pct"])
        pipeline_used = True
    else:
        organelle = _safe_float((tsv or {}).get("pct_reads_chrC"))
    flag, reasons = qc_flags.bs_flag(m)
    nc = _safe_float(m.get("nonconv"))
    return {"qc_flag": flag, "qc_reasons": "; ".join(reasons), "peak_flag": None,
            "aligned_pct": None, "dup_pct": None, "usable_fragments": None,
            "organelle_pct": organelle,
            "foreign_pct": _safe_float(m.get("foreign_pct")),
            "peaks_in_organelle_like_pct": None,
            "nonconv_pct": nc * 100 if nc is not None else None,
            "frac_cpg_cov_ge5": _safe_float(m.get("frac_cpg_cov_ge5")),
            "metrics_source": _metrics_source(pipeline_used, tsv is not None)}


def populate_qc(conn: sqlite3.Connection, accession: str, q: Dict[str, Any]) -> None:
    cols = ["qc_flag", "qc_reasons", "peak_flag", "aligned_pct", "dup_pct", "usable_fragments",
            "organelle_pct", "foreign_pct", "peaks_in_organelle_like_pct", "nonconv_pct",
            "frac_cpg_cov_ge5", "metrics_source"]
    conn.execute(
        f"INSERT OR REPLACE INTO sample_qc(accession, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))})",
        [accession] + [q.get(c) for c in cols],
    )


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build(
    data_root: pathlib.Path,
    metadata_root: pathlib.Path,
    db_path: pathlib.Path,
    qc_dir: Optional[pathlib.Path] = None,
    curation_overrides: Optional[pathlib.Path] = None,
) -> Dict[str, int]:
    """Drop + recreate the DB, populate all tables. Returns row count dict."""
    output_root = data_root / "output"
    qc = load_qc_tsvs(qc_dir)
    overrides = load_curation_overrides(curation_overrides)
    if qc_dir is not None and not any((qc_dir / n).exists() for n in QC_TSV_NAMES.values()):
        log.warning("qc-dir %s contains none of the QC TSVs %s; sample_qc will use pipeline stats only",
                    qc_dir, sorted(QC_TSV_NAMES.values()))

    # Drop and recreate
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)

    counts = {
        "sample": 0,
        "sample_curation": 0,
        "sample_chipseq": 0,
        "sample_bsseq": 0,
        "sample_qc": 0,
    }

    for row in collect_sample_rows(data_root, output_root, metadata_root):
        acc = row["accession"]
        strat = row["library_strategy"]
        status = row["status"]

        try:
            populate_sample(conn, row)
            counts["sample"] += 1
        except Exception as e:
            log.error("Failed to insert sample row for %s: %s", acc, e)
            continue

        try:
            populate_qc(conn, acc, qc_row(row, qc.get(acc)))
            counts["sample_qc"] += 1
        except Exception as e:
            log.warning("Failed to insert QC row for %s: %s", acc, e)

        curation = row.get("_curation", {})
        if acc in overrides:
            curation = apply_curation_overrides(curation, overrides[acc])
        if curation:
            try:
                populate_curation(conn, acc, curation)
                counts["sample_curation"] += 1
            except Exception as e:
                log.warning("Failed to insert curation for %s: %s", acc, e)

        if status == "ok":
            if strat in ("ChIP-Seq", "ATAC-Seq"):
                chip_stats = row.get("_chipseq_stats", {})
                chip_paths = row.get("_chipseq_paths", {})
                try:
                    populate_chipseq(conn, acc, chip_stats, chip_paths)
                    counts["sample_chipseq"] += 1
                except Exception as e:
                    log.warning("Failed to insert chipseq row for %s: %s", acc, e)

            elif strat == "Bisulfite-Seq":
                bs_stats = row.get("_bsseq_stats", {})
                bs_paths = row.get("_bsseq_paths", {})
                try:
                    populate_bsseq(conn, acc, bs_stats, bs_paths)
                    counts["sample_bsseq"] += 1
                except Exception as e:
                    log.warning("Failed to insert bsseq row for %s: %s", acc, e)

    conn.commit()
    conn.close()
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="Build SQLite catalog DB from zenigoke pipeline outputs."
    )
    p.add_argument("--data-root", default="/data1/zenigoke")
    p.add_argument("--metadata-root", default="metadata")
    p.add_argument("--output", default="db/kknmsmd.db")
    p.add_argument("--qc-dir", default="docs/qc",
                   help="directory with the 2026-10-01 QC TSVs (default: docs/qc)")
    p.add_argument("--curation-overrides", default="configs/curation_overrides.tsv",
                   help="TSV file with curation field overrides (default: configs/curation_overrides.tsv)")
    args = p.parse_args(argv)

    data_root = pathlib.Path(args.data_root)
    metadata_root = pathlib.Path(args.metadata_root)
    db_path = pathlib.Path(args.output)
    curation_overrides_path = pathlib.Path(args.curation_overrides)

    print(f"Building catalog DB: {db_path}")
    print(f"  data-root:     {data_root}")
    print(f"  metadata-root: {metadata_root}")
    print(f"  qc-dir:        {args.qc_dir}")
    print(f"  curation-overrides: {curation_overrides_path}")

    counts = build(data_root, metadata_root, db_path, qc_dir=pathlib.Path(args.qc_dir),
                   curation_overrides=curation_overrides_path)

    print("\nRow counts:")
    for table, n in counts.items():
        print(f"  {table:20s}: {n}")

    db_size = db_path.stat().st_size if db_path.exists() else 0
    print(f"\nDB size: {db_size / 1024:.1f} KB  ({db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
