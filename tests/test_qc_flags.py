from __future__ import annotations
import csv, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import qc_flags  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
CHIP_TSV = REPO / "docs/qc/2026-10-01-chip-atlas-qc.tsv"
BS_TSV = REPO / "docs/qc/2026-10-01-bsseq-qc.tsv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def test_chip_thresholds():
    ok = dict(status="ok", aligned_pct=95, dup_pct=20, usable_fragments=10e6,
              organelle_pct=2, human_pct=0, bacterial_pct=1, peaks_q05=5000,
              peaks_in_organelle_like_pct=2)
    assert qc_flags.chip_flag(ok)[0] == "PASS"
    assert qc_flags.chip_flag({**ok, "aligned_pct": 29.9})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "aligned_pct": 69.9})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "dup_pct": 80})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "usable_fragments": 0.9e6})[0] == "FAIL"
    assert qc_flags.chip_flag({**ok, "human_pct": 6, "bacterial_pct": 4})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "organelle_pct": 20})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_in_organelle_like_pct": 25})[2] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_q05": 0})[2] == "NA"
    assert qc_flags.chip_flag({"status": "failed"})[0] == "FAIL"


def test_bs_thresholds():
    ok = dict(status="ok", mean_cpg=0.4, mean_chg=0.2, mean_chh=0.05, nonconv=0.005,
              foreign_pct=5, frac_cpg_cov_ge5=0.5)
    assert qc_flags.bs_flag(ok)[0] == "PASS"
    assert qc_flags.bs_flag({**ok, "mean_cpg": 0.99, "mean_chg": 0.99, "mean_chh": 0.99})[0] == "FAIL"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.092})[0] == "FAIL"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.028})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "foreign_pct": 85})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "frac_cpg_cov_ge5": 0.014})[0] == "WARN"


def test_flag_with_no_metrics_is_na():
    assert qc_flags.chip_flag({"status": "ok"})[0] == "NA"
    assert qc_flags.bs_flag({"status": "ok"})[0] == "NA"


def test_reproduces_investigation_flags():
    """The module must agree with the 2026-10-01 investigation on every sample."""
    for r in csv.DictReader(CHIP_TSV.open(), delimiter="\t"):
        m = qc_flags.chip_metrics_from_tsv(r)
        assert qc_flags.chip_flag(m)[0] == r["qc_flag"], r["accession"]
        assert qc_flags.chip_flag(m)[2] == r["peak_flag"], r["accession"]
    for r in csv.DictReader(BS_TSV.open(), delimiter="\t"):
        m = qc_flags.bs_metrics_from_tsv(r)
        assert qc_flags.bs_flag(m)[0] == r["qc_flag"], r["accession"]
