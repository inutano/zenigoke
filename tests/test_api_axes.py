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
        ("SRX1", "thallus", None, "thallus",   "Tak-1", None, "H3K4me3"),
        ("SRX2", "thallus", None, "thallus",   "Mpez1", None, "H3K27me3"),
        ("SRX3", "thallus", None, "gemmaling", "Tak-1", None, None),
        ("SRX4", None,      None, None,        "Mpmet", None, None),
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
    from server import app
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
