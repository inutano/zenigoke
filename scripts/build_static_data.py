"""Pre-compute the JSON files the matrix UI fetches at runtime.

For static-only deployment (S3 + GitHub Pages, no API server), the matrix.js
runtime needs to load `data/axes.json` and a small set of matrix JSONs.
This module generates them at build time, mirroring the live /api/axes and
/api/matrix endpoints.

The same code paths are used by the FastAPI server (api_axes.get_axes,
api_matrix.build_matrix) so behavior stays consistent across modes.
"""
from __future__ import annotations
import json
import pathlib
import sys
from itertools import product

# Allow running from anywhere in the repo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from api_axes import get_axes  # noqa: E402
from api_matrix import build_matrix, VALID_AXES  # noqa: E402


def write_axes(data_dir: pathlib.Path) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = get_axes()
    (data_dir / "axes.json").write_text(json.dumps(payload))
    return 1


def write_matrices(data_dir: pathlib.Path) -> int:
    """Write one matrix JSON per (x, y, include_unknown) triple, skipping x==y.

    With 4 axes and 2 include_unknown values, that's 4*3*2 = 24 files. Each
    file is small (a few KB) because the live catalog has ~157 samples.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for x, y in product(sorted(VALID_AXES), sorted(VALID_AXES)):
        if x == y:
            continue
        for inc in (0, 1):
            payload = build_matrix(x, y, include_unknown=bool(inc))
            fname = f"matrix-{x}-{y}-{inc}.json"
            (data_dir / fname).write_text(json.dumps(payload))
            written += 1
    return written


def build(data_dir: pathlib.Path) -> dict:
    """Generate all static data files. Returns a small summary dict."""
    n_axes = write_axes(data_dir)
    n_matrices = write_matrices(data_dir)
    return {"axes": n_axes, "matrices": n_matrices, "dir": str(data_dir)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir",
                   default=str(pathlib.Path(__file__).resolve().parent.parent / "report" / "data"),
                   help="output directory for the generated JSON files")
    args = p.parse_args(argv)
    summary = build(pathlib.Path(args.data_dir))
    print(f"wrote {summary['axes']} axes file + {summary['matrices']} matrix files to {summary['dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
