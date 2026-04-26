"""Tests for build_report. We build a tiny fake output tree and check the
generated HTML contains expected rows + gracefully handles missing data."""
from __future__ import annotations
import json
import pathlib
import sys
import tempfile
import textwrap
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from build_report import collect_samples, render_html  # noqa: E402


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_collect_samples_finds_ok_and_failed(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")
    (status / "SRX2.failed").write_text("exit_code=3\nstrategy=ChIP-Seq\n--- last 50 lines of log ---\nboom")

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    accs = {s["accession"] for s in samples}
    assert accs == {"SRX1", "SRX2"}
    by = {s["accession"]: s for s in samples}
    assert by["SRX1"]["status"] == "ok"
    assert by["SRX2"]["status"] == "failed"
    assert by["SRX2"]["exit_code"] == "3"


def test_collect_samples_reads_chipseq_stats(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")

    # Fake 15-column chipseq stats TSV from pipeline-v2.sh
    stats_dir = tmp_path / "output" / "chipseq" / "SRX1"
    _write(
        stats_dir / "SRX1.stats.tsv",
        "SRX1\tPE\t1.2G\t10000000\t9500000\t0.92\t0.11\t500M\t200M\t50M\t1200\t300\t80\t5.3\n",
    )

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    s = samples[0]
    assert s["mapping_rate"] == "0.92"
    assert s["peaks_q10"] == "300"


def test_collect_samples_merges_curated_metadata(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "SRX1.ok").write_text("")

    curated_dir = tmp_path / "metadata" / "curated"
    _write(
        curated_dir / "SRX1.json",
        json.dumps({"accession": "SRX1", "tissue": "thallus",
                    "developmental_stage": "thallus", "antibody_target": "H3K4me3"}),
    )

    samples = collect_samples(
        data_root=tmp_path,
        output_root=tmp_path / "output",
        metadata_root=tmp_path / "metadata",
        csv_path=None,
    )
    s = samples[0]
    assert s["curated"]["tissue"] == "thallus"
    assert s["curated"]["antibody_target"] == "H3K4me3"


def test_render_html_produces_valid_html_skeleton(tmp_path):
    samples = [
        {"accession": "SRX1", "status": "ok", "strategy": "ChIP-Seq",
         "mapping_rate": "0.92", "peaks_q10": "300", "curated": {"tissue": "thallus"}},
        {"accession": "SRX2", "status": "failed", "strategy": "Bisulfite-Seq",
         "exit_code": "3", "log_snippet": "boom", "curated": {}},
    ]
    html = render_html(samples)
    assert html.startswith("<!DOCTYPE html>")
    assert "SRX1" in html and "SRX2" in html
    assert "thallus" in html
    assert "boom" in html  # failure snippet appears
    assert "</html>" in html
