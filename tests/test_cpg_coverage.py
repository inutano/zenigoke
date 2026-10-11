"""Tests for scripts/cpg_coverage.py (bigWig reading is mocked: no pyBigWig needed)."""
from __future__ import annotations

import csv
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import cpg_coverage  # noqa: E402


def test_count_cg_dinucleotides_per_record(tmp_path):
    fa = tmp_path / "g.fa"
    # 'CG' split across a line break counts; lowercase counts; a CG spanning two records does not.
    fa.write_text(">a\nACGt\ncgAC\n>b\nGACG\n")
    assert cpg_coverage.count_cg_dinucleotides(fa) == 3


def test_coverage_summary_arithmetic():
    # (bases, coverage): one site at 1x, one at 5x, two adjacent sites at 7x, one uncovered entry
    intervals = [(1, 1.0), (1, 5.0), (2, 7.0), (1, 0.0)]
    s = cpg_coverage.coverage_summary(intervals, n_cpg_cytosines=10)
    assert s["frac_cpg_covered"] == pytest.approx(0.4)     # 4 covered of 10
    assert s["frac_cpg_cov_ge5"] == pytest.approx(0.3)     # 3 at >= 5x
    assert s["median_cpg_cov"] == pytest.approx(6.0)       # median of 1, 5, 7, 7


def test_coverage_summary_empty():
    s = cpg_coverage.coverage_summary([], n_cpg_cytosines=10)
    assert s == {"frac_cpg_covered": 0.0, "frac_cpg_cov_ge5": 0.0, "median_cpg_cov": None}
    with pytest.raises(ValueError):
        cpg_coverage.coverage_summary([(1, 1.0)], n_cpg_cytosines=0)


def test_main_writes_tsv_for_each_bs_accession(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    for acc in ("SRX1", "SRX2"):
        d = out_root / "bsseq" / acc
        d.mkdir(parents=True)
        (d / f"{acc}.CpG.cover.bw").write_bytes(b"")
    (out_root / "bsseq" / "SRX3").mkdir()                   # no cover bigWig: skipped
    fa = tmp_path / "g.fa"
    fa.write_text(">chr1\nCGCGCGCGCG\n")                    # 5 CG -> 10 CpG cytosines
    fake = {"SRX1": [(1, 1.0), (1, 5.0), (2, 7.0)], "SRX2": [(10, 2.0)]}
    monkeypatch.setattr(cpg_coverage, "read_cover_intervals",
                        lambda p: iter(fake[pathlib.Path(p).name.split(".")[0]]))
    out = tmp_path / "cov.tsv"
    assert cpg_coverage.main(["--output-root", str(out_root), "--fasta", str(fa), "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(), delimiter="\t"))
    assert list(rows[0]) == ["accession", "frac_cpg_covered", "frac_cpg_cov_ge5", "median_cpg_cov"]
    assert [r["accession"] for r in rows] == ["SRX1", "SRX2"]
    assert float(rows[0]["frac_cpg_cov_ge5"]) == pytest.approx(0.3)
    assert float(rows[1]["frac_cpg_covered"]) == pytest.approx(1.0)
    assert float(rows[1]["frac_cpg_cov_ge5"]) == 0.0
    assert float(rows[1]["median_cpg_cov"]) == 2.0


def test_missing_pybigwig_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pyBigWig", None)      # makes `import pyBigWig` fail
    with pytest.raises(RuntimeError, match="pyBigWig"):
        list(cpg_coverage.read_cover_intervals(tmp_path / "x.bw"))


def test_default_out_matches_build_default():
    import build_catalog_db as bcd
    assert pathlib.Path(cpg_coverage._arg_parser().parse_args([]).out) == \
        pathlib.Path(bcd._arg_parser().parse_args([]).cpgcov_tsv)
