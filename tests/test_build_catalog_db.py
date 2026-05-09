"""Tests for build_catalog_db.

TDD: these tests define the expected interface and are written before the
implementation module exists.

Test coverage:
  1. schema_creation  — init_schema creates all 5 tables and 6 indices.
  2. sample_population — populate_sample writes correct row from stats TSV.
  3. curation_merge   — populate_curation merges extract + extract_experiment
                        with antibody_target preference for extract_experiment.
  4. anchor_query     — end-to-end tiny DB, anchor SQL returns expected rows.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import build_catalog_db as bcd  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# Test 1: schema creation
# ---------------------------------------------------------------------------

def test_init_schema_creates_all_tables_and_indices():
    conn = _conn()
    bcd.init_schema(conn)

    # Check tables
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "sample" in tables
    assert "sample_curation" in tables
    assert "sample_chipseq" in tables
    assert "sample_bsseq" in tables

    # Check indices (6 required)
    indices = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    expected_indices = {
        "idx_curation_tissue",
        "idx_curation_stage",
        "idx_curation_strain",
        "idx_curation_antibody",
        "idx_sample_strategy",
        "idx_sample_status",
    }
    assert expected_indices.issubset(indices), (
        f"Missing indices: {expected_indices - indices}"
    )
    conn.close()


# ---------------------------------------------------------------------------
# Test 2: sample row population
# ---------------------------------------------------------------------------

def test_populate_sample_writes_correct_row(tmp_path):
    """Given a fake status dir + stats TSV, populate_sample writes the right row."""
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    (status_dir / "SRX1.ok").write_text("")

    # 15-column ChIP-seq stats TSV (same layout as build_report._chipseq_stats_fields)
    stats_dir = tmp_path / "output" / "chipseq" / "SRX1"
    _write(
        stats_dir / "SRX1.stats.tsv",
        "SRX1\tPE\t1.2G\t10000000\t9500000\t9000000\t0.92\t0.11\t"
        "500M\t200M\t50M\t1200\t300\t80\t5.3\n",
    )

    conn = _conn()
    bcd.init_schema(conn)

    row = {
        "accession": "SRX1",
        "library_strategy": "ChIP-Seq",
        "status": "ok",
        "layout": "PE",
        "fastq_size": "1.2G",
        "reads_filtered": 9500000,
        "reads_mapped": 9000000,
        "mapping_rate": 0.92,
        "duplication_rate": 0.11,
        "elapsed_min": 5.3,
        "biosample_accession": "SAMN12345",
        "output_dir": str(stats_dir.parent),
    }
    bcd.populate_sample(conn, row)
    conn.commit()

    result = conn.execute(
        "SELECT * FROM sample WHERE accession='SRX1'"
    ).fetchone()
    assert result is not None
    assert result["accession"] == "SRX1"
    assert result["library_strategy"] == "ChIP-Seq"
    assert result["status"] == "ok"
    assert result["layout"] == "PE"
    assert result["fastq_size"] == "1.2G"
    assert result["reads_filtered"] == 9500000
    assert result["reads_mapped"] == 9000000
    assert abs(result["mapping_rate"] - 0.92) < 1e-6
    assert abs(result["duplication_rate"] - 0.11) < 1e-6
    assert abs(result["elapsed_min"] - 5.3) < 1e-6
    assert result["biosample_accession"] == "SAMN12345"
    conn.close()


# ---------------------------------------------------------------------------
# Test 3: curation merge
# ---------------------------------------------------------------------------

def test_populate_curation_merges_extract_and_extract_experiment(tmp_path):
    """Antibody from extract_experiment takes priority over extract."""
    # Build curated JSON with extract (no antibody) + extract_experiment (has antibody)
    curated_json = {
        "extract": {
            "accession": "SAMN99999",
            "extracted": {
                "tissue": "thallus",
                "cell_type": None,
                "developmental_stage": "thallus",
                "genotype_strain": "Tak-1",
                "treatment": None,
                "antibody_target": None,  # null in BioSample pass
            },
        },
        "extract_experiment": {
            "extract": {
                "accession": "SRX1",
                "extracted": {
                    "antibody_target": "H3K4me3",  # found in Experiment XML
                },
            },
        },
    }

    # parse_curation_json should merge these
    curation = bcd.parse_curation_json(curated_json)

    assert curation["tissue"] == "thallus"
    assert curation["developmental_stage"] == "thallus"
    assert curation["genotype_strain"] == "Tak-1"
    assert curation["treatment"] is None
    # antibody_target must come from extract_experiment
    assert curation["antibody_target"] == "H3K4me3"

    # Now write to DB and verify
    conn = _conn()
    bcd.init_schema(conn)

    # Insert parent sample row first (FK constraint)
    conn.execute(
        "INSERT INTO sample(accession, library_strategy, status, output_dir) "
        "VALUES ('SRX1', 'ChIP-Seq', 'ok', '/tmp/fake')"
    )
    bcd.populate_curation(conn, "SRX1", curation)
    conn.commit()

    result = conn.execute(
        "SELECT * FROM sample_curation WHERE accession='SRX1'"
    ).fetchone()
    assert result is not None
    assert result["tissue"] == "thallus"
    assert result["antibody_target"] == "H3K4me3"
    conn.close()


def test_populate_curation_falls_back_to_extract_when_experiment_null(tmp_path):
    """When extract_experiment.antibody_target is null, fall back to extract value."""
    curated_json = {
        "extract": {
            "accession": "SAMN88888",
            "extracted": {
                "tissue": "sporophyte",
                "cell_type": None,
                "developmental_stage": None,
                "genotype_strain": None,
                "treatment": None,
                "antibody_target": "H3K27me3",  # has value in BioSample pass
            },
        },
        "extract_experiment": {
            "extract": {
                "accession": "SRX2",
                "extracted": {
                    "antibody_target": None,  # null in Experiment XML
                },
            },
        },
    }

    curation = bcd.parse_curation_json(curated_json)
    # Should fall back to extract's antibody_target
    assert curation["antibody_target"] == "H3K27me3"
    assert curation["tissue"] == "sporophyte"


# ---------------------------------------------------------------------------
# Test 4: anchor query end-to-end
# ---------------------------------------------------------------------------

def test_anchor_query_returns_expected_rows():
    """Build a tiny DB with 3 samples, run anchor SQL, get correct results."""
    conn = _conn()
    bcd.init_schema(conn)

    # Insert 3 samples: 2 ChIP-Seq (1 H3K4me3, 1 H3K27me3), 1 BS-seq
    samples = [
        {
            "accession": "SRX_H3K4me3",
            "library_strategy": "ChIP-Seq",
            "status": "ok",
            "layout": "PE",
            "reads_filtered": 10000000,
            "mapping_rate": 0.92,
            "duplication_rate": 0.10,
            "elapsed_min": 10.0,
            "biosample_accession": "SAMN001",
            "output_dir": "/fake/chipseq/SRX_H3K4me3",
        },
        {
            "accession": "SRX_H3K27me3",
            "library_strategy": "ChIP-Seq",
            "status": "ok",
            "layout": "SE",
            "reads_filtered": 8000000,
            "mapping_rate": 0.88,
            "duplication_rate": 0.15,
            "elapsed_min": 8.0,
            "biosample_accession": "SAMN002",
            "output_dir": "/fake/chipseq/SRX_H3K27me3",
        },
        {
            "accession": "SRX_bsseq",
            "library_strategy": "Bisulfite-Seq",
            "status": "ok",
            "layout": "PE",
            "reads_filtered": 5000000,
            "mapping_rate": 0.75,
            "duplication_rate": None,
            "elapsed_min": 20.0,
            "biosample_accession": "SAMN003",
            "output_dir": "/fake/bsseq/SRX_bsseq",
        },
    ]
    for row in samples:
        bcd.populate_sample(conn, row)

    curations = {
        "SRX_H3K4me3": {
            "tissue": "thallus",
            "cell_type": None,
            "developmental_stage": "thallus",
            "genotype_strain": "Tak-1",
            "treatment": None,
            "antibody_target": "H3K4me3",
        },
        "SRX_H3K27me3": {
            "tissue": "thallus",
            "cell_type": None,
            "developmental_stage": "thallus",
            "genotype_strain": "Tak-1",
            "treatment": None,
            "antibody_target": "H3K27me3",
        },
        "SRX_bsseq": {
            "tissue": "thallus",
            "cell_type": None,
            "developmental_stage": "thallus",
            "genotype_strain": "Tak-1",
            "treatment": None,
            "antibody_target": None,
        },
    }
    for acc, cur in curations.items():
        bcd.populate_curation(conn, acc, cur)

    conn.commit()

    # Run the spec's anchor query (adapted: filter by antibody only)
    rows = conn.execute(
        """
        SELECT s.accession, s.library_strategy, c.tissue, c.developmental_stage,
               c.genotype_strain, c.antibody_target
        FROM sample s LEFT JOIN sample_curation c USING (accession)
        WHERE c.tissue='thallus' AND c.antibody_target='H3K4me3'
        """
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["accession"] == "SRX_H3K4me3"
    assert rows[0]["library_strategy"] == "ChIP-Seq"
    assert rows[0]["antibody_target"] == "H3K4me3"

    conn.close()
