"""GET /api/axes — list selectable matrix axes with values and counts.

Reads from $ZENIGOKE_DB_PATH (sqlite, read-only). Defines four axes:
  - experiment_type    (library_strategy, with antibody_target appended for ChIP)
  - genotype_strain    (raw strain name from curation)
  - genotype_class     (derived: wildtype / mutant / overexpression / unknown)
  - developmental_stage (raw stage from curation)
"""
from __future__ import annotations
import os
import sqlite3
from fastapi import APIRouter

router = APIRouter()


def _db() -> sqlite3.Connection:
    path = os.getenv("ZENIGOKE_DB_PATH", "db/kknmsmd.db")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _experiment_type_values(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT
          CASE WHEN s.library_strategy = 'ChIP-Seq'
               THEN 'ChIP:' || COALESCE(c.antibody_target, '?')
               ELSE s.library_strategy
          END AS et,
          COUNT(*) AS n
        FROM sample s LEFT JOIN sample_curation c USING (accession)
        GROUP BY et
        ORDER BY n DESC, et ASC
    """).fetchall()
    return [{"value": r[0], "n": r[1]} for r in rows]


def _strain_values(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT genotype_strain, COUNT(*) AS n
        FROM sample_curation
        WHERE genotype_strain IS NOT NULL
        GROUP BY genotype_strain
        ORDER BY n DESC, genotype_strain ASC
    """).fetchall()
    return [{"value": r[0], "n": r[1]} for r in rows]


def _classify_strain(strain: str | None) -> str:
    if strain is None:
        return "unknown"
    s = strain.lower()
    wt_markers = ("tak-1", "tak-2", "wt", "wild type", "tak1/tak2", "tak-1_bc")
    if any(m == s or s.startswith(m) for m in wt_markers):
        return "wildtype"
    if "overexpression" in s or "tagrfp" in s:
        return "overexpression"
    return "mutant"


def _genotype_class_values(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT genotype_strain FROM sample_curation").fetchall()
    counts: dict[str, int] = {}
    for (strain,) in rows:
        cls = _classify_strain(strain)
        counts[cls] = counts.get(cls, 0) + 1
    order = ["wildtype", "mutant", "overexpression", "unknown"]
    return [{"value": k, "n": counts[k]} for k in order if k in counts]


def _stage_values(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT developmental_stage, COUNT(*) AS n
        FROM sample_curation
        WHERE developmental_stage IS NOT NULL
        GROUP BY developmental_stage
        ORDER BY n DESC, developmental_stage ASC
    """).fetchall()
    return [{"value": r[0], "n": r[1]} for r in rows]


def get_axes() -> dict:
    with _db() as conn:
        return {
            "axes": [
                {"key": "experiment_type",    "label": "Experiment type",
                 "values": _experiment_type_values(conn)},
                {"key": "genotype_strain",    "label": "Genotype / strain",
                 "values": _strain_values(conn)},
                {"key": "genotype_class",     "label": "Genotype class",
                 "values": _genotype_class_values(conn)},
                {"key": "developmental_stage","label": "Developmental stage",
                 "values": _stage_values(conn)},
            ]
        }


@router.get("/api/axes")
def axes_endpoint() -> dict:
    return get_axes()
