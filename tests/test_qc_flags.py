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


def test_reason_numbers_keep_one_decimal():
    """A value just under a threshold must not read as e.g. '70% < 70%'."""
    reasons = lambda m: qc_flags.chip_flag(m)[1]  # noqa: E731
    assert "alignment rate 69.6% < 70%" in reasons({**CHIP_OK, "aligned_pct": 69.6})
    assert "alignment rate 29.6% < 30%" in reasons({**CHIP_OK, "aligned_pct": 29.6})
    assert "duplication 79.6% ≥ 50%" in reasons({**CHIP_OK, "dup_pct": 79.6})
    assert "duplication 80.0% ≥ 80%" in reasons({**CHIP_OK, "dup_pct": 80})
    assert "organelle reads 20.0%" in reasons({**CHIP_OK, "organelle_pct": 20})
    assert ("non-Marchantia DNA bacterial-like 8.5%, human 1.5%"
            in reasons({**CHIP_OK, "human_pct": 1.5, "bacterial_pct": 8.5}))
    assert ("25.4% of peaks in organelle-like (NUPT/NUMT) regions"
            in reasons({**CHIP_OK, "peaks_in_organelle_like_pct": 25.4}))
    assert "non-Marchantia reads 50.0%" in qc_flags.bs_flag({**BS_OK, "foreign_pct": 50})[1]


def test_chip_organelle_dedup_share_flags_only_without_subsample_value():
    no_sub = {k: v for k, v in CHIP_OK.items() if k != "organelle_pct"}
    flag, reasons, _ = qc_flags.chip_flag({**no_sub, "organelle_dedup_pct": 20})
    assert flag == "WARN"
    assert "organelle share of deduplicated reads 20.0%" in reasons
    assert qc_flags.chip_flag({**no_sub, "organelle_dedup_pct": 19.9})[0] == "PASS"
    # the subsample value (fastp-passed basis) wins whenever it is present
    assert qc_flags.chip_flag({**CHIP_OK, "organelle_pct": 2, "organelle_dedup_pct": 35})[0] == "PASS"
    assert qc_flags.chip_flag({**CHIP_OK, "organelle_pct": 61, "organelle_dedup_pct": 5})[0] == "WARN"
    # the dedup share alone is a metric (not NA)
    assert qc_flags.chip_flag({"status": "ok", "organelle_dedup_pct": 5})[0] == "PASS"


def test_reason_numbers_never_round_onto_a_strict_threshold():
    """One decimal unless rounding would print the threshold itself ("70.0% < 70%")."""
    chip = lambda m: qc_flags.chip_flag({**CHIP_OK, **m})[1]  # noqa: E731
    bs = lambda m: qc_flags.bs_flag({**BS_OK, **m})[1]  # noqa: E731
    assert "alignment rate 69.96% < 70%" in chip({"aligned_pct": 69.96})
    assert "alignment rate 29.96% < 30%" in chip({"aligned_pct": 29.96})
    assert "3.96M usable fragments < 4M" in chip({"usable_fragments": 3.96e6})
    assert "0.999M usable fragments < 1M" in chip({"usable_fragments": 999_000})
    assert "3.1M usable fragments < 4M" in chip({"usable_fragments": 3.14e6})
    assert "only 4.96% of CpGs covered ≥5×" in bs({"frac_cpg_cov_ge5": 0.0496})
    assert "non-conversion 2.004% > 2%" in bs({"nonconv": 0.02004})
    assert "non-conversion 2.1% > 2%" in bs({"nonconv": 0.0207})
