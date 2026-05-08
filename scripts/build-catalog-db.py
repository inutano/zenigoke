"""Thin CLI wrapper for build_catalog_db.

Usage: python3 scripts/build-catalog-db.py [--data-root ...] [--metadata-root ...] [--output ...]
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_catalog_db import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
