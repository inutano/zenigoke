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
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_", "igv_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def _fake_bedtools_run(args, **kwargs):
    """Replace shell-out with a noop that writes a one-line BED to the output."""
    cmd = " ".join(args) if isinstance(args, list) else args
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
    assert r.status_code in (400, 422)
