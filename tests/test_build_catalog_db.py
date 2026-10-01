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

    # 15-column legacy ChIP-seq stats TSV (same layout as
    # build_report._chipseq_stats_fields): col 4 is the literal "0" placeholder,
    # col 5 = reads before fastp, col 6 = after, col 7 = usable %.
    stats_dir = tmp_path / "output" / "chipseq" / "SRX1"
    _write(
        stats_dir / "SRX1.stats.tsv",
        "SRX1\tPE\t1.2G\t0\t10000000\t9500000\t0.92\t0.08\t"
        "500M\t200M\t50M\t1200\t300\t80\t5.3\n",
    )
    parsed = bcd.parse_chipseq_stats(
        bcd._read_stats_tsv(stats_dir / "SRX1.stats.tsv")
    )

    conn = _conn()
    bcd.init_schema(conn)

    row = {
        "accession": "SRX1",
        "library_strategy": "ChIP-Seq",
        "status": "ok",
        "layout": parsed["layout"],
        "fastq_size": parsed["fastq_size"],
        "reads_raw": parsed["reads_raw"],
        "reads_filtered": parsed["reads_filtered"],
        "reads_mapped": parsed["reads_mapped"],
        "mapping_rate": parsed["mapping_rate"],
        "duplication_rate": parsed["duplication_rate"],
        "usable_rate": parsed["usable_rate"],
        "elapsed_min": parsed["elapsed_min"],
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
    assert result["reads_raw"] == 10000000
    assert result["reads_filtered"] == 9500000
    # Legacy format has no alignment/duplication rate or mapped-read count.
    assert result["reads_mapped"] is None
    assert result["mapping_rate"] is None
    assert result["duplication_rate"] is None
    assert abs(result["usable_rate"] - 0.92) < 1e-6
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


# ---------------------------------------------------------------------------
# Test: legacy vs zenigoke-v2 stats parsing
# ---------------------------------------------------------------------------

LEGACY_CHIP = ["SRX1", "1", "3829082553", "0", "53268038", "52973920", "21.3", "78.7",
               "1661569781", "365054360", "68579040", "1097", "772", "403", "21.48"]
V2_CHIP = ["SRX2", "1", "3829082553", "0", "53268038", "52973920", "93.0", "20.0",
           "1661569781", "365054360", "68579040", "1097", "772", "403", "21.48",
           "zenigoke-v2", "49265000", "9853000", "39412000", "412000", "39000000"]


def test_parse_chipseq_stats_legacy_vs_v2():
    legacy = bcd.parse_chipseq_stats(LEGACY_CHIP)
    assert legacy["format"] == "legacy"
    assert legacy["reads_raw"] == 53268038
    assert legacy["reads_filtered"] == 52973920
    assert legacy["usable_rate"] == 21.3
    assert legacy["mapping_rate"] is None
    assert legacy["duplication_rate"] is None

    v2 = bcd.parse_chipseq_stats(V2_CHIP)
    assert v2["format"] == "zenigoke-v2"
    assert v2["mapping_rate"] == 93.0
    assert v2["duplication_rate"] == 20.0
    assert v2["reads_mapped"] == 49265000
    assert v2["nuclear_usable_reads"] == 39000000
    assert abs(v2["usable_rate"] - 100 * 39412000 / 52973920) < 0.05


def test_parse_bsseq_stats_v2_extra_columns():
    base = ["SRX3", "1", "100", "50", "17561884", "73.6", "30.0", "5.0",
            "22", "0", "3", "40", "0.3030", "0.1430", "0.0480"]
    assert bcd.parse_bsseq_stats(base)["nonconv"] is None
    v2 = bcd.parse_bsseq_stats(base + ["zenigoke-v2", "60.1", "45.2", "0.0207"])
    assert v2["unique_rate"] == 60.1
    assert v2["organelle_pct"] == 45.2
    assert v2["nonconv"] == 0.0207


# ---------------------------------------------------------------------------
# Test: sample_qc population (QC TSV + pipeline stats)
# ---------------------------------------------------------------------------

def test_build_populates_sample_qc_from_tsv(tmp_path):
    data_root = tmp_path / "data"
    out = data_root / "output" / "chipseq" / "SRX9800303"
    _write(out / "SRX9800303.stats.tsv", "\t".join(LEGACY_CHIP).replace("SRX1", "SRX9800303") + "\n")
    _write(data_root / "status" / "SRX9800303.ok", "")
    qc_dir = tmp_path / "qc"
    _write(qc_dir / "2026-10-01-chip-atlas-qc.tsv",
           "accession\tstrategy\tstatus\taligned_pct\tdup_pct\tusable_fragments\torganelle_pct\t"
           "human_pct\tother_unmapped_pct\tpeaks_q05\tpeaks_in_organelle_like_pct\tqc_flag\tpeak_flag\n"
           "SRX9800303\tChIP-Seq\tok\t12.9\t26.6\t5600000\t0.3\t0.2\t86\t2662\t1.0\tFAIL\tOK\n")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db, qc_dir=qc_dir)
    row = sqlite3.connect(str(db)).execute(
        "SELECT qc_flag, aligned_pct, metrics_source FROM sample_qc WHERE accession='SRX9800303'").fetchone()
    assert row == ("FAIL", 12.9, "subsample-2026-10-01")


def test_build_sample_qc_without_tsv_uses_pipeline_stats(tmp_path):
    """v2 pipeline stats alone give a flag; legacy without any TSV is NA with no source."""
    data_root = tmp_path / "data"
    v2 = V2_CHIP[:]
    v2[0] = "SRX9800304"
    _write(data_root / "output" / "chipseq" / "SRX9800304" / "SRX9800304.stats.tsv", "\t".join(v2) + "\n")
    _write(data_root / "status" / "SRX9800304.ok", "")
    legacy = LEGACY_CHIP[:]
    legacy[0] = "SRX9800305"
    _write(data_root / "output" / "chipseq" / "SRX9800305" / "SRX9800305.stats.tsv", "\t".join(legacy) + "\n")
    _write(data_root / "status" / "SRX9800305.ok", "")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db)
    c = sqlite3.connect(str(db))
    v2row = c.execute("SELECT qc_flag, aligned_pct, dup_pct, metrics_source FROM sample_qc "
                      "WHERE accession='SRX9800304'").fetchone()
    assert v2row == ("PASS", 93.0, 20.0, "pipeline")
    assert c.execute("SELECT qc_flag, metrics_source FROM sample_qc WHERE accession='SRX9800305'"
                     ).fetchone() == ("NA", None)


# ---------------------------------------------------------------------------
# Tests: sample_qc merge paths (pipeline stats + subsample TSV)
# ---------------------------------------------------------------------------

CHIP_QC_HEADER = ("accession\tstrategy\tstatus\taligned_pct\tdup_pct\tusable_fragments\torganelle_pct\t"
                  "human_pct\tother_unmapped_pct\tpeaks_q05\tpeaks_in_organelle_like_pct\tqc_flag\tpeak_flag\n")
BS_QC_HEADER = ("accession\tmean_cpg\tmean_chg\tmean_chh\tpct_reads_chrC\tpct_reads_bacteria_panel\t"
                "pct_reads_unmapped_all_refs\tnonconv_chrC_CHH\tfrac_cpg_cov_ge5\tqc_flag\n")
BS_LEGACY = ["SRX3", "1", "100", "50", "17561884", "73.6", "30.0", "5.0",
             "22", "0", "3", "40", "0.3030", "0.1430", "0.0480"]


def _qc_db_row(db, acc):
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return dict(c.execute("SELECT * FROM sample_qc WHERE accession=?", (acc,)).fetchone())


def _put_stats(data_root, sub, acc, cols):
    cols = [acc] + list(cols[1:])
    _write(data_root / "output" / sub / acc / f"{acc}.stats.tsv", "\t".join(cols) + "\n")
    _write(data_root / "status" / f"{acc}.ok", "")


def test_sample_qc_bs_v2_with_tsv_prefers_pipeline_values(tmp_path):
    data_root, qc_dir = tmp_path / "data", tmp_path / "qc"
    _put_stats(data_root, "bsseq", "SRX9800310",
               BS_LEGACY + ["zenigoke-v2", "60.1", "45.2", "0.0207"])
    # TSV would say FAIL (all-context methylation 0.99, nonconv 0.005 -> no warn), pipeline says otherwise
    _write(qc_dir / "2026-10-01-bsseq-qc.tsv", BS_QC_HEADER +
           "SRX9800310\t0.99\t0.99\t0.99\t2.78\t5\t10\t0.005\t0.5\tFAIL\n")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db, qc_dir=qc_dir)
    q = _qc_db_row(db, "SRX9800310")
    # flag from pipeline means (0.303/0.143/0.048) + pipeline nonconv 2.07% (WARN, not the TSV 0.5%)
    assert q["qc_flag"] == "WARN"
    assert "non-conversion 2.1% > 2%" in q["qc_reasons"]
    assert "wrong strand" not in q["qc_reasons"]
    assert q["nonconv_pct"] == pytest.approx(2.07)
    assert q["organelle_pct"] == 45.2          # pipeline col 18, not TSV pct_reads_chrC (2.78)
    assert q["foreign_pct"] == 15.0            # TSV unmapped (10) + bacteria (5)
    assert q["frac_cpg_cov_ge5"] == 0.5        # TSV only
    assert q["metrics_source"] == "pipeline+subsample"
    assert q["peak_flag"] is None and q["aligned_pct"] is None


def test_sample_qc_bs_legacy_with_tsv_source_reflects_pipeline_means(tmp_path):
    data_root, qc_dir = tmp_path / "data", tmp_path / "qc"
    _put_stats(data_root, "bsseq", "SRX9800311", BS_LEGACY)
    _write(qc_dir / "2026-10-01-bsseq-qc.tsv", BS_QC_HEADER +
           "SRX9800311\t0.1\t0.1\t0.1\t2.78\t5\t10\t0.003\t0.5\tPASS\n")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db, qc_dir=qc_dir)
    q = _qc_db_row(db, "SRX9800311")
    assert q["qc_flag"] == "PASS"
    # legacy stats have no nonconv/organelle_pct: those fall back to the TSV...
    assert q["nonconv_pct"] == pytest.approx(0.3)
    assert q["organelle_pct"] == 2.78
    # ...but the means used for the flag came from the pipeline, so the source says so
    assert q["metrics_source"] == "pipeline+subsample"


def test_sample_qc_bs_pipeline_only_source(tmp_path):
    data_root = tmp_path / "data"
    _put_stats(data_root, "bsseq", "SRX9800312", BS_LEGACY)
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db)
    q = _qc_db_row(db, "SRX9800312")
    assert q["qc_flag"] == "PASS" and q["metrics_source"] == "pipeline"


def test_sample_qc_chip_pe_v2_with_tsv_prefers_pipeline_values(tmp_path):
    data_root, qc_dir = tmp_path / "data", tmp_path / "qc"
    _put_stats(data_root, "chipseq", "SRX9800313", V2_CHIP)   # layout "1" = PE
    # TSV alone would FAIL (aligned 12.9) and flag NUPT peaks (30%); pipeline says clean
    _write(qc_dir / "2026-10-01-chip-atlas-qc.tsv", CHIP_QC_HEADER +
           "SRX9800313\tChIP-Seq\tok\t12.9\t90\t100000\t50\t0.2\t3\t3000\t30\tFAIL\tWARN\n")
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db, qc_dir=qc_dir)
    q = _qc_db_row(db, "SRX9800313")
    assert q["qc_flag"] == "PASS", q["qc_reasons"]
    assert q["aligned_pct"] == 93.0 and q["dup_pct"] == 20.0
    assert q["usable_fragments"] == 19500000                    # nuclear usable 39.0M / 2 (PE)
    assert q["organelle_pct"] == pytest.approx(100.0 * 412000 / 39412000)
    assert q["foreign_pct"] == pytest.approx(3.2)               # TSV human 0.2 + bacterial-like 3
    assert q["peak_flag"] == "OK"
    assert q["peaks_in_organelle_like_pct"] == 0.0
    assert q["metrics_source"] == "pipeline+subsample"


def test_sample_qc_chip_se_v2_fragments_equal_reads(tmp_path):
    data_root = tmp_path / "data"
    v2 = V2_CHIP[:]
    v2[1] = "0"                                                  # SE
    _put_stats(data_root, "chipseq", "SRX9800314", v2)
    db = tmp_path / "k.db"
    bcd.build(data_root, tmp_path / "meta", db)
    q = _qc_db_row(db, "SRX9800314")
    assert q["usable_fragments"] == 39000000
    assert q["metrics_source"] == "pipeline"


def test_qc_row_v2_missing_pipeline_field_keeps_tsv_value():
    """A blank pipeline field must not overwrite (or erase) the subsample value."""
    row = {"library_strategy": "ChIP-Seq", "status": "ok", "layout": "1",
           "_chipseq_stats": {"format": "zenigoke-v2", "mapping_rate": None, "duplication_rate": 20.0,
                              "nuclear_usable_reads": None, "usable_reads": 39412000,
                              "organelle_usable_reads": None, "peaks_q5": 1097}}
    tsv = {"_kind": "chip", "status": "ok", "aligned_pct": "88", "dup_pct": "55",
           "usable_fragments": "5600000", "organelle_pct": "1.5", "human_pct": "0",
           "other_unmapped_pct": "0", "peaks_q05": "10", "peaks_in_organelle_like_pct": "0"}
    q = bcd.qc_row(row, tsv)
    assert q["aligned_pct"] == 88.0            # kept from TSV (pipeline value missing)
    assert q["dup_pct"] == 20.0                # pipeline value present -> wins
    assert q["usable_fragments"] == 5600000
    assert q["organelle_pct"] == 1.5
    assert q["metrics_source"] == "pipeline+subsample"


def test_qc_row_legacy_chip_source_is_subsample_only():
    """Legacy ChIP stats carry no QC fields, so a legacy sample is purely subsample-based."""
    row = {"library_strategy": "ChIP-Seq", "status": "ok", "layout": "1",
           "_chipseq_stats": bcd.parse_chipseq_stats(LEGACY_CHIP)}
    tsv = {"_kind": "chip", "status": "ok", "aligned_pct": "90", "dup_pct": "10",
           "usable_fragments": "9000000", "organelle_pct": "1", "human_pct": "0",
           "other_unmapped_pct": "0", "peaks_q05": "100", "peaks_in_organelle_like_pct": "0"}
    assert bcd.qc_row(row, tsv)["metrics_source"] == "subsample-2026-10-01"
    assert bcd.qc_row(row, None)["metrics_source"] is None
    assert bcd.qc_row(row, None)["qc_flag"] == "NA"


def test_qc_row_foreign_pct_tolerates_blank_values():
    row = {"library_strategy": "ChIP-Seq", "status": "ok", "layout": "0", "_chipseq_stats": {}}
    base = {"_kind": "chip", "status": "ok", "aligned_pct": "90", "human_pct": "", "other_unmapped_pct": ""}
    assert bcd.qc_row(row, base)["foreign_pct"] is None
    assert bcd.qc_row(row, {**base, "other_unmapped_pct": "4.5"})["foreign_pct"] == 4.5
    assert bcd.qc_row(row, {**base, "human_pct": "n/a", "other_unmapped_pct": "4.5"})["foreign_pct"] == 4.5


def test_build_warns_when_qc_dir_has_no_tsvs(tmp_path, caplog):
    data_root = tmp_path / "data"
    _put_stats(data_root, "chipseq", "SRX9800315", LEGACY_CHIP)
    empty_qc = tmp_path / "qc"
    empty_qc.mkdir()
    with caplog.at_level("WARNING"):
        bcd.build(data_root, tmp_path / "meta", tmp_path / "k.db", qc_dir=empty_qc)
    assert any("QC TSV" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level("WARNING"):
        bcd.build(data_root, tmp_path / "meta", tmp_path / "k2.db")      # no qc_dir: not an error
    assert not any("QC TSV" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: curation overrides
# ---------------------------------------------------------------------------

def test_curation_overrides_apply(tmp_path):
    p = tmp_path / "o.tsv"
    p.write_text("# c\naccession\tfield\tvalue\treason\nSRX1\tantibody_target\tH3\tt\n")
    ov = bcd.load_curation_overrides(p)
    cur = {"antibody_target": "H2A", "tissue": "thallus"}
    assert bcd.apply_curation_overrides(cur, ov["SRX1"]) == {"antibody_target": "H3", "tissue": "thallus"}
    assert cur["antibody_target"] == "H2A"  # input not mutated
    with pytest.raises(ValueError):
        bcd.apply_curation_overrides(cur, {"antibdy": "x"})


def test_repo_overrides_file_is_valid():
    ov = bcd.load_curation_overrides(pathlib.Path(__file__).resolve().parent.parent / "configs/curation_overrides.tsv")
    assert len(ov) == 12
    for acc, fields in ov.items():
        bcd.apply_curation_overrides({}, fields)
