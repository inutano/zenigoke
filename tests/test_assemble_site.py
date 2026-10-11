"""scripts/assemble-site.sh: the Pages site = web UI at the root + catalog pages at their paths."""
from __future__ import annotations

import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "assemble-site.sh"
WORKFLOW = REPO / ".github" / "workflows" / "pages.yml"


def _report(tmp_path: pathlib.Path) -> pathlib.Path:
    rep = tmp_path / "report"
    for rel, text in {
        "index.html": "<title>zenigoke catalog</title>",
        "methods.html": "methods", "enrichment.html": "enrichment",
        "samples/SRX1.html": "sample", "assets/style.css": "css",
        "data/axes.json": "{}", "bundles/b1.json": "{}",
    }.items():
        (rep / rel).parent.mkdir(parents=True, exist_ok=True)
        (rep / rel).write_text(text)
    (rep / "output").symlink_to(tmp_path)          # like the local report/output link
    return rep


def _catalog(tmp_path: pathlib.Path, schema="zenigoke-catalog-index/0.2") -> pathlib.Path:
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({"schema": schema, "samples": [{"acc": "SRX1"}]}))
    return p


def _run(*args):
    return subprocess.run(["bash", str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=60)


def test_web_ui_at_root_and_catalog_pages_kept(tmp_path):
    out = tmp_path / "site"
    r = _run(_report(tmp_path), _catalog(tmp_path), out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out / "index.html").read_text() == (REPO / "web" / "index.html").read_text()
    assert (out / "classic.html").read_text() == "<title>zenigoke catalog</title>"
    for rel in ("methods.html", "enrichment.html", "samples/SRX1.html", "assets/style.css", "data/axes.json",
                "css/app.css", "js/app.js", "js/core.js", "js/viz.js",
                "data/basemap.json", "data/MpTak_v7.1.igv.json"):
        assert (out / rel).is_file(), rel
    assert json.loads((out / "data" / "catalog.json").read_text())["samples"] == [{"acc": "SRX1"}]
    assert not (out / "output").exists() and not (out / "output").is_symlink()
    assert not (out / "bundles").exists()


def test_rejects_a_file_that_is_not_the_web_index(tmp_path):
    out = tmp_path / "site"
    r = _run(_report(tmp_path), _catalog(tmp_path, schema="kknmsmd-axes"), out)
    assert r.returncode != 0 and not out.exists()


def test_refuses_an_existing_output_dir(tmp_path):
    out = tmp_path / "site"
    out.mkdir()
    r = _run(_report(tmp_path), _catalog(tmp_path), out)
    assert r.returncode != 0 and list(out.iterdir()) == []


def test_requires_the_built_report(tmp_path):
    r = _run(tmp_path / "missing", _catalog(tmp_path), tmp_path / "site")
    assert r.returncode != 0 and "build-catalog-pages.py" in r.stdout


def test_pages_workflow_publishes_the_assembled_site():
    wf = WORKFLOW.read_text()
    assert "db/$f" in wf and "kknmsmd.db catalog.json" in wf
    assert "bash scripts/assemble-site.sh report db/catalog.json site" in wf
    assert "publish_dir: ./site" in wf and "publish_dir: ./report" not in wf
    for path in ('"scripts/assemble-site.sh"', '"web/index.html"', '"web/css/**"', '"web/js/**"', '"web/data/**"'):
        assert path in wf, path
