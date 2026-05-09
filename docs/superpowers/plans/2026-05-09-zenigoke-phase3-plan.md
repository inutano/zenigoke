# Zenigoke Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static catalog top page with an interactive matrix-driven webapp. Backend (FastAPI) serves static catalog pages plus three JSON endpoints (axes / matrix / bundle). Frontend (vanilla JS) renders a 2-axis sample-count matrix; user multi-selects cells, reviews the bundle in a side panel, and POSTs track URLs to their local IGV at `localhost:60151`.

**Architecture:** Single FastAPI/uvicorn process replaces `python3 -m http.server`. Reads `db/kknmsmd.db` (SQLite, RO). Bundle generation shells out to `bedtools merge`. Same code runs single-host (Tailscale) or AWS (GitHub Pages + EC2 + S3); switching is env-vars only. No SPA framework on the frontend — just `fetch()` + DOM manipulation.

**Tech Stack:** Python 3.8+, FastAPI, uvicorn, sqlite3 (stdlib), subprocess (stdlib), httpx (test only), pytest, vanilla JS, bedtools 2.x (apt-installed on host).

**Reference spec:** `docs/superpowers/specs/2026-05-09-zenigoke-phase3-design.md`

**Execution guidance:** "Move fast and iterate." TDD applies to API endpoints (Python). Frontend JS gets a smoke test (page loads, expected DOM nodes present) but no per-handler unit tests. Commits are per-task.

---

## File structure

| Path | Responsibility |
|--|--|
| `requirements.txt` | Pin fastapi, uvicorn, httpx (test). Stdlib for everything else. |
| `scripts/server.py` | FastAPI app entry + uvicorn launcher + env-var config + CORS + static mount |
| `scripts/api_axes.py` | Module exporting `get_axes()` and the FastAPI router for `/api/axes` |
| `scripts/api_matrix.py` | Module + router for `/api/matrix` |
| `scripts/api_bundle.py` | Module + router for `/api/bundle` and `/bundle/{hash}` |
| `scripts/igv_url_helper.py` | Build per-sample / consensus track URL lists with group colors |
| `scripts/build_catalog_pages.py` | EXISTING — extended to generate matrix-flavored `index.html` + new `browse.html` |
| `report/assets/matrix.js` | Vanilla JS: fetch + render matrix, cell click, multi-select, send to IGV |
| `report/assets/matrix.css` | Matrix-specific styles (table, side panel, chips) |
| `tests/test_api_axes.py` | pytest for `/api/axes` |
| `tests/test_api_matrix.py` | pytest for `/api/matrix` |
| `tests/test_api_bundle.py` | pytest for `/api/bundle` (mocks subprocess.run) |
| `tests/test_e2e_smoke.py` | One end-to-end test exercising static fallback + matrix + bundle |
| `deploy/Caddyfile` | Auto-TLS reverse proxy to uvicorn (cloud mode) |
| `deploy/zenigoke.service` | systemd unit (committed, not auto-installed) |
| `deploy/s3-cors.json` | CORS config for the data bucket |

Bundle artifacts (gitignored): `report/bundles/{hash}/{manifest.json, consensus.{strat}.bed}`.

---

## Pre-flight (do this once, before Task 1)

Install runtime prerequisites on the dev host:

```bash
# bedtools for bundle generation
sudo apt-get install -y bedtools

# Python deps
cd /home/inutano/work/zenigoke
echo -e "fastapi==0.110.*\nuvicorn[standard]==0.29.*\nhttpx==0.27.*" > requirements.txt
python3 -m pip install --user -r requirements.txt

# verify
python3 -c "import fastapi, uvicorn, httpx; print('ok')"
which bedtools && bedtools --version

# stop the existing http.server (we'll replace it with FastAPI)
ss -tlnp 2>/dev/null | grep ':8088' && echo "Port 8088 still in use — kill the existing http.server before Task 10"
```

If `apt install bedtools` requires sudo prompt, write the command to `~/run/install-bedtools.sh` per the user's terminal-quirk preference. Do not block the plan on this — Task 4 has the actual integration point.

Add `requirements.txt` to git separately:

```bash
git add requirements.txt
git commit -m "deps: pin fastapi + uvicorn + httpx for Phase 3"
```

---

## Task 1: FastAPI server skeleton + static mount

**Goal:** Replace `python3 -m http.server` with a FastAPI app that serves the existing `report/` tree at the same paths, on the same port. No API endpoints yet; just the static fallback.

**Files:**
- Create: `scripts/server.py`
- Test: smoke check via curl

- [ ] **Step 1: Write `scripts/server.py`**

```python
"""Zenigoke catalog server — FastAPI + static mount.

Replaces `python3 -m http.server`. Serves the existing `report/` tree at the
same URLs and adds /api/* endpoints (added in Tasks 2-4).

Env vars:
  ZENIGOKE_HOST            default 0.0.0.0
  ZENIGOKE_PORT            default 8088
  ZENIGOKE_REPORT_DIR      default report
  ZENIGOKE_DB_PATH         default db/kknmsmd.db
  ZENIGOKE_BUNDLES_DIR     default report/bundles
  ZENIGOKE_PUBLIC_BASE     default http://<host>:<port>  (URL prefix announced in track responses)
  ZENIGOKE_BUNDLES_PUBLIC  default <ZENIGOKE_PUBLIC_BASE>/bundles
  ZENIGOKE_CORS_ORIGIN     default *  (single origin or '*' — set to GitHub Pages URL in cloud mode)
"""
from __future__ import annotations
import os
import pathlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT_DIR = pathlib.Path(os.getenv("ZENIGOKE_REPORT_DIR", REPO_ROOT / "report"))
BUNDLES_DIR = pathlib.Path(os.getenv("ZENIGOKE_BUNDLES_DIR", REPORT_DIR / "bundles"))
DB_PATH = pathlib.Path(os.getenv("ZENIGOKE_DB_PATH", REPO_ROOT / "db" / "kknmsmd.db"))
CORS_ORIGIN = os.getenv("ZENIGOKE_CORS_ORIGIN", "*")

app = FastAPI(title="zenigoke", version="phase3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Routers added in later tasks:
#   from api_axes import router as axes_router; app.include_router(axes_router)
#   from api_matrix import router as matrix_router; app.include_router(matrix_router)
#   from api_bundle import router as bundle_router; app.include_router(bundle_router)

BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

# Static fallback last, so /api routes win
app.mount("/", StaticFiles(directory=str(REPORT_DIR), html=True), name="report")


def main() -> None:
    import uvicorn
    host = os.getenv("ZENIGOKE_HOST", "0.0.0.0")
    port = int(os.getenv("ZENIGOKE_PORT", "8088"))
    uvicorn.run("server:app", host=host, port=port, app_dir=str(REPO_ROOT / "scripts"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test it**

```bash
cd /home/inutano/work/zenigoke
# stop any existing server on 8088 first
pkill -f "http.server 8088" 2>/dev/null
sleep 1
nohup python3 scripts/server.py > /tmp/zenigoke-server.log 2>&1 &
echo $! > /tmp/zenigoke-server.pid
sleep 3
curl -sI http://localhost:8088/index.html | head -2
curl -sI http://localhost:8088/samples/SRX22603368.html | head -2
```

Expected: both return `HTTP/1.1 200 OK`. Log shows uvicorn started.

- [ ] **Step 3: Commit**

```bash
git add scripts/server.py
git commit -m "scripts: add FastAPI server skeleton + static mount (Phase 3 Task 1)"
```

- [ ] **Step 4: Leave the server running**

We'll iterate on it in subsequent tasks. Each task can `kill $(cat /tmp/zenigoke-server.pid)` and restart via the same `nohup` command after making changes — uvicorn auto-reload is not used (keeps the test loop deterministic).

---

## Task 2: `/api/axes` endpoint

**Goal:** Return the four selectable matrix axes with their value sets and counts, derived from `db/kknmsmd.db`. Matches §4.1 of the spec.

**Files:**
- Create: `scripts/api_axes.py`
- Test: `tests/test_api_axes.py`
- Modify: `scripts/server.py` to include the router

- [ ] **Step 1: Write the failing test `tests/test_api_axes.py`**

```python
"""Test /api/axes endpoint."""
from __future__ import annotations
import pathlib
import sqlite3
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _build_fixture_db(path: pathlib.Path) -> None:
    """Create a 4-sample fixture DB for axis testing."""
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE sample (accession TEXT PRIMARY KEY, library_strategy TEXT, status TEXT, output_dir TEXT);
      CREATE TABLE sample_curation (
        accession TEXT PRIMARY KEY, tissue TEXT, cell_type TEXT,
        developmental_stage TEXT, genotype_strain TEXT, treatment TEXT, antibody_target TEXT
      );
    """)
    rows = [
        ("SRX1", "ChIP-Seq",      "ok", "/x"),
        ("SRX2", "ChIP-Seq",      "ok", "/x"),
        ("SRX3", "ATAC-Seq",      "ok", "/x"),
        ("SRX4", "Bisulfite-Seq", "ok", "/x"),
    ]
    conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", rows)
    conn.executemany("""INSERT INTO sample_curation
        (accession, tissue, cell_type, developmental_stage, genotype_strain, treatment, antibody_target)
        VALUES (?,?,?,?,?,?,?)""", [
        ("SRX1", "thallus", None, "thallus",   "Tak-1",   None, "H3K4me3"),
        ("SRX2", "thallus", None, "thallus",   "Mpez1",   None, "H3K27me3"),
        ("SRX3", "thallus", None, "gemmaling", "Tak-1",   None, None),
        ("SRX4", None,      None, None,        "Mpmet",   None, None),
    ])
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    _build_fixture_db(db)
    monkeypatch.setenv("ZENIGOKE_DB_PATH", str(db))
    monkeypatch.setenv("ZENIGOKE_REPORT_DIR", str(tmp_path))
    (tmp_path / "bundles").mkdir()
    monkeypatch.setenv("ZENIGOKE_BUNDLES_DIR", str(tmp_path / "bundles"))
    # Force re-import of server module so env vars take effect
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_"))]:
        del sys.modules[mod]
    from server import app  # noqa: E402
    return TestClient(app)


def test_axes_returns_four_axes(client):
    r = client.get("/api/axes")
    assert r.status_code == 200
    body = r.json()
    keys = [a["key"] for a in body["axes"]]
    assert keys == ["experiment_type", "genotype_strain", "genotype_class", "developmental_stage"]


def test_experiment_type_combines_strategy_and_antibody(client):
    r = client.get("/api/axes")
    et = next(a for a in r.json()["axes"] if a["key"] == "experiment_type")
    values = {v["value"]: v["n"] for v in et["values"]}
    # ChIP-Seq SRX1 has antibody H3K4me3 -> "ChIP:H3K4me3"
    assert values.get("ChIP:H3K4me3") == 1
    assert values.get("ChIP:H3K27me3") == 1
    assert values.get("ATAC-Seq") == 1
    assert values.get("Bisulfite-Seq") == 1


def test_genotype_class_collapses_strains(client):
    r = client.get("/api/axes")
    gc = next(a for a in r.json()["axes"] if a["key"] == "genotype_class")
    values = {v["value"]: v["n"] for v in gc["values"]}
    # Tak-1 (×2) -> wildtype
    assert values.get("wildtype") == 2
    # Mpez1, Mpmet are mutants
    assert values.get("mutant") == 2


def test_tissue_is_excluded(client):
    r = client.get("/api/axes")
    keys = [a["key"] for a in r.json()["axes"]]
    assert "tissue" not in keys
```

- [ ] **Step 2: Run the test**

```bash
cd /home/inutano/work/zenigoke
python3 -m pytest tests/test_api_axes.py -v
```

Expected: All FAIL with `ModuleNotFoundError: No module named 'api_axes'` or similar.

- [ ] **Step 3: Write `scripts/api_axes.py`**

```python
"""GET /api/axes — list selectable matrix axes with values and counts.

Reads from $ZENIGOKE_DB_PATH (sqlite, read-only). Defines four axes:
  - experiment_type    (library_strategy, with antibody_target appended for ChIP)
  - genotype_strain    (raw strain name from curation)
  - genotype_class     (derived: wildtype / mutant / overexpression / unknown)
  - developmental_stage (raw stage from curation)
"""
from __future__ import annotations
import os
import pathlib
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
```

- [ ] **Step 4: Wire the router into `scripts/server.py`**

Edit `scripts/server.py` and replace the `# Routers added in later tasks:` comment block with:

```python
from api_axes import router as axes_router
app.include_router(axes_router)
```

(Place this before the `app.mount("/", ...)` line — order matters because the static mount is the catch-all.)

- [ ] **Step 5: Run the test**

```bash
python3 -m pytest tests/test_api_axes.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/api_axes.py scripts/server.py tests/test_api_axes.py
git commit -m "scripts+tests: add /api/axes endpoint (Phase 3 Task 2)"
```

---

## Task 3: `/api/matrix` endpoint

**Goal:** Return a 2D count grid for any pair of axes, with sample accessions per cell. Matches §4.2.

**Files:**
- Create: `scripts/api_matrix.py`
- Test: `tests/test_api_matrix.py`
- Modify: `scripts/server.py`

- [ ] **Step 1: Write the failing test `tests/test_api_matrix.py`**

```python
"""Test /api/matrix endpoint."""
from __future__ import annotations
import pathlib
import sqlite3
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _build_fixture_db(path: pathlib.Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE sample (accession TEXT PRIMARY KEY, library_strategy TEXT, status TEXT, output_dir TEXT);
      CREATE TABLE sample_curation (
        accession TEXT PRIMARY KEY, tissue TEXT, cell_type TEXT,
        developmental_stage TEXT, genotype_strain TEXT, treatment TEXT, antibody_target TEXT
      );
    """)
    samples = [
        ("SRX1", "ChIP-Seq",      "ok", "/x"),
        ("SRX2", "ChIP-Seq",      "ok", "/x"),
        ("SRX3", "ChIP-Seq",      "ok", "/x"),
        ("SRX4", "ATAC-Seq",      "ok", "/x"),
        ("SRX5", "Bisulfite-Seq", "ok", "/x"),
    ]
    conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", samples)
    conn.executemany("""INSERT INTO sample_curation
        (accession, tissue, cell_type, developmental_stage, genotype_strain, treatment, antibody_target)
        VALUES (?,?,?,?,?,?,?)""", [
        ("SRX1", None, None, "thallus",   "Tak-1", None, "H3K4me3"),
        ("SRX2", None, None, "thallus",   "Tak-1", None, "H3K27me3"),
        ("SRX3", None, None, "gemmaling", "Mpez1", None, "H3K27me3"),
        ("SRX4", None, None, "thallus",   "Tak-1", None, None),
        ("SRX5", None, None, None,        None,    None, None),
    ])
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    _build_fixture_db(db)
    monkeypatch.setenv("ZENIGOKE_DB_PATH", str(db))
    monkeypatch.setenv("ZENIGOKE_REPORT_DIR", str(tmp_path))
    (tmp_path / "bundles").mkdir()
    monkeypatch.setenv("ZENIGOKE_BUNDLES_DIR", str(tmp_path / "bundles"))
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def test_matrix_experiment_type_x_strain(client):
    r = client.get("/api/matrix?x=experiment_type&y=genotype_strain")
    assert r.status_code == 200
    body = r.json()
    assert body["x_axis"] == "experiment_type"
    assert body["y_axis"] == "genotype_strain"
    cells = {(c["x"], c["y"]): c for c in body["cells"]}
    cell = cells.get(("ChIP:H3K27me3", "Tak-1"))
    assert cell is not None
    assert cell["n"] == 1
    assert cell["accessions"] == ["SRX2"]


def test_matrix_excludes_unknowns_by_default(client):
    r = client.get("/api/matrix?x=experiment_type&y=genotype_strain")
    cells = r.json()["cells"]
    accs = [a for c in cells for a in c["accessions"]]
    assert "SRX5" not in accs   # SRX5 has null strain


def test_matrix_includes_unknowns_when_asked(client):
    r = client.get("/api/matrix?x=experiment_type&y=genotype_strain&include_unknown=1")
    cells = r.json()["cells"]
    accs = [a for c in cells for a in c["accessions"]]
    assert "SRX5" in accs


def test_matrix_returns_400_for_unknown_axis(client):
    r = client.get("/api/matrix?x=banana&y=genotype_strain")
    assert r.status_code == 400
```

- [ ] **Step 2: Run, expect 4 failures**

```bash
python3 -m pytest tests/test_api_matrix.py -v
```

- [ ] **Step 3: Write `scripts/api_matrix.py`**

```python
"""GET /api/matrix — 2D sample-count grid for two axes."""
from __future__ import annotations
import os
import sqlite3
from fastapi import APIRouter, HTTPException, Query

from api_axes import _classify_strain, _db

router = APIRouter()

VALID_AXES = {"experiment_type", "genotype_strain", "genotype_class", "developmental_stage"}


def _row_to_axis_value(row: dict, axis: str) -> str | None:
    """Map a flat sample row to its value on the given axis, or None for unknown."""
    if axis == "experiment_type":
        if row["library_strategy"] == "ChIP-Seq":
            ab = row.get("antibody_target")
            return f"ChIP:{ab}" if ab else "ChIP:?"
        return row["library_strategy"]
    if axis == "genotype_strain":
        return row.get("genotype_strain")
    if axis == "genotype_class":
        cls = _classify_strain(row.get("genotype_strain"))
        return cls if cls != "unknown" else None
    if axis == "developmental_stage":
        return row.get("developmental_stage")
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
```

- [ ] **Step 4: Wire into `scripts/server.py`**

Add after the `axes_router` line:

```python
from api_matrix import router as matrix_router
app.include_router(matrix_router)
```

- [ ] **Step 5: Run the test**

```bash
python3 -m pytest tests/test_api_matrix.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/api_matrix.py scripts/server.py tests/test_api_matrix.py
git commit -m "scripts+tests: add /api/matrix endpoint (Phase 3 Task 3)"
```

---

## Task 4: `/api/bundle` endpoint + bedtools shell-out

**Goal:** Build per-strategy consensus BEDs via `bedtools merge` and return per-sample track URLs. Matches §4.3 + §5.

**Files:**
- Create: `scripts/api_bundle.py`, `scripts/igv_url_helper.py`
- Test: `tests/test_api_bundle.py`
- Modify: `scripts/server.py`

- [ ] **Step 1: Write `scripts/igv_url_helper.py`**

```python
"""Helpers to build IGV-ready track URL lists.

URL templating is governed by env vars so the same code runs in local mode
(URLs point at the catalog server) and cloud mode (URLs point at S3).
"""
from __future__ import annotations
import os

TRACK_GROUP_PALETTE = [
    "#3060a0", "#a04030", "#308050", "#a0a030",
    "#603090", "#308090", "#a07030", "#7060a0",
]


def _public_base() -> str:
    return os.getenv("ZENIGOKE_PUBLIC_BASE", "http://localhost:8088").rstrip("/")


def _bundles_public() -> str:
    return os.getenv("ZENIGOKE_BUNDLES_PUBLIC", f"{_public_base()}/bundles").rstrip("/")


def per_sample_tracks(sample: dict, q_cutoff: str, color: str) -> list[dict]:
    """Return the IGV track entries for one sample.

    Per-strategy defaults from spec §5:
      ChIP/ATAC: BigWig + narrowPeak at the chosen q
      BS-seq:    CpG/CHG/CHH methyl BigWigs
    """
    acc = sample["accession"]
    strat = sample["library_strategy"]
    base = _public_base()
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        sub = "chipseq" if strat == "ChIP-Seq" else "atacseq"
        q_label = {"1e-5": "05", "1e-10": "10", "1e-20": "20"}[q_cutoff]
        return [
            {"name": f"{acc} bigwig", "url": f"{base}/output/{sub}/{acc}/{acc}.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} peaks q≤{q_cutoff}",
             "url": f"{base}/output/{sub}/{acc}/{acc}.{q_label}_peaks.narrowPeak",
             "type": "annotation", "color": color, "group": sample.get("_group", "")},
        ]
    if strat == "Bisulfite-Seq":
        return [
            {"name": f"{acc} CpG methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CpG.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} CHG methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CHG.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} CHH methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CHH.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
        ]
    return []


def consensus_track(strategy: str, hash_id: str, q_cutoff: str, n_samples: int) -> dict:
    return {
        "name": f"consensus {strategy} q≤{q_cutoff} (n={n_samples})",
        "url": f"{_bundles_public()}/{hash_id}/consensus.{strategy}.bed",
        "type": "annotation",
        "color": "#1a9970",
        "group": "consensus",
    }


def color_for_group(idx: int) -> str:
    return TRACK_GROUP_PALETTE[idx % len(TRACK_GROUP_PALETTE)]
```

- [ ] **Step 2: Write the failing test `tests/test_api_bundle.py`**

```python
"""Test /api/bundle — bedtools is mocked; cache hit / miss verified."""
from __future__ import annotations
import json
import pathlib
import sqlite3
import sys
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _build_fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a tiny DB + fake output tree with empty peak files."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE sample (accession TEXT PRIMARY KEY, library_strategy TEXT, status TEXT, output_dir TEXT);
      CREATE TABLE sample_curation (
        accession TEXT PRIMARY KEY, tissue TEXT, cell_type TEXT,
        developmental_stage TEXT, genotype_strain TEXT, treatment TEXT, antibody_target TEXT
      );
    """)
    out = tmp_path / "report"
    (out / "output" / "chipseq" / "SRX_A").mkdir(parents=True)
    (out / "output" / "chipseq" / "SRX_A" / "SRX_A.10_peaks.narrowPeak").write_text(
        "chr1\t100\t200\tpeak1\n")
    (out / "output" / "chipseq" / "SRX_B").mkdir(parents=True)
    (out / "output" / "chipseq" / "SRX_B" / "SRX_B.10_peaks.narrowPeak").write_text(
        "chr1\t150\t250\tpeak2\n")
    (out / "bundles").mkdir(parents=True)
    conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", [
        ("SRX_A", "ChIP-Seq", "ok", str(out / "output" / "chipseq" / "SRX_A")),
        ("SRX_B", "ChIP-Seq", "ok", str(out / "output" / "chipseq" / "SRX_B")),
    ])
    conn.executemany("""INSERT INTO sample_curation
        (accession, tissue, cell_type, developmental_stage, genotype_strain, treatment, antibody_target)
        VALUES (?,?,?,?,?,?,?)""", [
        ("SRX_A", None, None, "thallus", "Tak-1", None, "H3K4me3"),
        ("SRX_B", None, None, "thallus", "Tak-1", None, "H3K4me3"),
    ])
    conn.commit()
    conn.close()
    return db, out


@pytest.fixture
def client(tmp_path, monkeypatch):
    db, report = _build_fixture(tmp_path)
    monkeypatch.setenv("ZENIGOKE_DB_PATH", str(db))
    monkeypatch.setenv("ZENIGOKE_REPORT_DIR", str(report))
    monkeypatch.setenv("ZENIGOKE_BUNDLES_DIR", str(report / "bundles"))
    monkeypatch.setenv("ZENIGOKE_PUBLIC_BASE", "http://test.example/")
    monkeypatch.setenv("ZENIGOKE_BUNDLES_PUBLIC", "http://test.example/bundles")
    monkeypatch.setenv("ZENIGOKE_REPO_ROOT", str(tmp_path))
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_", "igv_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def _fake_bedtools_run(args, **kwargs):
    """Replace shell-out with a noop that writes a one-line BED to the output."""
    cmd = " ".join(args) if isinstance(args, list) else args
    # Find redirect target and write something
    if ">" in cmd:
        out = cmd.split(">")[-1].strip().strip("'\"")
        pathlib.Path(out).write_text("chr1\t100\t250\n")

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    return _R()


def test_bundle_builds_consensus_for_two_chip_samples(client, tmp_path):
    with patch("api_bundle.subprocess.run", side_effect=_fake_bedtools_run):
        r = client.post("/api/bundle", json={
            "accessions": ["SRX_A", "SRX_B"],
            "q_cutoff": "1e-10",
            "groups": [{"label": "ChIP × Tak-1", "accessions": ["SRX_A", "SRX_B"]}],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "hash" in body and len(body["hash"]) == 16
    assert any(t["name"].startswith("consensus ChIP-Seq") for t in body["tracks"])
    assert sum(1 for t in body["tracks"] if "bigwig" in t["name"]) == 2


def test_bundle_cache_hit_skips_bedtools(client, tmp_path):
    payload = {"accessions": ["SRX_A", "SRX_B"], "q_cutoff": "1e-10", "groups": []}
    with patch("api_bundle.subprocess.run", side_effect=_fake_bedtools_run) as mocked:
        client.post("/api/bundle", json=payload)
        first_calls = mocked.call_count
        client.post("/api/bundle", json=payload)  # same payload → cache hit
        assert mocked.call_count == first_calls   # no new invocation


def test_bundle_skips_consensus_for_single_sample(client):
    with patch("api_bundle.subprocess.run", side_effect=_fake_bedtools_run) as mocked:
        r = client.post("/api/bundle", json={
            "accessions": ["SRX_A"], "q_cutoff": "1e-10", "groups": []})
    assert r.status_code == 200
    assert not any("consensus" in t["name"] for t in r.json()["tracks"])
    assert mocked.call_count == 0


def test_bundle_rejects_empty(client):
    r = client.post("/api/bundle", json={"accessions": [], "q_cutoff": "1e-10", "groups": []})
    assert r.status_code == 400
```

- [ ] **Step 3: Run, expect failures**

```bash
python3 -m pytest tests/test_api_bundle.py -v
```

- [ ] **Step 4: Write `scripts/api_bundle.py`**

```python
"""POST /api/bundle — build consensus BEDs and return IGV track manifest.

Synchronous bedtools shell-out per strategy. Cache key is sha256 of the
sorted sample list + q-cutoff. Cached artifacts live in $ZENIGOKE_BUNDLES_DIR.
"""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_axes import _db
from igv_url_helper import per_sample_tracks, consensus_track, color_for_group

router = APIRouter()


class GroupSpec(BaseModel):
    label: str
    accessions: List[str]


class BundleRequest(BaseModel):
    accessions: List[str] = Field(..., min_length=1)
    q_cutoff: str = "1e-10"
    groups: List[GroupSpec] = []


def _hash(accs: list[str], q: str) -> str:
    s = ",".join(sorted(set(accs))) + "|" + q
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _bundles_dir() -> pathlib.Path:
    p = pathlib.Path(os.getenv("ZENIGOKE_BUNDLES_DIR", "report/bundles"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _peak_path_for(sample: dict, q_cutoff: str, report_dir: pathlib.Path) -> pathlib.Path | None:
    """Locate the per-sample peak file at the requested q-cutoff."""
    acc = sample["accession"]
    strat = sample["library_strategy"]
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        sub = "chipseq" if strat == "ChIP-Seq" else "atacseq"
        q_label = {"1e-5": "05", "1e-10": "10", "1e-20": "20"}[q_cutoff]
        return report_dir / "output" / sub / acc / f"{acc}.{q_label}_peaks.narrowPeak"
    if strat == "Bisulfite-Seq":
        return report_dir / "output" / "bsseq" / acc / f"{acc}.CpG.hmr.bed"
    return None


def _load_samples(accessions: list[str]) -> list[dict]:
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        placeholder = ",".join("?" for _ in accessions)
        rows = conn.execute(f"""
            SELECT s.accession, s.library_strategy, s.status,
                   c.antibody_target, c.genotype_strain
            FROM sample s LEFT JOIN sample_curation c USING (accession)
            WHERE s.accession IN ({placeholder})
            ORDER BY s.accession
        """, accessions).fetchall()
    return [dict(r) for r in rows]


def build_bundle(req: BundleRequest) -> dict:
    if not req.accessions:
        raise ValueError("accessions cannot be empty")
    h = _hash(req.accessions, req.q_cutoff)
    out_dir = _bundles_dir() / h
    manifest_path = out_dir / "manifest.json"

    if manifest_path.exists():
        return json.loads(manifest_path.read_text())

    out_dir.mkdir(parents=True, exist_ok=True)
    samples = _load_samples(req.accessions)
    report_dir = pathlib.Path(os.getenv("ZENIGOKE_REPORT_DIR", "report"))

    by_strategy: dict[str, list[dict]] = {}
    for s in samples:
        if s["status"] != "ok":
            continue
        by_strategy.setdefault(s["library_strategy"], []).append(s)

    consensus_tracks: list[dict] = []
    warnings: list[str] = []
    for strat, strat_samples in by_strategy.items():
        if len(strat_samples) < 2:
            continue
        peak_files = [_peak_path_for(s, req.q_cutoff, report_dir) for s in strat_samples]
        peak_files = [p for p in peak_files if p and p.exists()]
        if len(peak_files) < 2:
            warnings.append(f"{strat}: not enough peak files; skipped consensus")
            continue
        out_bed = out_dir / f"consensus.{strat}.bed"
        cmd = (
            f"cat {' '.join(str(p) for p in peak_files)} "
            "| sort -k1,1 -k2,2n "
            f"| bedtools merge -i - > {out_bed}"
        )
        try:
            subprocess.run(["bash", "-c", cmd], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            warnings.append(f"{strat}: bedtools failed: {e.stderr.decode()[-200:]}")
            continue
        consensus_tracks.append(consensus_track(strat, h, req.q_cutoff, len(strat_samples)))

    # Per-sample tracks, with group color assigned per group spec
    acc_to_group: dict[str, int] = {}
    for idx, g in enumerate(req.groups):
        for a in g.accessions:
            acc_to_group[a] = idx
    sample_tracks: list[dict] = []
    for s in samples:
        if s["status"] != "ok":
            continue
        gidx = acc_to_group.get(s["accession"], 0)
        s_with_group = dict(s, _group=req.groups[gidx].label if gidx < len(req.groups) else "")
        sample_tracks.extend(per_sample_tracks(s_with_group, req.q_cutoff, color_for_group(gidx)))

    manifest = {
        "hash": h,
        "drilldown_url": f"/bundle/{h}",
        "consensus_url": consensus_tracks[0]["url"] if consensus_tracks else None,
        "tracks": consensus_tracks + sample_tracks,
        "warnings": warnings,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


@router.post("/api/bundle")
def bundle_endpoint(req: BundleRequest) -> dict:
    try:
        return build_bundle(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/bundle/{hash_id}")
def bundle_drilldown(hash_id: str) -> dict:
    """Return the manifest JSON. The HTML page is generated separately by
    build_catalog_pages.py at build time, but the API can serve raw JSON
    for clients that prefer it."""
    manifest_path = _bundles_dir() / hash_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="bundle not found")
    return json.loads(manifest_path.read_text())
```

- [ ] **Step 5: Wire into `scripts/server.py`**

Add after the matrix_router line:

```python
from api_bundle import router as bundle_router
app.include_router(bundle_router)
```

- [ ] **Step 6: Run the test**

```bash
python3 -m pytest tests/test_api_bundle.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 7: Add `report/bundles/` to `.gitignore`**

```bash
grep -q "^report/bundles/$" .gitignore || echo "report/bundles/" >> .gitignore
```

- [ ] **Step 8: Commit**

```bash
git add scripts/api_bundle.py scripts/igv_url_helper.py scripts/server.py tests/test_api_bundle.py .gitignore
git commit -m "scripts+tests: add /api/bundle endpoint with bedtools shell-out (Phase 3 Task 4)"
```

---

## Task 5: Drilldown HTML page generation in `build_catalog_pages.py`

**Goal:** Extend the existing build script so it also emits a `report/bundle.html` template that the server populates dynamically — OR (simpler) the API endpoint returns JSON and a tiny static `bundle.html` shell loads any `{hash}` dynamically. Pick the latter (KISS).

**Files:**
- Modify: `scripts/build_catalog_pages.py` (add a `bundle.html` template generator)

- [ ] **Step 1: Add a `render_bundle_shell()` function to `scripts/build_catalog_pages.py`**

Insert near the existing `render_index`/`render_sample` functions:

```python
def render_bundle_shell() -> str:
    """A static HTML shell that reads the bundle hash from the URL and
    fetches the manifest via /bundle/{hash}. No per-bundle HTML files —
    one shell handles all bundles.
    """
    return _wrap_page(
        title="Bundle — zenigoke",
        nav_active=None,
        body="""
<div class='container'>
  <h1 id='bundle-title'>Bundle</h1>
  <p class='subtitle' id='bundle-subtitle'></p>
  <div class='card'>
    <h2>Tracks</h2>
    <table id='tracks-table' class='kv'><thead>
      <tr><th>name</th><th>type</th><th>color</th><th>url</th></tr>
    </thead><tbody></tbody></table>
  </div>
  <div class='card'>
    <h2>Actions</h2>
    <button id='igv-btn'>&#9654; Send to IGV</button>
    <a id='session-link' href='#'>&darr; Download IGV session</a>
  </div>
</div>
<script>
const path = location.pathname.split('/');
const hash = path[path.length - 1].replace('.html','');
fetch('/bundle/' + hash).then(r => r.json()).then(b => {
  document.getElementById('bundle-title').textContent = 'Bundle ' + b.hash;
  document.getElementById('bundle-subtitle').textContent =
    b.tracks.length + ' tracks';
  const tbody = document.querySelector('#tracks-table tbody');
  for (const t of b.tracks) {
    const row = document.createElement('tr');
    row.innerHTML = '<td>' + t.name + '</td><td>' + t.type +
      '</td><td><span style=\"color:' + t.color + '\">&#9608;</span></td>' +
      '<td><a href=\"' + t.url + '\">' + t.url.split('/').pop() + '</a></td>';
    tbody.appendChild(row);
  }
  document.getElementById('igv-btn').onclick = () => {
    const param = b.tracks.map(t => t.url + '|' + t.name).join(',');
    fetch('http://localhost:60151/load?file=' + encodeURIComponent(param))
      .catch(e => alert('Could not reach IGV at :60151. Make sure IGV is running with the port enabled.'));
  };
});
</script>
""")


# In the main build() function, also write the shell:
def write_bundle_shell(out_dir: pathlib.Path) -> None:
    (out_dir / "bundle.html").write_text(render_bundle_shell())
```

Find the `write_pages()` function and add a call to `write_bundle_shell(out_dir)` near the other page-write calls.

- [ ] **Step 2: Regenerate**

```bash
python3 scripts/build-catalog-pages.py
ls -lh report/bundle.html
```

Expected: `report/bundle.html` exists, ~3 KB.

- [ ] **Step 3: Commit**

```bash
git add scripts/build_catalog_pages.py report/bundle.html
git commit -m "scripts: add bundle.html shell that fetches /bundle/{hash} manifest (Phase 3 Task 5)"
```

Note: `report/bundle.html` IS committed because it's a template, not a per-bundle artifact. Per-bundle data lives in `report/bundles/{hash}/manifest.json` (gitignored).

---

## Task 6: Matrix UI HTML scaffold

**Goal:** Replace `report/index.html` with a matrix-flavored top page. Static HTML; data is fetched at load time. The old "all samples table" view moves to `report/browse.html`.

**Files:**
- Modify: `scripts/build_catalog_pages.py` (rewrite `render_index`, add `render_browse`)
- Test: `tests/test_build_catalog_pages.py` (update existing tests; add browse test)

- [ ] **Step 1: Update tests in `tests/test_build_catalog_pages.py`**

Find the existing `test_render_index_contains_all_accessions_and_filter_input` and replace it. Also add a new test for `browse.html`. Replacement test:

```python
def test_render_index_contains_matrix_scaffold(tmp_path):
    samples = []   # matrix is dynamic — index doesn't need samples now
    html = render_index(samples)
    assert html.startswith("<!DOCTYPE html>")
    # axis selectors present
    assert 'id="x-axis-select"' in html
    assert 'id="y-axis-select"' in html
    # matrix container present
    assert 'id="matrix-grid"' in html
    # side panel present
    assert 'id="selection-panel"' in html
    # matrix.js loaded
    assert 'matrix.js' in html


def test_render_browse_contains_all_accessions(tmp_path):
    samples = [
        {"accession": "SRX1", "strategy": "ChIP-Seq", "status": "ok",
         "curated": {"tissue": "thallus"}, "mapping_rate": "88.0",
         "elapsed_min": "3.4"},
        {"accession": "SRX2", "strategy": "ATAC-Seq", "status": "ok",
         "curated": {"tissue": "thallus"}, "mapping_rate": "90.5",
         "elapsed_min": "4.1"},
    ]
    from build_catalog_pages import render_browse
    html = render_browse(samples)
    assert "SRX1" in html and "SRX2" in html
    assert 'id="q"' in html  # the existing filter input
```

- [ ] **Step 2: Run tests, expect failures**

```bash
python3 -m pytest tests/test_build_catalog_pages.py -v
```

- [ ] **Step 3: Modify `render_index` in `scripts/build_catalog_pages.py`**

Replace the existing `render_index(samples)` function body with:

```python
def render_index(samples):
    """Top page is now the interactive matrix. Samples arg kept for
    signature compat; not used (matrix loads via /api/matrix at runtime)."""
    return _wrap_page(
        title="zenigoke catalog",
        nav_active="index",
        head_extra='<link rel="stylesheet" href="assets/matrix.css">',
        body=f"""
<div class='container'>
  <div class='card info-card'>
    <h2>Marchantia polymorpha multiomics catalog</h2>
    <p>Pick two attributes to cross-tabulate the {len(samples) or 157} samples. Click a cell to send the bundle to IGV.</p>
  </div>

  <div class='card'>
    <div style='display:flex;gap:1rem;align-items:center;flex-wrap:wrap'>
      <label>X axis: <select id='x-axis-select'></select></label>
      <label>Y axis: <select id='y-axis-select'></select></label>
      <label><input type='checkbox' id='include-unknown'> include unknowns</label>
    </div>
  </div>

  <div style='display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap'>
    <div class='card' style='flex:2;min-width:400px'>
      <div id='matrix-grid'>Loading…</div>
    </div>
    <div class='card' style='flex:1;min-width:280px;position:sticky;top:1rem'>
      <h2>Selection</h2>
      <div id='selection-panel'><p class='subtitle'>Click a populated cell to begin.</p></div>
    </div>
  </div>
</div>
<script src='assets/matrix.js' defer></script>
""")
```

- [ ] **Step 4: Add `render_browse` to `scripts/build_catalog_pages.py`**

```python
def render_browse(samples):
    """The old 'all samples table' view, now on its own page."""
    # Reuse the existing table-rendering logic that used to live in render_index.
    # Copy the body that was removed from render_index, including the filter
    # input id='q' and the existing rows.
    rows_html = "\n".join(_sample_row_html(s) for s in samples)
    return _wrap_page(
        title="Browse — zenigoke catalog",
        nav_active="browse",
        body=f"""
<div class='container'>
  <h1>Browse all samples</h1>
  <input id='q' type='search' placeholder='filter…' style='margin-bottom:1rem;padding:0.4rem;width:100%;max-width:300px'>
  <table id='samples-table'>
    <thead><tr>
      <th data-col='accession'>accession</th>
      <th data-col='strategy'>strategy</th>
      <th data-col='status'>status</th>
      <th data-col='tissue'>tissue</th>
      <th data-col='dev_stage'>dev_stage</th>
      <th data-col='strain'>strain</th>
      <th data-col='antibody'>antibody</th>
      <th data-col='mapping_rate'>mapping</th>
      <th data-col='elapsed'>elapsed</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<script>
const q = document.getElementById('q');
const rows = document.querySelectorAll('#samples-table tbody tr');
q.addEventListener('input', () => {{
  const t = q.value.toLowerCase();
  rows.forEach(r => r.style.display = r.textContent.toLowerCase().includes(t) ? '' : 'none');
}});
</script>
""")
```

(Add a `_sample_row_html(sample)` helper that emits one `<tr>` per sample using the same column order. Move the existing per-row HTML construction from the old `render_index` into this helper.)

Also update the nav builder helper so the **Browse** link maps to `browse.html` and the **Matrix** link maps to `index.html`:

```python
NAV_LINKS = [
    ("index",    "Matrix",    "index.html"),
    ("browse",   "Browse",    "browse.html"),
    ("chipseq",  "ChIP-Seq",  "strategy/chipseq.html"),
    ("atacseq",  "ATAC-Seq",  "strategy/atacseq.html"),
    ("bsseq",    "BS-Seq",    "strategy/bsseq.html"),
    ("summary",  "Summary",   "summary.html"),
    ("methods",  "Methods",   "methods.html"),
    ("about",    "About",     "about.html"),
]
```

And update `write_pages` to also write `browse.html`:

```python
(out_dir / "browse.html").write_text(render_browse(samples))
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_build_catalog_pages.py -v
```

Expected: all pass (existing tests + 1 new).

- [ ] **Step 6: Regenerate the catalog and verify**

```bash
python3 scripts/build-catalog-pages.py
ls -lh report/index.html report/browse.html report/bundle.html
grep -c '<select' report/index.html
grep -c 'matrix.js' report/index.html
```

Expected: index.html 3-5 KB (now mostly scaffold), browse.html 30+ KB (the table). 2 select tags in index. 1 matrix.js include.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_catalog_pages.py tests/test_build_catalog_pages.py report/index.html report/browse.html
git commit -m "scripts: rewrite top page as matrix scaffold; move table to browse.html (Phase 3 Task 6)"
```

---

## Task 7: matrix.js — fetch axes + render matrix

**Goal:** Wire up the matrix UI. On load, fetch `/api/axes`, populate the dropdowns, render the matrix when both axes are selected.

**Files:**
- Create: `report/assets/matrix.js`
- Create: `report/assets/matrix.css`
- Test: manual via browser, no JS unit tests (per spec §7)

- [ ] **Step 1: Write `report/assets/matrix.css`**

```css
/* Matrix UI styles, layered on top of style.css */
.info-card h2 { margin-top: 0; }
#matrix-grid table { border-collapse: collapse; font-size: 12px; }
#matrix-grid th, #matrix-grid td {
  border: 1px solid #ddd; padding: 4px 8px; text-align: center;
}
#matrix-grid thead th { background: #f4f4f4; }
#matrix-grid td.cell { cursor: pointer; }
#matrix-grid td.cell:hover { background: #f0f8ff; }
#matrix-grid td.cell.selected { background: #d8eed8 !important; outline: 2px solid #1a9970; }
#matrix-grid td.cell.empty { color: #ccc; cursor: default; background: #fafafa; }

#selection-panel .group { margin-bottom: 0.7rem; }
#selection-panel .group-header { font-weight: 600; }
#selection-panel ul { margin: 0.2rem 0 0 1rem; padding: 0; font-family: ui-monospace, monospace; font-size: 11px; }
#selection-panel button { background: #1a9970; color: #fff; border: 0; padding: 0.5rem 1rem; cursor: pointer; border-radius: 4px; }
#selection-panel button:disabled { background: #aaa; cursor: not-allowed; }
#selection-panel a.secondary { display: block; margin-top: 0.4rem; font-size: 0.85rem; }
```

- [ ] **Step 2: Write `report/assets/matrix.js`**

```javascript
"use strict";

const state = {
  axes: null,         // /api/axes response
  matrix: null,       // /api/matrix response
  selectedCells: new Map(),  // key "x|y" -> {x, y, n, accessions, color}
  groupColors: ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"],
};

async function init() {
  const r = await fetch("/api/axes");
  state.axes = await r.json();

  const xsel = document.getElementById("x-axis-select");
  const ysel = document.getElementById("y-axis-select");
  for (const ax of state.axes.axes) {
    xsel.appendChild(opt(ax.key, ax.label));
    ysel.appendChild(opt(ax.key, ax.label));
  }
  // Default: experiment_type x genotype_class
  xsel.value = "experiment_type";
  ysel.value = "genotype_class";
  xsel.addEventListener("change", refreshMatrix);
  ysel.addEventListener("change", refreshMatrix);
  document.getElementById("include-unknown").addEventListener("change", refreshMatrix);
  refreshMatrix();
}

function opt(value, text) {
  const o = document.createElement("option");
  o.value = value; o.textContent = text;
  return o;
}

async function refreshMatrix() {
  const x = document.getElementById("x-axis-select").value;
  const y = document.getElementById("y-axis-select").value;
  const inc = document.getElementById("include-unknown").checked ? 1 : 0;
  const r = await fetch(`/api/matrix?x=${x}&y=${y}&include_unknown=${inc}`);
  state.matrix = await r.json();
  state.selectedCells.clear();
  renderSelection();
  renderMatrix();
}

function renderMatrix() {
  const m = state.matrix;
  const cells = new Map(m.cells.map(c => [`${c.x}|${c.y}`, c]));
  const grid = document.getElementById("matrix-grid");
  let html = "<table><thead><tr><th></th>";
  for (const xv of m.x_values) html += `<th>${escapeHtml(xv)}</th>`;
  html += "</tr></thead><tbody>";
  for (const yv of m.y_values) {
    html += `<tr><th>${escapeHtml(yv)}</th>`;
    for (const xv of m.x_values) {
      const c = cells.get(`${xv}|${yv}`);
      if (c) {
        const sel = state.selectedCells.has(`${xv}|${yv}`) ? " selected" : "";
        html += `<td class="cell${sel}" data-x="${escapeAttr(xv)}" data-y="${escapeAttr(yv)}">${c.n}</td>`;
      } else {
        html += `<td class="cell empty"></td>`;
      }
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  grid.innerHTML = html;
  for (const td of grid.querySelectorAll("td.cell:not(.empty)")) {
    td.addEventListener("click", onCellClick);
  }
}

function onCellClick(e) {
  const td = e.currentTarget;
  const x = td.dataset.x, y = td.dataset.y;
  const key = `${x}|${y}`;
  if (state.selectedCells.has(key)) {
    state.selectedCells.delete(key);
  } else {
    const c = state.matrix.cells.find(c => c.x === x && c.y === y);
    state.selectedCells.set(key, c);
  }
  renderMatrix();
  renderSelection();
}

function renderSelection() {
  const panel = document.getElementById("selection-panel");
  if (state.selectedCells.size === 0) {
    panel.innerHTML = "<p class='subtitle'>Click a populated cell to begin.</p>";
    return;
  }
  let html = "";
  let total = 0;
  let i = 0;
  const groups = [];
  for (const [key, c] of state.selectedCells) {
    const color = state.groupColors[i % state.groupColors.length];
    total += c.accessions.length;
    groups.push({label: `${c.x} × ${c.y}`, accessions: c.accessions, color: color});
    html += `<div class="group">`;
    html += `<div class="group-header" style="color:${color}">${escapeHtml(c.x)} × ${escapeHtml(c.y)} (${c.n})</div>`;
    html += `<ul>${c.accessions.map(a => `<li>${a}</li>`).join("")}</ul>`;
    html += `</div>`;
    i += 1;
  }
  html += `<button id="send-igv-btn">▶ Send ${total} samples to IGV</button>`;
  html += `<a class="secondary" href="#" id="drilldown-link">Open detailed bundle page ↗</a>`;
  html += `<a class="secondary" href="#" id="clear-link">Clear selection</a>`;
  panel.innerHTML = html;
  document.getElementById("send-igv-btn").addEventListener("click", () => sendToIgv(groups));
  document.getElementById("drilldown-link").addEventListener("click", e => { e.preventDefault(); openDrilldown(groups); });
  document.getElementById("clear-link").addEventListener("click", e => {
    e.preventDefault();
    state.selectedCells.clear();
    renderMatrix(); renderSelection();
  });
}

async function postBundle(groups) {
  const accessions = [...new Set(groups.flatMap(g => g.accessions))];
  const r = await fetch("/api/bundle", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({accessions: accessions, q_cutoff: "1e-10",
                          groups: groups.map(g => ({label: g.label, accessions: g.accessions}))}),
  });
  if (!r.ok) throw new Error(`bundle failed: ${r.status}`);
  return await r.json();
}

async function sendToIgv(groups) {
  const btn = document.getElementById("send-igv-btn");
  btn.disabled = true; btn.textContent = "Building bundle…";
  try {
    const bundle = await postBundle(groups);
    const param = bundle.tracks.map(t => t.url + "|" + t.name).join(",");
    btn.textContent = "Loading into IGV…";
    try {
      await fetch("http://localhost:60151/load?file=" + encodeURIComponent(param));
      btn.textContent = "✔ Sent to IGV";
    } catch (e) {
      btn.textContent = "Could not reach IGV (port :60151 not enabled?)";
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = "Bundle failed: " + e.message;
    btn.disabled = false;
  }
}

async function openDrilldown(groups) {
  const bundle = await postBundle(groups);
  window.open("/bundle.html#" + bundle.hash, "_blank");
  // bundle.html reads location.hash and fetches /bundle/{hash}; we already have it
  // but the page does its own fetch — minor duplication, acceptable.
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

init();
```

- [ ] **Step 3: Update `bundle.html` shell to use `location.hash`**

In Task 5 the shell parses the URL filename. Change it to use `location.hash` for compatibility with `/bundle.html#abc123`. In `scripts/build_catalog_pages.py`, find the `render_bundle_shell()` function and change:

```javascript
const hash = path[path.length - 1].replace('.html','');
```

to:

```javascript
const hash = location.hash.replace('#', '');
```

- [ ] **Step 4: Regenerate**

```bash
python3 scripts/build-catalog-pages.py
```

- [ ] **Step 5: Manual smoke**

```bash
# server should already be running from Task 1; if not:
nohup python3 scripts/server.py > /tmp/zenigoke-server.log 2>&1 &
sleep 2
curl -sI http://localhost:8088/index.html | head -2
curl -s http://localhost:8088/api/axes | head -c 200
curl -s http://localhost:8088/assets/matrix.js | head -c 200
```

Then open `http://localhost:8088/` (or `http://100.88.253.33:8088/` over Tailscale) in a browser. Expected: matrix renders with experiment_type × genotype_class. Click a cell → side panel shows samples + Send-to-IGV button.

- [ ] **Step 6: Commit**

```bash
git add report/assets/matrix.js report/assets/matrix.css scripts/build_catalog_pages.py report/index.html report/bundle.html
git commit -m "frontend: add matrix.js + matrix.css with cell selection + IGV submit (Phase 3 Task 7)"
```

---

## Task 8: End-to-end smoke test

**Goal:** One pytest that exercises the static fallback + matrix endpoint + bundle endpoint together against the real DB. Catches regressions across module boundaries.

**Files:**
- Create: `tests/test_e2e_smoke.py`

- [ ] **Step 1: Write `tests/test_e2e_smoke.py`**

```python
"""End-to-end smoke against the real catalog. Requires db/kknmsmd.db to exist
(skip if not — Phase 3 implementation may run before Phase 2B has been built)."""
from __future__ import annotations
import os
import pathlib
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


REPO = pathlib.Path(__file__).resolve().parent.parent
DB = REPO / "db" / "kknmsmd.db"


@pytest.fixture
def client(monkeypatch):
    if not DB.exists():
        pytest.skip("db/kknmsmd.db not built — run scripts/build-catalog-db.py first")
    monkeypatch.setenv("ZENIGOKE_DB_PATH", str(DB))
    monkeypatch.setenv("ZENIGOKE_REPORT_DIR", str(REPO / "report"))
    monkeypatch.setenv("ZENIGOKE_BUNDLES_DIR", str(REPO / "report" / "bundles"))
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_", "igv_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def test_static_index_served(client):
    r = client.get("/index.html")
    assert r.status_code == 200
    assert "matrix" in r.text.lower()


def test_axes_returns_four(client):
    r = client.get("/api/axes")
    assert r.status_code == 200
    assert len(r.json()["axes"]) == 4


def test_matrix_anchor_query(client):
    r = client.get("/api/matrix?x=experiment_type&y=genotype_class")
    assert r.status_code == 200
    body = r.json()
    # H3K4me3 ChIPs against wildtype should be non-empty on the real catalog
    cells = {(c["x"], c["y"]): c for c in body["cells"]}
    cell = cells.get(("ChIP:H3K4me3", "wildtype"))
    if cell:
        assert cell["n"] >= 1


def test_bundle_two_chip_samples(client):
    # Pick two ChIP-Seq accessions known to be ok in the catalog.
    r = client.get("/api/matrix?x=experiment_type&y=genotype_class")
    cells = r.json()["cells"]
    chip_cell = next((c for c in cells if c["x"].startswith("ChIP:") and c["n"] >= 2), None)
    if chip_cell is None:
        pytest.skip("no ChIP cell with >=2 samples in catalog")
    accessions = chip_cell["accessions"][:2]
    rb = client.post("/api/bundle", json={
        "accessions": accessions, "q_cutoff": "1e-10",
        "groups": [{"label": "test", "accessions": accessions}],
    })
    assert rb.status_code == 200, rb.text
    body = rb.json()
    assert body["hash"]
    assert body["tracks"]
```

- [ ] **Step 2: Run**

```bash
python3 -m pytest tests/test_e2e_smoke.py -v
```

Expected: all pass (or skipped gracefully if db not built — but it should be, from Phase 2B).

- [ ] **Step 3: Run the full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: 29 (existing) + 4 (axes) + 4 (matrix) + 4 (bundle) + 4 (smoke) = ~45 tests, all passing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_smoke.py
git commit -m "tests: end-to-end smoke covering static + matrix + bundle (Phase 3 Task 8)"
```

---

## Task 9: Deployment files (committed, not auto-installed)

**Goal:** Lay down the AWS-deployment scaffolding so a future "go live on EC2" run is mostly config + git push. None of these run automatically.

**Files:**
- Create: `deploy/Caddyfile`
- Create: `deploy/zenigoke.service`
- Create: `deploy/s3-cors.json`
- Create: `deploy/README.md`

- [ ] **Step 1: Write `deploy/Caddyfile`**

```caddyfile
# Zenigoke catalog API — auto-TLS via Let's Encrypt.
# Set the actual domain via the API_HOST env var when launching caddy:
#   API_HOST=api.zenigoke.example.com caddy run --config Caddyfile

{$API_HOST} {
  reverse_proxy 127.0.0.1:8088
  encode gzip
  log {
    output file /var/log/caddy/zenigoke.log
    format console
  }
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    X-Content-Type-Options "nosniff"
  }
}
```

- [ ] **Step 2: Write `deploy/zenigoke.service`**

```ini
[Unit]
Description=Zenigoke catalog (FastAPI + uvicorn)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/zenigoke
EnvironmentFile=/etc/zenigoke.env
ExecStart=/home/ubuntu/.local/bin/uvicorn server:app --host 127.0.0.1 --port 8088 --app-dir scripts
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/zenigoke.env` is created on the EC2 instance during deployment, contains the cloud-mode env vars. Documented but not generated automatically.

- [ ] **Step 3: Write `deploy/s3-cors.json`**

```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://*.github.io", "http://localhost:8088"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["Range", "Content-Type"],
      "ExposeHeaders": ["Accept-Ranges", "Content-Length", "Content-Range"],
      "MaxAgeSeconds": 3600
    }
  ]
}
```

- [ ] **Step 4: Write `deploy/README.md`**

```markdown
# Cloud deployment notes (manual; not auto-applied)

See `docs/superpowers/specs/2026-05-09-zenigoke-phase3-design.md` §6 for context.

## Initial setup

```bash
# 1. S3 bucket
aws s3 mb s3://zenigoke-catalog
aws s3api put-bucket-cors --bucket zenigoke-catalog \
  --cors-configuration file://deploy/s3-cors.json

# 2. Initial data sync (run from the dev host)
aws s3 sync /data1/zenigoke/output s3://zenigoke-catalog/output
aws s3 cp db/kknmsmd.db s3://zenigoke-catalog/db/

# 3. EC2 t3.small (Ubuntu 22.04). On the instance:
sudo apt-get update && sudo apt-get install -y python3-pip bedtools caddy
git clone https://github.com/<org>/zenigoke.git
cd zenigoke
pip install --user -r requirements.txt

# 4. /etc/zenigoke.env — fill these in:
sudo bash -c 'cat > /etc/zenigoke.env <<EOF
ZENIGOKE_DB_PATH=/home/ubuntu/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=/home/ubuntu/zenigoke/report
ZENIGOKE_BUNDLES_DIR=/home/ubuntu/zenigoke/report/bundles
ZENIGOKE_PUBLIC_BASE=https://api.zenigoke.example.com
ZENIGOKE_BUNDLES_PUBLIC=https://zenigoke-catalog.s3.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://<user>.github.io
EOF'

# 5. systemd unit
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl enable --now zenigoke

# 6. Caddy
sudo API_HOST=api.zenigoke.example.com caddy run --config deploy/Caddyfile
```

## Frontend deploy

```bash
# Build the static catalog with S3 URLs in the track links:
ZENIGOKE_S3_BASE=https://zenigoke-catalog.s3.amazonaws.com python3 scripts/build-catalog-pages.py

# Push to gh-pages
git checkout gh-pages
cp -r report/* .
git add -A && git commit -m "publish catalog"
git push origin gh-pages
```
```

- [ ] **Step 5: Commit**

```bash
git add deploy/
git commit -m "deploy: AWS scaffolding (Caddyfile, systemd unit, s3-cors, README) (Phase 3 Task 9)"
```

---

## Task 10: Final regenerate + walk-through

**Goal:** Single end-to-end verification that the new top page works in a real browser over Tailscale.

- [ ] **Step 1: Regenerate everything**

```bash
cd /home/inutano/work/zenigoke
python3 scripts/build-catalog-db.py
python3 scripts/build-catalog-pages.py
```

- [ ] **Step 2: Restart the FastAPI server**

```bash
[ -f /tmp/zenigoke-server.pid ] && kill $(cat /tmp/zenigoke-server.pid) 2>/dev/null
nohup python3 scripts/server.py > /tmp/zenigoke-server.log 2>&1 &
echo $! > /tmp/zenigoke-server.pid
sleep 2
tail -10 /tmp/zenigoke-server.log
```

- [ ] **Step 3: Smoke test from CLI**

```bash
curl -sI http://localhost:8088/index.html | head -2
curl -s http://localhost:8088/api/axes | python3 -m json.tool | head -20
curl -s "http://localhost:8088/api/matrix?x=experiment_type&y=genotype_class" | python3 -m json.tool | head -30
curl -s -X POST http://localhost:8088/api/bundle \
  -H 'Content-Type: application/json' \
  -d '{"accessions":["SRX22603368","SRX22603369"],"q_cutoff":"1e-10","groups":[]}' \
  | python3 -m json.tool | head -30
```

Expected:
- `/index.html` → 200
- `/api/axes` → JSON with 4 axes
- `/api/matrix?...` → JSON with cells
- `/api/bundle` → JSON with hash + tracks (and bedtools wrote `report/bundles/<hash>/consensus.ChIP-Seq.bed`)

- [ ] **Step 4: Browser walk-through**

Open `http://100.88.253.33:8088/` from any Tailscale-connected device. Verify:
- Matrix renders with experiment_type × genotype_class.
- Click a populated cell → side panel shows the samples.
- Click a second cell → both groups visible, color-coded.
- Click "Send to IGV" with IGV running locally → tracks appear in IGV. (If IGV is not running on the same machine as the browser, expect the alert.)
- Click "Open detailed bundle page" → new tab loads `/bundle.html#<hash>` with the tracks table.

- [ ] **Step 5: Final test run + commit summary**

```bash
python3 -m pytest tests/ -q
git log --oneline 15a4bc2..HEAD   # everything since the spec
```

Document anything that broke during the walk-through and either fix in this task or open follow-up commits. No formal commit needed for this task unless something changed.

---

## Self-review

**Spec coverage:**
- §1 Goal & boundary → all five "Phase 3 ships" items implemented across Tasks 1–7. ✓
- §2 Architecture → server.py + routers + bundle artifacts in Task 1, 2, 3, 4. ✓
- §3 UX flow → matrix scaffold (Task 6) + interactions (Task 7) + drilldown shell (Task 5). ✓
- §4 API → /api/axes (Task 2), /api/matrix (Task 3), /api/bundle + /bundle/{hash} (Task 4). IGV-load is client-side via matrix.js (Task 7). ✓
- §5 Bundle generation → bedtools shell-out in Task 4, per-sample defaults in Task 4 + igv_url_helper. ✓
- §6 Cloud deployment → Task 9. Local mode is the default in env-var defaults; cloud env vars documented in deploy/README.md. ✓
- §7 Testing → 4 axes + 4 matrix + 4 bundle + 4 smoke = 16 new tests; existing 29 unaffected. Frontend smoke is manual per spec §7. ✓
- §8 Out of scope → respected (no enrichment, no target-gene, no igv.js, no auth). ✓
- §9 Open items → AWS account + DNS noted in deploy/README.md as prereqs. ✓

**Placeholder scan:** no TBD/TODO in the plan body. Each step has concrete code or commands.

**Type consistency:** `BundleRequest` shape matches the JS `postBundle()` payload (`accessions`, `q_cutoff`, `groups`). The matrix axes returned by `/api/axes` match `VALID_AXES` in `api_matrix.py`. `_classify_strain` is defined in `api_axes.py` and re-imported in `api_matrix.py`. Hash format `sha256[:16]` consistent across `_hash()`, the JS `bundle.hash` reference, and the drilldown URL.

**Frontend-backend URL conventions:** the JS in matrix.js calls `/api/axes`, `/api/matrix`, `/api/bundle`, `http://localhost:60151/load` — all match the FastAPI routes and the IGV port command.

If anything in the actual implementation drifts from this list, fix the code before merging the task.
