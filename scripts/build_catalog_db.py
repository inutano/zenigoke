"""Build a SQLite catalog DB from zenigoke pipeline outputs + curated metadata.

Stdlib + sqlite3 only.

Public API:
  init_schema(conn)
  parse_chipseq_stats(row)     -> dict
  parse_bsseq_stats(row)       -> dict
  parse_curation_json(d)       -> dict
  collect_sample_rows(...)     -> Iterator[dict]
  populate_sample(conn, row)
  populate_curation(conn, accession, curation)
  populate_chipseq(conn, accession, output_dir, stats)
  populate_bsseq(conn, accession, output_dir, stats)
  build(data_root, metadata_root, db_path)
  main(argv=None)
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
from typing import Any, Dict, Iterator, List, Optional

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
  reads_filtered       INTEGER,
  mapping_rate         REAL,
  duplication_rate     REAL,
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
            "sample", "layout", "fastq_size", "reads_raw", "reads_filt",
            "reads_mapped", "mapping_rate", "duplication_rate",
            "dedup_bam_size", "bedgraph_size", "bigwig_size",
            "peaks_q5", "peaks_q10", "peaks_q20", "elapsed_min",
        ]
        return {k: v for k, v in zip(keys, row)}

    def _bsseq_fields_fn(row: List[str]) -> Dict[str, str]:  # fallback
        keys = [
            "sample", "layout", "fastq_size", "dedup_bam_size", "read_count",
            "mapping_rate", "methylation_rate", "cpg_coverage",
            "hmr_count", "pmd_count", "hypermr_count", "elapsed_min",
            "mean_cpg", "mean_chg", "mean_chh",
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
    """Parse a 15-column ChIP/ATAC stats TSV row into a typed dict."""
    fields = _chipseq_fields_fn(row)
    return {
        "layout": fields.get("layout"),
        "reads_filtered": _safe_int(fields.get("reads_filt")),
        "mapping_rate": _safe_float(fields.get("mapping_rate")),
        "duplication_rate": _safe_float(fields.get("duplication_rate")),
        "elapsed_min": _safe_float(fields.get("elapsed_min")),
        "peaks_q5": _safe_int(fields.get("peaks_q5")),
        "peaks_q10": _safe_int(fields.get("peaks_q10")),
        "peaks_q20": _safe_int(fields.get("peaks_q20")),
    }


def parse_bsseq_stats(row: List[str]) -> Dict[str, Any]:
    """Parse a 15-column BS-seq stats TSV row into a typed dict."""
    fields = _bsseq_fields_fn(row)
    return {
        "layout": fields.get("layout"),
        "reads_filtered": _safe_int(fields.get("read_count")),
        "mapping_rate": _safe_float(fields.get("mapping_rate")),
        "duplication_rate": None,  # not in bsseq stats
        "elapsed_min": _safe_float(fields.get("elapsed_min")),
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
# Curation parsing
# ---------------------------------------------------------------------------

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
            "reads_filtered": None,
            "mapping_rate": None,
            "duplication_rate": None,
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
                    "reads_filtered": parsed.get("reads_filtered"),
                    "mapping_rate": parsed.get("mapping_rate"),
                    "duplication_rate": parsed.get("duplication_rate"),
                    "elapsed_min": parsed.get("elapsed_min"),
                })
                row["_chipseq_stats"] = parsed
                row["_chipseq_paths"] = _chipseq_paths(output_root, strat_dir, acc)
            elif strat == "Bisulfite-Seq":
                parsed = parse_bsseq_stats(raw) if raw else {}
                row.update({
                    "layout": parsed.get("layout"),
                    "reads_filtered": parsed.get("reads_filtered"),
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
          reads_filtered, mapping_rate, duplication_rate, elapsed_min,
          biosample_accession, output_dir
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["accession"],
            row["library_strategy"],
            row["status"],
            row.get("layout"),
            row.get("reads_filtered"),
            row.get("mapping_rate"),
            row.get("duplication_rate"),
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
# Main build function
# ---------------------------------------------------------------------------

def build(
    data_root: pathlib.Path,
    metadata_root: pathlib.Path,
    db_path: pathlib.Path,
) -> Dict[str, int]:
    """Drop + recreate the DB, populate all tables. Returns row count dict."""
    output_root = data_root / "output"

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

        curation = row.get("_curation", {})
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
    args = p.parse_args(argv)

    data_root = pathlib.Path(args.data_root)
    metadata_root = pathlib.Path(args.metadata_root)
    db_path = pathlib.Path(args.output)

    print(f"Building catalog DB: {db_path}")
    print(f"  data-root:     {data_root}")
    print(f"  metadata-root: {metadata_root}")

    counts = build(data_root, metadata_root, db_path)

    print("\nRow counts:")
    for table, n in counts.items():
        print(f"  {table:20s}: {n}")

    db_size = db_path.stat().st_size if db_path.exists() else 0
    print(f"\nDB size: {db_size / 1024:.1f} KB  ({db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
