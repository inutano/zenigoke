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
    monkeypatch.setenv("ZENIGOKE_BLACKLIST", "")
    monkeypatch.setenv("ZENIGOKE_ENRICH_CACHE", str(tmp_path / "ecache"))
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


@pytest.fixture
def client_bl(tmp_path, monkeypatch):
    db, peaks_dir, chrom_sizes = _build_fixture(tmp_path)
    bl = tmp_path / "blacklist.bed"
    bl.write_text("chr1\t100\t200\n")          # covers user region 120-180 and part of SRX_A/SRX_B peaks
    for k, v in {"ZENIGOKE_DB_PATH": db, "ZENIGOKE_REPORT_DIR": tmp_path,
                 "ZENIGOKE_BUNDLES_DIR": tmp_path / "bundles", "ZENIGOKE_PEAKS_DIR": peaks_dir,
                 "ZENIGOKE_CHROM_SIZES": chrom_sizes, "ZENIGOKE_BLACKLIST": bl,
                 "ZENIGOKE_ENRICH_CACHE": tmp_path / "ecache"}.items():
        monkeypatch.setenv(k, str(v))
    (tmp_path / "bundles").mkdir()
    for mod in [m for m in list(sys.modules) if m.startswith(("server", "api_"))]:
        del sys.modules[mod]
    from server import app
    return TestClient(app)


def test_enrichment_excludes_blacklisted_regions(client_bl):
    r = client_bl.post("/api/enrichment", json={
        "regions_bed": "chr1\t120\t180\nchr1\t910\t950\n",
        "q_cutoff": "1e-10",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_user_regions"] == 1          # 120-180 is inside the blacklist
    top = body["results"][0]
    assert top["accession"] == "SRX_A"
    assert top["overlap_count"] == 1            # only 910-950 vs SRX_A 900-1100


def test_enrichment_blacklist_filters_peaks_and_genome(client_bl):
    """Blacklist hits drop whole peaks and shrink the genome used for p_null."""
    r = client_bl.post("/api/enrichment", json={
        "regions_bed": "chr1\t910\t950\n", "q_cutoff": "1e-10"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["genome_bp"] == 10000 - 100                 # chrom.sizes minus blacklist bp
    srx_a = next(x for x in body["results"] if x["accession"] == "SRX_A")
    # SRX_A peak 100-300 overlaps the blacklist and is removed; 500-700 + 900-1100 remain
    assert srx_a["p_null"] == pytest.approx(400 / 9900)


def test_enrichment_all_regions_blacklisted_is_400(client_bl):
    r = client_bl.post("/api/enrichment", json={
        "regions_bed": "chr1\t120\t180\nchr1\t150\t160\n", "q_cutoff": "1e-10"})
    assert r.status_code == 400
    assert "blacklist" in r.json()["detail"]


def test_enrichment_malformed_coordinates_are_400_not_500(client_bl):
    r = client_bl.post("/api/enrichment", json={
        "regions_bed": "chr1\t500\t400\n", "q_cutoff": "1e-10"})
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["chr1\t500\t400\n", "chr1\t-5\t10\n", "chr1\t10\t10\n"])
def test_enrichment_malformed_coordinates_400_without_blacklist(client, bad):
    """Same 400 path whether or not a blacklist is active (`client` disables it)."""
    r = client.post("/api/enrichment", json={"regions_bed": bad, "q_cutoff": "1e-10"})
    assert r.status_code == 400, r.text


def test_enrichment_comment_and_header_lines_still_skipped(client):
    r = client.post("/api/enrichment", json={
        "regions_bed": "track name=x\n#c\nbrowser position chr1:1-9\nchr1\t120\t180\n",
        "q_cutoff": "1e-10"})
    assert r.status_code == 200, r.text
    assert r.json()["n_user_regions"] == 1


def test_filtered_is_race_free_on_cold_cache(tmp_path, monkeypatch):
    """Concurrent cold-cache calls must neither raise nor expose partial output."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import api_enrichment as ae

    src = tmp_path / "peaks.bed"
    src.write_text("".join(f"chr1\t{i * 1000}\t{i * 1000 + 500}\n" for i in range(20000)))
    bl = tmp_path / "bl.bed"
    bl.write_text("chr1\t0\t10000000\n")                 # removes the first 10000 peaks
    expected = "".join(f"chr1\t{i * 1000}\t{i * 1000 + 500}\n" for i in range(10000, 20000))
    monkeypatch.setenv("ZENIGOKE_BLACKLIST", str(bl))

    workers = 8
    for rnd in range(5):                                  # fresh cold cache each round
        monkeypatch.setenv("ZENIGOKE_ENRICH_CACHE", str(tmp_path / f"ecache{rnd}"))
        barrier = threading.Barrier(workers)

        def work(_):
            barrier.wait()
            out = ae._filtered(src)
            return out, out.read_text()

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(work, range(workers)))   # re-raises any worker exception
        assert len({str(o) for o, _ in results}) == 1
        assert all(text == expected for _, text in results)
        assert [p.name for p in (tmp_path / f"ecache{rnd}" / "blacklisted").iterdir() if p.suffix == ".tmp"] == []


def test_blacklist_path_warns_when_configured_file_missing(tmp_path, monkeypatch, caplog):
    import logging
    import api_enrichment as ae
    missing = tmp_path / "nope.bed"
    monkeypatch.setenv("ZENIGOKE_BLACKLIST", str(missing))
    ae._WARNED_BLACKLISTS.clear()
    with caplog.at_level(logging.WARNING):
        assert ae._blacklist_path() is None
        assert ae._blacklist_path() is None               # second call: no duplicate warning
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(msgs) == 1 and str(missing) in msgs[0]


def test_blacklist_path_silent_when_disabled_or_default(tmp_path, monkeypatch, caplog):
    import logging
    import api_enrichment as ae
    ae._WARNED_BLACKLISTS.clear()
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv("ZENIGOKE_BLACKLIST", "")
        assert ae._blacklist_path() is None               # "" disables, no warning
        monkeypatch.delenv("ZENIGOKE_BLACKLIST")
        assert ae._blacklist_path() is not None           # unset -> repo default
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
