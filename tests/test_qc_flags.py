from __future__ import annotations
import csv, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import qc_flags  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
CHIP_TSV = REPO / "docs/qc/2026-10-01-chip-atlas-qc.tsv"
BS_TSV = REPO / "docs/qc/2026-10-01-bsseq-qc.tsv"


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


CHIP_OK = dict(status="ok", aligned_pct=95, dup_pct=20, usable_fragments=10e6,
               organelle_pct=2, human_pct=0, bacterial_pct=1, peaks_q05=5000,
               peaks_in_organelle_like_pct=2)
BS_OK = dict(status="ok", mean_cpg=0.4, mean_chg=0.2, mean_chh=0.05, nonconv=0.005,
             foreign_pct=5, frac_cpg_cov_ge5=0.5)


def test_chip_threshold_boundaries():
    ok = CHIP_OK
    # alignment: < 30 FAIL, < 70 WARN, otherwise PASS
    assert qc_flags.chip_flag({**ok, "aligned_pct": 30})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "aligned_pct": 70})[0] == "PASS"
    # duplication: >= 50 WARN, >= 80 FAIL
    assert qc_flags.chip_flag({**ok, "dup_pct": 49.9})[0] == "PASS"
    assert qc_flags.chip_flag({**ok, "dup_pct": 50})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "dup_pct": 80})[0] == "FAIL"
    # usable fragments: < 1e6 FAIL, < 4e6 WARN, otherwise PASS
    assert qc_flags.chip_flag({**ok, "usable_fragments": 1e6})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "usable_fragments": 4e6})[0] == "PASS"
    # foreign DNA (human + bacterial) >= 10 WARN; organelle >= 20 WARN
    assert qc_flags.chip_flag({**ok, "human_pct": 5, "bacterial_pct": 4.9})[0] == "PASS"
    assert qc_flags.chip_flag({**ok, "human_pct": 5, "bacterial_pct": 5})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "organelle_pct": 19.9})[0] == "PASS"


def test_chip_peak_flag_boundaries_and_independence():
    ok = CHIP_OK
    # NUPT/NUMT peaks flagged at >= 20% of peaks and >= 50 peaks
    assert qc_flags.chip_flag({**ok, "peaks_q05": 49, "peaks_in_organelle_like_pct": 20})[2] == "OK"
    assert qc_flags.chip_flag({**ok, "peaks_q05": 50, "peaks_in_organelle_like_pct": 20})[2] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_q05": 50, "peaks_in_organelle_like_pct": 19.9})[2] == "OK"
    # peak_flag is reported separately: it never changes qc_flag, but its reason is listed
    flag, reasons, peak_flag = qc_flags.chip_flag({**ok, "peaks_in_organelle_like_pct": 25})
    assert (flag, peak_flag) == ("PASS", "WARN")
    assert any("NUPT/NUMT" in r for r in reasons)
    # ...and it does not mask a real WARN/FAIL
    assert qc_flags.chip_flag({**ok, "peaks_in_organelle_like_pct": 25, "dup_pct": 60})[0] == "WARN"
    assert qc_flags.chip_flag({**ok, "peaks_in_organelle_like_pct": 25, "aligned_pct": 10})[0] == "FAIL"
    # a sample whose only metric is peaks has no QC verdict
    assert qc_flags.chip_flag({"status": "ok", "peaks_q05": 5000, "peaks_in_organelle_like_pct": 25})[0] == "NA"


def test_bs_threshold_boundaries():
    ok = BS_OK
    assert qc_flags.bs_flag({**ok, "nonconv": 0.02})[0] == "PASS"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.0201})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "nonconv": 0.05})[0] == "FAIL"
    assert qc_flags.bs_flag({**ok, "foreign_pct": 49.9})[0] == "PASS"
    assert qc_flags.bs_flag({**ok, "foreign_pct": 50})[0] == "WARN"
    assert qc_flags.bs_flag({**ok, "frac_cpg_cov_ge5": 0.05})[0] == "PASS"
    # wrong-strand signature needs methylation >= 0.9 in ALL three contexts
    assert qc_flags.bs_flag({**ok, "mean_cpg": 0.99, "mean_chg": 0.99, "mean_chh": 0.89})[0] == "PASS"


def test_flag_with_no_metrics_is_na():
    assert qc_flags.chip_flag({"status": "ok"})[0] == "NA"
    assert qc_flags.bs_flag({"status": "ok"})[0] == "NA"


def test_reproduces_investigation_flags():
    """The module must agree with the 2026-10-01 investigation on every sample."""
    n_chip = n_bs = 0
    for r in csv.DictReader(CHIP_TSV.open(), delimiter="\t"):
        n_chip += 1
        m = qc_flags.chip_metrics_from_tsv(r)
        assert qc_flags.chip_flag(m)[0] == r["qc_flag"], r["accession"]
        assert qc_flags.chip_flag(m)[2] == r["peak_flag"], r["accession"]
    for r in csv.DictReader(BS_TSV.open(), delimiter="\t"):
        n_bs += 1
        m = qc_flags.bs_metrics_from_tsv(r)
        assert qc_flags.bs_flag(m)[0] == r["qc_flag"], r["accession"]
    assert n_chip > 0 and n_bs > 0, "regression test iterated no rows"
