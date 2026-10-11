from __future__ import annotations
import pathlib, shutil, subprocess
import pytest

HERE = pathlib.Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_igv_js_unit_tests():
    r = subprocess.run(["node", str(HERE / "js" / "igv.test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_enrichment_js_unit_tests():
    r = subprocess.run(["node", str(HERE / "js" / "enrichment.test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
