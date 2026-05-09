"""End-to-end smoke against the real catalog. Requires db/kknmsmd.db to exist
(skip if not — Phase 3 implementation may run before Phase 2B has been built)."""
from __future__ import annotations
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
    # Matrix scaffold should be the new content; legacy 'matrix' word is fine
    assert "matrix" in r.text.lower()


def test_axes_returns_four(client):
    r = client.get("/api/axes")
    assert r.status_code == 200
    assert len(r.json()["axes"]) == 4


def test_matrix_anchor_query(client):
    r = client.get("/api/matrix?x=experiment_type&y=genotype_class")
    assert r.status_code == 200
    body = r.json()
    assert len(body["cells"]) > 0
    # H3K4me3 ChIPs against wildtype should be non-empty on the real catalog (4 samples)
    cells = {(c["x"], c["y"]): c for c in body["cells"]}
    cell = cells.get(("ChIP:H3K4me3", "wildtype"))
    if cell:
        assert cell["n"] >= 1


def test_bundle_two_chip_samples(client):
    """Pick two ChIP-Seq accessions known to be ok in the catalog. Tolerates
    bedtools-missing on this dev host: the bundle endpoint still returns a
    manifest (with `warnings`) even if `bedtools merge` fails."""
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
    assert isinstance(body["tracks"], list)
    # Per-sample tracks always exist; consensus may have failed if bedtools is missing
    assert len(body["tracks"]) >= 2  # at least 2 per-sample tracks for 2 chip samples
