"""Thin CLI wrapper for build_catalog_pages — matches project script conventions."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_catalog_pages

if __name__ == "__main__":
    raise SystemExit(build_catalog_pages.main())
