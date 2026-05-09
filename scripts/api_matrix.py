"""GET /api/matrix — 2D sample-count grid for two axes."""
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, HTTPException, Query

from api_axes import _classify_strain, _db

router = APIRouter()

VALID_AXES = {"experiment_type", "genotype_strain", "genotype_class", "developmental_stage"}


def _row_to_axis_value(row: dict, axis: str) -> str | None:
    """Map a flat sample row to its value on the given axis, or None for unknown."""
    if axis == "experiment_type":
        if row["library_strategy"] == "ChIP-Seq":
            ab = row["antibody_target"]
            return f"ChIP:{ab}" if ab else "ChIP:?"
        return row["library_strategy"]
    if axis == "genotype_strain":
        return row["genotype_strain"]
    if axis == "genotype_class":
        cls = _classify_strain(row["genotype_strain"])
        return cls if cls != "unknown" else None
    if axis == "developmental_stage":
        return row["developmental_stage"]
    return None


def build_matrix(x: str, y: str, include_unknown: bool = False) -> dict:
    if x not in VALID_AXES or y not in VALID_AXES:
        raise ValueError(f"unknown axis: {x if x not in VALID_AXES else y}")
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT s.accession, s.library_strategy,
                   c.tissue, c.cell_type, c.developmental_stage,
                   c.genotype_strain, c.treatment, c.antibody_target
            FROM sample s LEFT JOIN sample_curation c USING (accession)
            WHERE s.status = 'ok'
        """).fetchall()
    cells: dict[tuple[str, str], list[str]] = {}
    x_values: list[str] = []
    y_values: list[str] = []
    seen_x: set[str] = set()
    seen_y: set[str] = set()
    for r in rows:
        xv = _row_to_axis_value(r, x)
        yv = _row_to_axis_value(r, y)
        if (xv is None or yv is None) and not include_unknown:
            continue
        xv_key = xv or "(unknown)"
        yv_key = yv or "(unknown)"
        if xv_key not in seen_x:
            seen_x.add(xv_key); x_values.append(xv_key)
        if yv_key not in seen_y:
            seen_y.add(yv_key); y_values.append(yv_key)
        cells.setdefault((xv_key, yv_key), []).append(r["accession"])
    return {
        "x_axis": x, "y_axis": y,
        "x_values": sorted(x_values),
        "y_values": sorted(y_values),
        "cells": [
            {"x": xv, "y": yv, "n": len(accs), "accessions": sorted(accs)}
            for (xv, yv), accs in sorted(cells.items())
        ],
    }


@router.get("/api/matrix")
def matrix_endpoint(
    x: str = Query(...), y: str = Query(...),
    include_unknown: int = Query(0),
) -> dict:
    try:
        return build_matrix(x, y, include_unknown=bool(include_unknown))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
