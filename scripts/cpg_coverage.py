"""CpG coverage of BS-seq outputs, for the catalog's "CpGs covered >=5x" QC metric.

For each accession under <output-root>/bsseq/ that has {acc}.CpG.cover.bw:

  frac_cpg_covered  = CpG cytosines with coverage >= 1 / (2 x CG dinucleotides in the FASTA)
  frac_cpg_cov_ge5  = CpG cytosines with coverage >= 5 / (2 x CG dinucleotides in the FASTA)
  median_cpg_cov    = median coverage of the covered CpG cytosines

The denominator counts both strands of every CG in the nuclear FASTA (same basis
as the 2026-10-01 investigation, /data1/zenigoke/tmp/qc-20261001-bs/cpgcov.py).
build_catalog_db.py reads the output (--cpgcov-tsv) for zenigoke-v2 BS rows.

pyBigWig is not a build dependency; it is imported only when a bigWig is read:

  PYTHONPATH=/data1/zenigoke/tmp/qc-20261001-bs/pylib python3 scripts/cpg_coverage.py
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = "/data1/zenigoke/output"
DEFAULT_FASTA = "/data1/zenigoke/references/MpTak_v7.1/MpTak_v7.1.fa"
DEFAULT_OUT = REPO_ROOT / "docs" / "qc" / "bsseq-v2-cpgcov.tsv"
COLUMNS = ["accession", "frac_cpg_covered", "frac_cpg_cov_ge5", "median_cpg_cov"]


def count_cg_dinucleotides(fasta: pathlib.Path) -> int:
    """Number of CG dinucleotides, counted within each record (case-insensitive)."""
    n = 0
    seq: List[str] = []
    with open(fasta) as fh:
        for line in fh:
            if line.startswith(">"):
                n += "".join(seq).upper().count("CG")
                seq = []
            else:
                seq.append(line.strip())
    return n + "".join(seq).upper().count("CG")


def read_cover_intervals(bw_path: pathlib.Path) -> Iterator[Tuple[int, float]]:
    """Yield (bases, coverage) for every interval of a CpG cover bigWig."""
    try:
        import pyBigWig  # noqa: WPS433 (optional dependency)
    except ImportError as e:
        raise RuntimeError(
            "pyBigWig is required to read bigWig files; run with "
            "PYTHONPATH=/data1/zenigoke/tmp/qc-20261001-bs/pylib (or pip install pyBigWig)") from e
    bw = pyBigWig.open(str(bw_path))
    try:
        for chrom in bw.chroms():
            for start, end, value in bw.intervals(chrom) or ():
                yield end - start, value
    finally:
        bw.close()


def coverage_summary(intervals: Iterable[Tuple[int, float]], n_cpg_cytosines: int) -> Dict[str, Optional[float]]:
    """Fractions of CpG cytosines covered >= 1x and >= 5x, and the median coverage of covered ones."""
    if n_cpg_cytosines <= 0:
        raise ValueError("n_cpg_cytosines must be positive")
    hist: Dict[float, int] = {}
    for bases, cov in intervals:
        if cov > 0 and bases > 0:
            hist[cov] = hist.get(cov, 0) + bases
    covered = sum(hist.values())
    ge5 = sum(n for cov, n in hist.items() if cov >= 5)
    return {
        "frac_cpg_covered": covered / n_cpg_cytosines,
        "frac_cpg_cov_ge5": ge5 / n_cpg_cytosines,
        "median_cpg_cov": _median(hist, covered),
    }


def _median(hist: Dict[float, int], total: int) -> Optional[float]:
    if total == 0:
        return None
    lo_rank, hi_rank = (total - 1) // 2, total // 2       # 0-based ranks of the middle value(s)
    seen, lo = 0, None
    for cov in sorted(hist):
        seen += hist[cov]
        if lo is None and seen > lo_rank:
            lo = cov
        if seen > hi_rank:
            return (lo + cov) / 2
    return None  # unreachable


def summarize_outputs(output_root: pathlib.Path, n_cpg_cytosines: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    bs_dir = output_root / "bsseq"
    if not bs_dir.is_dir():
        raise FileNotFoundError(f"{bs_dir} does not exist")
    for acc_dir in sorted(p for p in bs_dir.iterdir() if p.is_dir()):
        acc = acc_dir.name
        bw = acc_dir / f"{acc}.CpG.cover.bw"
        if not bw.exists():
            print(f"  skip {acc}: no {bw.name}", file=sys.stderr)
            continue
        s = coverage_summary(read_cover_intervals(bw), n_cpg_cytosines)
        rows.append({"accession": acc, **s})
        print(f"  {acc}: >=5x {s['frac_cpg_cov_ge5']:.4f}", file=sys.stderr)
    return rows


def write_tsv(rows: List[Dict[str, object]], out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for r in rows:
            med = r["median_cpg_cov"]
            w.writerow([r["accession"], f"{r['frac_cpg_covered']:.4f}", f"{r['frac_cpg_cov_ge5']:.4f}",
                        "" if med is None else f"{med:g}"])
    tmp.replace(out)


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT,
                   help=f"pipeline output root containing bsseq/ (default: {DEFAULT_OUTPUT_ROOT})")
    p.add_argument("--fasta", default=DEFAULT_FASTA,
                   help=f"nuclear reference FASTA for the CpG count (default: {DEFAULT_FASTA})")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output TSV (default: <repo>/docs/qc/bsseq-v2-cpgcov.tsv)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _arg_parser().parse_args(argv)
    n_cg = count_cg_dinucleotides(pathlib.Path(args.fasta))
    print(f"{args.fasta}: {n_cg} CG dinucleotides = {2 * n_cg} CpG cytosines (both strands)", file=sys.stderr)
    rows = summarize_outputs(pathlib.Path(args.output_root), 2 * n_cg)
    write_tsv(rows, pathlib.Path(args.out))
    print(f"wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
