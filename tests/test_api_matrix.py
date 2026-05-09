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
