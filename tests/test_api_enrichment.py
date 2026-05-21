"""Test /api/enrichment endpoint."""
from __future__ import annotations
import pathlib
import sqlite3
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _build_fixture(tmp_path: pathlib.Path):
    """Tiny DB + 3 fake peak files + chrom.sizes-equivalent."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE sample (accession TEXT PRIMARY KEY, library_strategy TEXT, status TEXT, output_dir TEXT);
      CREATE TABLE sample_curation (
        accession TEXT PRIMARY KEY, tissue TEXT, cell_type TEXT,
        developmental_stage TEXT, genotype_strain TEXT, treatment TEXT, antibody_target TEXT
      );
    """)
    # Three samples: A & B have peaks heavily overlapping user regions; C is null.
    peaks_dir = tmp_path / "peaks"
    for acc, peaks in [
        ("SRX_A", "chr1\t100\t300\nchr1\t500\t700\nchr1\t900\t1100\n"),
        ("SRX_B", "chr1\t150\t350\nchr1\t800\t900\n"),
        ("SRX_C", "chr1\t5000\t5100\n"),
    ]:
        d = peaks_dir / "chipseq" / acc
        d.mkdir(parents=True)
        (d / f"{acc}.10_peaks.narrowPeak").write_text(peaks)
    conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", [
        ("SRX_A", "ChIP-Seq", "ok", str(peaks_dir / "chipseq" / "SRX_A")),
        ("SRX_B", "ChIP-Seq", "ok", str(peaks_dir / "chipseq" / "SRX_B")),
        ("SRX_C", "ChIP-Seq", "ok", str(peaks_dir / "chipseq" / "SRX_C")),
    ])
    conn.executemany("""INSERT INTO sample_curation
        (accession, tissue, cell_type, developmental_stage, genotype_strain, treatment, antibody_target)
        VALUES (?,?,?,?,?,?,?)""", [
        ("SRX_A", None, None, "thallus", "Tak-1", None, "H3K4me3"),
        ("SRX_B", None, None, "thallus", "Tak-1", None, "H3K4me3"),
        ("SRX_C", None, None, "thallus", "Tak-1", None, "input"),
    ])
    conn.commit(); conn.close()
    chrom_sizes = peaks_dir / "chrom.sizes"
    chrom_sizes.write_text("chr1\t10000\n")
    return db, peaks_dir, chrom_sizes


@pytest.fixture
def client(tmp_path, monkeypatch):
    db, peaks_dir, chrom_sizes = _build_fixture(tmp_path)
    monkeypatch.setenv("ZENIGOKE_DB_PATH", str(db))
    monkeypatch.setenv("ZENIGOKE_REPORT_DIR", str(tmp_path))
    (tmp_path / "bundles").mkdir()
    monkeypatch.setenv("ZENIGOKE_BUNDLES_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("ZENIGOKE_PEAKS_DIR", str(peaks_dir))
    monkeypatch.setenv("ZENIGOKE_CHROM_SIZES", str(chrom_sizes))
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def test_enrichment_known_overlap(client):
    """User regions overlap 2/2 with SRX_A peaks; expect tiny p-value."""
    r = client.post("/api/enrichment", json={
        "regions_bed": "chr1\t120\t180\nchr1\t910\t950\n",
        "q_cutoff": "1e-10",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_user_regions"] == 2
    assert body["n_experiments_tested"] >= 1
    # Top hit should be SRX_A (more peaks overlapping)
    top = body["results"][0]
    assert top["accession"] == "SRX_A"
    assert top["overlap_count"] == 2
    assert top["p_value"] < 0.05


def test_enrichment_filter_by_strategy(client):
    r = client.post("/api/enrichment", json={
        "regions_bed": "chr1\t120\t180\n",
        "q_cutoff": "1e-10",
        "filter": {"strategy": ["Bisulfite-Seq"]},
    })
    assert r.status_code == 200
    assert r.json()["n_experiments_tested"] == 0


def test_enrichment_rejects_empty_bed(client):
    r = client.post("/api/enrichment", json={"regions_bed": "", "q_cutoff": "1e-10"})
    assert r.status_code in (400, 422)


def test_enrichment_rejects_invalid_q_cutoff(client):
    r = client.post("/api/enrichment", json={
        "regions_bed": "chr1\t100\t200\n", "q_cutoff": "0.5"})
    assert r.status_code == 422


def test_enrichment_cache_hit_idempotent(client):
    """Two identical POSTs return identical results."""
    payload = {"regions_bed": "chr1\t100\t200\n", "q_cutoff": "1e-10"}
    r1 = client.post("/api/enrichment", json=payload).json()
    r2 = client.post("/api/enrichment", json=payload).json()
    assert r1["results"] == r2["results"]
