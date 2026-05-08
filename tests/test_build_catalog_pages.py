"""Tests for build_catalog_pages.

TDD: these tests are written before the implementation module.

Test coverage:
  1. index_rendering    — tiny in-memory DB with 3 samples; render_index contains
                         the 3 accessions and the filter <input>.
  2. per_sample_rendering — render_sample for a ChIP, ATAC, and BS-seq row;
                            each contains strategy-specific stats.
  3. strategy_rendering  — render_strategy('chipseq', samples) contains only
                           ChIP rows, not ATAC or BS-seq rows.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import build_catalog_pages as bcp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: synthetic sample dicts matching the DB schema (flattened)
# ---------------------------------------------------------------------------

CHIP_SAMPLE: dict = {
    "accession": "SRX_CHIP_001",
    "library_strategy": "ChIP-Seq",
    "status": "ok",
    "layout": "PE",
    "reads_filtered": 10_000_000,
    "mapping_rate": 85.3,
    "duplication_rate": 12.4,
    "elapsed_min": 15.7,
    "biosample_accession": "SAMN00001",
    "output_dir": "/fake/chipseq/SRX_CHIP_001",
    # curation
    "tissue": "thallus",
    "cell_type": None,
    "developmental_stage": "thallus",
    "genotype_strain": "Tak-1",
    "treatment": None,
    "antibody_target": "H3K4me3",
    # chipseq-specific
    "peaks_q5": 2500,
    "peaks_q10": 1800,
    "peaks_q20": 900,
    "bigwig_path": "/fake/chipseq/SRX_CHIP_001/SRX_CHIP_001.bw",
    "peaks_q5_path": "/fake/chipseq/SRX_CHIP_001/SRX_CHIP_001.05_peaks.narrowPeak",
    "peaks_q10_path": "/fake/chipseq/SRX_CHIP_001/SRX_CHIP_001.10_peaks.narrowPeak",
    "peaks_q20_path": "/fake/chipseq/SRX_CHIP_001/SRX_CHIP_001.20_peaks.narrowPeak",
    # bsseq-specific (absent)
    "mean_cpg": None, "mean_chg": None, "mean_chh": None,
    "cpg_hmr_count": None, "cpg_hypermr_count": None, "cpg_pmd_count": None,
    "chg_hypermr_count": None,
    "cpg_methyl_bw_path": None, "cpg_cover_bw_path": None,
    "cpg_hmr_path": None, "cpg_hypermr_path": None, "cpg_pmd_path": None,
    "chg_methyl_bw_path": None, "chg_cover_bw_path": None,
    "chg_hypermr_path": None, "chh_methyl_bw_path": None, "chh_cover_bw_path": None,
}

ATAC_SAMPLE: dict = {
    "accession": "SRX_ATAC_002",
    "library_strategy": "ATAC-Seq",
    "status": "ok",
    "layout": "PE",
    "reads_filtered": 8_000_000,
    "mapping_rate": 72.1,
    "duplication_rate": 30.5,
    "elapsed_min": 8.2,
    "biosample_accession": "SAMN00002",
    "output_dir": "/fake/atacseq/SRX_ATAC_002",
    # curation
    "tissue": "sporophyte",
    "cell_type": None,
    "developmental_stage": "sporophyte",
    "genotype_strain": "Tak-1",
    "treatment": None,
    "antibody_target": None,
    # chipseq-specific (ATAC uses same)
    "peaks_q5": 5000,
    "peaks_q10": 3200,
    "peaks_q20": 1100,
    "bigwig_path": "/fake/atacseq/SRX_ATAC_002/SRX_ATAC_002.bw",
    "peaks_q5_path": "/fake/atacseq/SRX_ATAC_002/SRX_ATAC_002.05_peaks.narrowPeak",
    "peaks_q10_path": None,
    "peaks_q20_path": None,
    # bsseq-specific (absent)
    "mean_cpg": None, "mean_chg": None, "mean_chh": None,
    "cpg_hmr_count": None, "cpg_hypermr_count": None, "cpg_pmd_count": None,
    "chg_hypermr_count": None,
    "cpg_methyl_bw_path": None, "cpg_cover_bw_path": None,
    "cpg_hmr_path": None, "cpg_hypermr_path": None, "cpg_pmd_path": None,
    "chg_methyl_bw_path": None, "chg_cover_bw_path": None,
    "chg_hypermr_path": None, "chh_methyl_bw_path": None, "chh_cover_bw_path": None,
}

BSSEQ_SAMPLE: dict = {
    "accession": "SRX_BSSEQ_003",
    "library_strategy": "Bisulfite-Seq",
    "status": "ok",
    "layout": "PE",
    "reads_filtered": 50_000_000,
    "mapping_rate": 22.5,
    "duplication_rate": None,
    "elapsed_min": 45.0,
    "biosample_accession": "SAMN00003",
    "output_dir": "/fake/bsseq/SRX_BSSEQ_003",
    # curation
    "tissue": "thallus",
    "cell_type": None,
    "developmental_stage": "gametophyte",
    "genotype_strain": "Tak-1",
    "treatment": None,
    "antibody_target": None,
    # chipseq-specific (absent)
    "peaks_q5": None, "peaks_q10": None, "peaks_q20": None,
    "bigwig_path": None, "peaks_q5_path": None,
    "peaks_q10_path": None, "peaks_q20_path": None,
    # bsseq-specific
    "mean_cpg": 0.178,
    "mean_chg": 0.062,
    "mean_chh": 0.019,
    "cpg_hmr_count": 8200,
    "cpg_hypermr_count": 15000,
    "cpg_pmd_count": 1200,
    "chg_hypermr_count": 9800,
    "cpg_methyl_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CpG.methyl.bw",
    "cpg_cover_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CpG.cover.bw",
    "cpg_hmr_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CpG.hmr.bed",
    "cpg_hypermr_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CpG.hypermr.bed",
    "cpg_pmd_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CpG.pmd.bed",
    "chg_methyl_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CHG.methyl.bw",
    "chg_cover_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CHG.cover.bw",
    "chg_hypermr_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CHG.hypermr.bed",
    "chh_methyl_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CHH.methyl.bw",
    "chh_cover_bw_path": "/fake/bsseq/SRX_BSSEQ_003/SRX_BSSEQ_003.CHH.cover.bw",
}

ALL_SAMPLES = [CHIP_SAMPLE, ATAC_SAMPLE, BSSEQ_SAMPLE]


# ---------------------------------------------------------------------------
# Test 1: Index page rendering
# ---------------------------------------------------------------------------

def test_render_index_contains_all_accessions_and_filter_input():
    """render_index must include all 3 accessions and a filter <input>."""
    html = bcp.render_index(ALL_SAMPLES)

    # All 3 accessions appear in the page
    assert "SRX_CHIP_001" in html
    assert "SRX_ATAC_002" in html
    assert "SRX_BSSEQ_003" in html

    # Filter input is present (spec: <input id="q" placeholder="filter…">)
    assert 'id="q"' in html
    assert "<input" in html

    # Must be valid enough HTML: has table rows for data
    assert "<tr" in html
    assert "<th" in html  # header row

    # JS filter handler present
    assert "oninput" in html or "addEventListener" in html


# ---------------------------------------------------------------------------
# Test 2: Per-sample page rendering
# ---------------------------------------------------------------------------

def test_render_sample_chip_contains_peak_counts():
    """ChIP sample page must show peak counts."""
    html = bcp.render_sample(CHIP_SAMPLE)

    assert "SRX_CHIP_001" in html
    assert "ChIP-Seq" in html
    assert "H3K4me3" in html          # antibody_target

    # Peak counts should appear
    assert "2500" in html  # peaks_q5
    assert "1800" in html  # peaks_q10
    assert "900" in html   # peaks_q20

    # Pipeline stats
    assert "85.3" in html   # mapping_rate
    assert "12.4" in html   # duplication_rate


def test_render_sample_bsseq_contains_methylation_stats():
    """BS-seq sample page must show mean methylation values."""
    html = bcp.render_sample(BSSEQ_SAMPLE)

    assert "SRX_BSSEQ_003" in html
    assert "Bisulfite-Seq" in html

    # Mean methylation values
    assert "0.178" in html   # mean_cpg
    assert "0.062" in html   # mean_chg
    assert "0.019" in html   # mean_chh

    # HMR/PMD region counts
    assert "8200" in html    # cpg_hmr_count


def test_render_sample_atac_contains_peak_stats():
    """ATAC sample page shows peaks and mapping_rate."""
    html = bcp.render_sample(ATAC_SAMPLE)

    assert "SRX_ATAC_002" in html
    assert "ATAC-Seq" in html
    assert "5000" in html   # peaks_q5
    assert "72.1" in html   # mapping_rate


# ---------------------------------------------------------------------------
# Test 3: Strategy page rendering
# ---------------------------------------------------------------------------

def test_render_strategy_chipseq_contains_only_chip_rows():
    """render_strategy('chipseq', samples) includes ChIP but not ATAC or BS-seq."""
    html = bcp.render_strategy("chipseq", ALL_SAMPLES)

    # Should contain the ChIP accession
    assert "SRX_CHIP_001" in html

    # Should NOT contain the ATAC or BS-seq accessions
    assert "SRX_ATAC_002" not in html
    assert "SRX_BSSEQ_003" not in html


def test_render_strategy_atacseq_contains_only_atac_rows():
    """render_strategy('atacseq', samples) includes ATAC but not ChIP or BS-seq."""
    html = bcp.render_strategy("atacseq", ALL_SAMPLES)

    assert "SRX_ATAC_002" in html
    assert "SRX_CHIP_001" not in html
    assert "SRX_BSSEQ_003" not in html


def test_render_strategy_bsseq_contains_only_bsseq_rows():
    """render_strategy('bsseq', samples) includes BS-seq but not ChIP or ATAC."""
    html = bcp.render_strategy("bsseq", ALL_SAMPLES)

    assert "SRX_BSSEQ_003" in html
    assert "SRX_CHIP_001" not in html
    assert "SRX_ATAC_002" not in html
