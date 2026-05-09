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

    # Mean methylation values — now rendered as percentages (Item 11)
    assert "17.8%" in html   # mean_cpg (0.178 * 100)
    assert "6.2%" in html    # mean_chg (0.062 * 100)
    assert "1.9%" in html    # mean_chh (0.019 * 100)

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


# ---------------------------------------------------------------------------
# Test 4: Nav link depth correctness (Item 1)
# ---------------------------------------------------------------------------

def test_strategy_page_nav_links_use_relative_parent_for_index():
    """Strategy pages must use ../index.html and ../summary.html, not index.html."""
    for slug in ("chipseq", "atacseq", "bsseq"):
        h = bcp.render_strategy(slug, ALL_SAMPLES)
        # Must link up to parent for root-level pages
        assert '../index.html' in h, f"strategy/{slug}.html missing ../index.html"
        assert '../summary.html' in h, f"strategy/{slug}.html missing ../summary.html"
        # Must NOT use root-relative paths that would be broken from strategy/
        assert 'href="index.html"' not in h, f"strategy/{slug}.html has broken href=index.html"
        assert 'href="summary.html"' not in h, f"strategy/{slug}.html has broken href=summary.html"


def test_strategy_page_active_link_is_self_relative():
    """The active strategy link in chipseq.html should be chipseq.html (not strategy/chipseq.html)."""
    h = bcp.render_strategy("chipseq", ALL_SAMPLES)
    # The active link for ChIP-Seq page itself should be chipseq.html (same dir)
    assert 'href="chipseq.html" class="active"' in h or 'href="chipseq.html"' in h


# ---------------------------------------------------------------------------
# Test 5: Failed sample card (Item 7)
# ---------------------------------------------------------------------------

FAILED_SAMPLE: dict = {
    "accession": "SRX_FAIL_999",
    "library_strategy": "ChIP-Seq",
    "status": "failed",
    "layout": "0",
    "fastq_size": None,
    "reads_filtered": None,
    "reads_mapped": None,
    "mapping_rate": None,
    "duplication_rate": None,
    "elapsed_min": None,
    "biosample_accession": "SAMN99999",
    "output_dir": "/fake/chipseq/SRX_FAIL_999",
    "exit_code": "1",
    "log_snippet": (
        "[#1504fb 160MiB/1.1GiB(14%) CN:8 DL:31MiB ETA:30s]\n"
        "[#1504fb 1.1GiB/1.1GiB(100%) CN:0]\n"
        "[E::sam_parse1] SEQ and QUAL are of different length\n"
        "samtools sort: truncated file. Aborting\n"
        "samtools markdup: error reading header\n"
    ),
    # curation
    "tissue": "thallus", "cell_type": None, "developmental_stage": None,
    "genotype_strain": "Tak-1", "treatment": None, "antibody_target": None,
    # chipseq-specific (absent)
    "peaks_q5": None, "peaks_q10": None, "peaks_q20": None,
    "bigwig_path": None, "peaks_q5_path": None, "peaks_q10_path": None, "peaks_q20_path": None,
    # bsseq-specific (absent)
    "mean_cpg": None, "mean_chg": None, "mean_chh": None,
    "cpg_hmr_count": None, "cpg_hypermr_count": None, "cpg_pmd_count": None,
    "chg_hypermr_count": None,
    "cpg_methyl_bw_path": None, "cpg_cover_bw_path": None,
    "cpg_hmr_path": None, "cpg_hypermr_path": None, "cpg_pmd_path": None,
    "chg_methyl_bw_path": None, "chg_cover_bw_path": None,
    "chg_hypermr_path": None, "chh_methyl_bw_path": None, "chh_cover_bw_path": None,
}


def test_render_failed_sample_shows_failure_card_not_pipeline_stats():
    """Failed samples must render the failure card and NOT an empty Pipeline Stats card."""
    h = bcp.render_sample(FAILED_SAMPLE)

    # Failure card must be present
    assert "failed-card" in h or "Did Not Complete" in h or "Did not complete" in h
    assert "exit code 1" in h or "exit_code" in h.lower() or "1" in h

    # Pipeline Stats card must NOT appear for failed samples
    assert "Pipeline Stats" not in h


def test_render_failed_sample_filters_aria2c_progress_lines():
    """Log snippet in failed sample page must not contain aria2c progress lines."""
    h = bcp.render_sample(FAILED_SAMPLE)

    # aria2c lines like [#1504fb ...] must be filtered out
    assert "[#1504fb" not in h
    # But real error lines must remain
    assert "sam_parse1" in h or "truncated" in h or "QUAL" in h


# ---------------------------------------------------------------------------
# Test 6: Layout 0/1 → SE/PE (Item 2)
# ---------------------------------------------------------------------------

def test_layout_translated_to_se_pe():
    """layout=0 renders as SE, layout=1 renders as PE on sample pages."""
    sample_se = dict(CHIP_SAMPLE)
    sample_se["layout"] = "0"
    sample_pe = dict(CHIP_SAMPLE)
    sample_pe["layout"] = "1"

    html_se = bcp.render_sample(sample_se)
    html_pe = bcp.render_sample(sample_pe)

    assert ">SE<" in html_se or "SE</td>" in html_se or ">SE" in html_se
    assert ">PE<" in html_pe or "PE</td>" in html_pe or ">PE" in html_pe
    # Raw numeric values must not appear in the layout row
    assert "<td>0</td>" not in html_se
    assert "<td>1</td>" not in html_pe


# ---------------------------------------------------------------------------
# Test 7: Units rendering (Item 11)
# ---------------------------------------------------------------------------

def test_mapping_rate_rendered_with_percent():
    """mapping_rate must be rendered as 'XX.X%' not raw float."""
    h = bcp.render_sample(CHIP_SAMPLE)
    # Should contain the percentage string
    assert "85.3%" in h


def test_elapsed_min_rendered_with_min_suffix():
    """elapsed_min must be rendered with ' min' suffix."""
    h = bcp.render_sample(CHIP_SAMPLE)
    assert " min" in h


# ---------------------------------------------------------------------------
# Test 8: BS-seq strategy page has methylation columns (Item 10)
# ---------------------------------------------------------------------------

def test_bsseq_strategy_page_has_methylation_columns():
    """BS-seq strategy page must include mean_CpG/mean_CHG/mean_CHH columns."""
    h = bcp.render_strategy("bsseq", ALL_SAMPLES)
    assert "mean_CpG" in h or "mean_cpg" in h.lower()
    assert "mean_CHG" in h or "mean_chg" in h.lower()


def test_atacseq_strategy_page_has_no_antibody_column():
    """ATAC-Seq strategy page must not have an antibody column."""
    h = bcp.render_strategy("atacseq", ALL_SAMPLES)
    assert "antibody" not in h.lower()


# ---------------------------------------------------------------------------
# Test 9: Index page has filter count, chip buttons, and sortable headers
# ---------------------------------------------------------------------------

def test_index_has_filter_count_and_chips():
    """Index page must have #filter-count element and chip buttons."""
    h = bcp.render_index(ALL_SAMPLES)
    assert "filter-count" in h
    assert "chip" in h and "data-filter" in h


def test_index_table_headers_have_data_col():
    """Index table headers must have data-col attributes for JS sorting."""
    h = bcp.render_index(ALL_SAMPLES)
    assert 'data-col=' in h


# ---------------------------------------------------------------------------
# Test 10: ENA SRA link on sample pages (Item 5)
# ---------------------------------------------------------------------------

def test_sample_page_has_ena_sra_link():
    """Sample pages must include a link to ENA browser for the accession."""
    h = bcp.render_sample(CHIP_SAMPLE)
    assert "ebi.ac.uk/ena/browser/view/SRX_CHIP_001" in h
    assert "SRA" in h
