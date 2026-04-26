#!/usr/bin/env python3
"""CLI wrapper (dashes in the name mean this isn't importable; the underscored
module is the real code; this just forwards argv)."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_report import main

if __name__ == "__main__":
    raise SystemExit(main())
