# Zenigoke Phase 5 Implementation Plan — enrichment analysis on EC2

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add `POST /api/enrichment` to the FastAPI app (bedtools intersect + binomial test against the 157 catalogued experiments) and a `/enrichment.html` page on the static frontend. Deploy on a t3.small EC2 in Tokyo at `zenigoke.inutano.com` with caddy + Let's Encrypt.

**Architecture:** Existing FastAPI app gains one endpoint. Existing matrix.js gains a new page. New EC2 setup scripts in `deploy/aws/`. Cost: ~$15/mo.

**Tech stack:** scipy (binom.sf + BH), bedtools (already used in Phase 3), caddy, systemd, aws-cli.

**Reference spec:** `docs/superpowers/specs/2026-05-20-zenigoke-phase5-enrichment.md`

---

## File structure

| Path | What |
|---|---|
| `scripts/api_enrichment.py` | NEW. `/api/enrichment` endpoint, bedtools intersect per experiment, binomial + BH. |
| `scripts/server.py` | MODIFY. Include the new router. |
| `scripts/build_static_data.py` | MODIFY. Also write `data/enrichment-meta.json` (per-experiment p_null + accession metadata) at build time. |
| `scripts/build_catalog_pages.py` | MODIFY. Add a `render_enrichment_page()` + nav link. |
| `report/assets/enrichment.js` | NEW. Upload form + result table + integration with matrix.js track builder. |
| `report/assets/matrix.css` | MODIFY. A few rules for the enrichment table. |
| `tests/test_api_enrichment.py` | NEW. Unit tests covering the algorithm + caching. |
| `requirements.txt` | MODIFY. Add scipy. |
| `deploy/aws/03-launch-ec2.sh` | NEW. Idempotent EC2 + EIP + SG launch via aws cli. |
| `deploy/aws/04-ec2-bootstrap.sh` | NEW. Run on the EC2: apt deps, clone, sync peaks from S3, caddy, systemd. |
| `deploy/Caddyfile` | MODIFY. Use `zenigoke.inutano.com` instead of placeholder. |
| `deploy/s3-cors.json` | MODIFY (if needed) — frontend stays on Pages so existing rules cover. |

---

## Task 1: `/api/enrichment` endpoint (algorithm + tests)

**Files:** create `scripts/api_enrichment.py`, `tests/test_api_enrichment.py`; modify `scripts/server.py`, `requirements.txt`.

- [ ] **Step 1: Add scipy to requirements.txt**

```bash
cd /home/inutano/work/zenigoke
grep -q "^scipy" requirements.txt || echo "scipy==1.13.*" >> requirements.txt
python3 -m pip install --user scipy
python3 -c "from scipy.stats import binom; print(binom.sf(5, 100, 0.1))"
```

Expected: a small float, e.g. `0.9763...`.

- [ ] **Step 2: Write failing test `tests/test_api_enrichment.py`**

```python
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
```

- [ ] **Step 3: Run; expect failures**

```bash
cd /home/inutano/work/zenigoke
python3 -m pytest tests/test_api_enrichment.py -v
```

Expect ModuleNotFoundError or similar.

- [ ] **Step 4: Write `scripts/api_enrichment.py`**

```python
"""POST /api/enrichment — enrichment of user-supplied BED regions against
catalogued ChIP/ATAC/BS-Seq experiments.

Algorithm:
  For each experiment:
    k = bedtools intersect -u -a user.bed -b sample.peaks | wc -l
    p_null = total_peak_bp / genome_bp
    p_value = scipy.stats.binom.sf(k - 1, n_user, p_null)
  BH correction across all tested experiments yields q_values.
  Sort by q_value ascending.
"""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from scipy.stats import binom

from api_axes import _db

router = APIRouter()

QCutoff = Literal["1e-5", "1e-10", "1e-20"]
MAX_REGIONS = 50000


class EnrichmentFilter(BaseModel):
    strategy: Optional[List[str]] = None


class EnrichmentRequest(BaseModel):
    regions_bed: str = Field(..., min_length=1)
    q_cutoff: QCutoff = "1e-10"
    filter: Optional[EnrichmentFilter] = None


# ---------------------------------------------------------------------------
# Genome / peak file accessors
# ---------------------------------------------------------------------------

def _peaks_dir() -> pathlib.Path:
    p = os.getenv("ZENIGOKE_PEAKS_DIR")
    if p:
        return pathlib.Path(p)
    return pathlib.Path(os.getenv("ZENIGOKE_REPORT_DIR", "report")) / "output"


def _chrom_sizes_path() -> pathlib.Path:
    p = os.getenv("ZENIGOKE_CHROM_SIZES")
    if p:
        return pathlib.Path(p)
    return pathlib.Path("/data1/zenigoke/references/MpTak_v7.1/chrom.sizes")


def _genome_bp() -> int:
    """Sum of chrom sizes; cached at module level once computed."""
    if hasattr(_genome_bp, "_cached"):
        return _genome_bp._cached
    total = 0
    p = _chrom_sizes_path()
    if not p.exists():
        return 0
    for line in p.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].isdigit():
            total += int(parts[1])
    _genome_bp._cached = total
    return total


def _q_label(q: str) -> str:
    return {"1e-5": "05", "1e-10": "10", "1e-20": "20"}[q]


def _peak_file_for(sample: dict, q_cutoff: str) -> pathlib.Path | None:
    acc = sample["accession"]
    strat = sample["library_strategy"]
    peaks_root = _peaks_dir()
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        sub = "chipseq" if strat == "ChIP-Seq" else "atacseq"
        return peaks_root / sub / acc / f"{acc}.{_q_label(q_cutoff)}_peaks.narrowPeak"
    if strat == "Bisulfite-Seq":
        return peaks_root / "bsseq" / acc / f"{acc}.CpG.hmr.bed"
    return None


def _total_peak_bp(peaks: pathlib.Path) -> int:
    n = 0
    if not peaks.exists():
        return 0
    for line in peaks.read_text().splitlines():
        if not line or line.startswith(("track", "#", "browser")):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            n += int(parts[2]) - int(parts[1])
        except (ValueError, IndexError):
            continue
    return n


# ---------------------------------------------------------------------------
# Single-experiment scoring (worker-safe)
# ---------------------------------------------------------------------------

def _score_one(args: tuple) -> dict | None:
    sample, q_cutoff, user_bed_path, n_user, genome_bp = args
    peak_path = _peak_file_for(sample, q_cutoff)
    if peak_path is None or not peak_path.exists():
        return None
    peak_bp = _total_peak_bp(peak_path)
    if peak_bp == 0 or genome_bp == 0:
        return None
    p_null = peak_bp / genome_bp
    try:
        out = subprocess.run(
            ["bedtools", "intersect", "-u", "-a", user_bed_path, "-b", str(peak_path)],
            check=True, capture_output=True, timeout=60,
        )
    except subprocess.CalledProcessError:
        return None
    k = sum(1 for ln in out.stdout.splitlines() if ln.strip())
    if k == 0:
        p_value = 1.0
    else:
        p_value = float(binom.sf(k - 1, n_user, p_null))
    fold = (k / n_user) / p_null if p_null > 0 else 0.0
    return {
        "accession": sample["accession"],
        "library_strategy": sample["library_strategy"],
        "antibody_target": sample.get("antibody_target"),
        "genotype_strain": sample.get("genotype_strain"),
        "developmental_stage": sample.get("developmental_stage"),
        "overlap_count": k,
        "p_null": p_null,
        "fold_enrichment": fold,
        "p_value": p_value,
    }


# ---------------------------------------------------------------------------
# BH correction
# ---------------------------------------------------------------------------

def _bh(p_values: list[float]) -> list[float]:
    n = len(p_values)
    if n == 0:
        return []
    indexed = sorted(range(n), key=lambda i: p_values[i])
    q = [0.0] * n
    prev = 1.0
    for rank, idx in enumerate(reversed(indexed)):
        i = n - rank
        adj = min(prev, p_values[idx] * n / i)
        q[idx] = adj
        prev = adj
    return q


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _hash_payload(req: EnrichmentRequest) -> str:
    h = hashlib.sha256()
    h.update(req.regions_bed.encode())
    h.update(req.q_cutoff.encode())
    if req.filter and req.filter.strategy:
        h.update(",".join(sorted(req.filter.strategy)).encode())
    return h.hexdigest()[:16]


_CACHE: dict[str, dict] = {}


def _load_samples(strategies: list[str] | None) -> list[dict]:
    import sqlite3
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        if strategies:
            placeholder = ",".join("?" for _ in strategies)
            rows = conn.execute(f"""
                SELECT s.accession, s.library_strategy, s.status,
                       c.antibody_target, c.genotype_strain, c.developmental_stage
                FROM sample s LEFT JOIN sample_curation c USING (accession)
                WHERE s.status='ok' AND s.library_strategy IN ({placeholder})
                ORDER BY s.accession
            """, strategies).fetchall()
        else:
            rows = conn.execute("""
                SELECT s.accession, s.library_strategy, s.status,
                       c.antibody_target, c.genotype_strain, c.developmental_stage
                FROM sample s LEFT JOIN sample_curation c USING (accession)
                WHERE s.status='ok' ORDER BY s.accession
            """).fetchall()
    return [dict(r) for r in rows]


def _parse_bed(text: str) -> list[tuple[str, int, int]]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("track", "#", "browser")):
            continue
        parts = ln.split("\t") if "\t" in ln else ln.split()
        if len(parts) < 3:
            continue
        try:
            out.append((parts[0], int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    return out


def run_enrichment(req: EnrichmentRequest) -> dict:
    regions = _parse_bed(req.regions_bed)
    if not regions:
        raise ValueError("no valid BED regions in input")
    if len(regions) > MAX_REGIONS:
        raise ValueError(f"too many regions ({len(regions)} > {MAX_REGIONS})")

    cache_key = _hash_payload(req)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    strategies = req.filter.strategy if (req.filter and req.filter.strategy) else None
    samples = _load_samples(strategies)
    genome_bp = _genome_bp()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".bed", delete=False) as tf:
        for chrom, start, end in regions:
            tf.write(f"{chrom}\t{start}\t{end}\n")
        user_bed_path = tf.name

    try:
        n_user = len(regions)
        args = [(s, req.q_cutoff, user_bed_path, n_user, genome_bp) for s in samples]
        results: list[dict] = []
        # Sequential is fine for small catalogs; switch to ProcessPoolExecutor
        # for >50 samples (test env stays sequential to avoid pickling fastapi).
        for a in args:
            r = _score_one(a)
            if r is not None:
                results.append(r)
    finally:
        try:
            os.unlink(user_bed_path)
        except OSError:
            pass

    # BH correction
    if results:
        ps = [r["p_value"] for r in results]
        qs = _bh(ps)
        for r, q in zip(results, qs):
            r["q_value"] = q
        results.sort(key=lambda r: r["q_value"])

    payload = {
        "n_user_regions": n_user,
        "n_experiments_tested": len(results),
        "genome_bp": genome_bp,
        "results": results,
    }
    _CACHE[cache_key] = payload
    return payload


@router.post("/api/enrichment")
def enrichment_endpoint(req: EnrichmentRequest) -> dict:
    try:
        return run_enrichment(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 5: Wire into `scripts/server.py`**

Add after the bundle_router include:

```python
from api_enrichment import router as enrichment_router
app.include_router(enrichment_router)
```

- [ ] **Step 6: Run the test**

```bash
python3 -m pytest tests/test_api_enrichment.py -v
```

Expected: 5/5 PASS. If `binom.sf` returns slightly different values from a hand calc, that's fine — just confirm p < 0.05 in the known-overlap test.

- [ ] **Step 7: Commit**

```bash
git add scripts/api_enrichment.py scripts/server.py tests/test_api_enrichment.py requirements.txt
git commit -m "scripts+tests: add /api/enrichment endpoint (Phase 5 Task 1)"
```

---

## Task 2: Static `enrichment.html` page

**Files:** modify `scripts/build_catalog_pages.py` (add render_enrichment + nav), create `report/assets/enrichment.js`, modify `report/assets/matrix.css`.

- [ ] **Step 1: Add the nav link**

Edit the `NAV_LINKS` (or equivalent in `build_catalog_pages.py`) to include `("enrichment", "Enrichment", "enrichment.html")` between Methods and About.

- [ ] **Step 2: Add `render_enrichment_page()` to `scripts/build_catalog_pages.py`**

```python
def render_enrichment_page() -> str:
    parts: list[str] = []
    parts.append(_page_header("Enrichment — zenigoke"))
    parts.append(_nav_from_root(active="enrichment"))
    parts.append("""<div class='container'>
  <h1>Enrichment analysis</h1>
  <p class='subtitle'>Score your regions of interest against the catalog's ChIP/ATAC/BS-Seq peak sets.</p>

  <div class='card'>
    <h2>1. Upload regions (BED)</h2>
    <textarea id='bed-input' rows='6'
              placeholder='chr1<TAB>1234<TAB>5678&#10;chr1<TAB>9000<TAB>12000&#10;…'
              style='width:100%;font:11px ui-monospace,monospace'></textarea>
    <p class='label'>Or drop a .bed file here:</p>
    <input id='bed-file' type='file' accept='.bed,.txt'>
  </div>

  <div class='card'>
    <h2>2. Options</h2>
    <label>q-cutoff: <select id='q-cutoff'>
      <option value='1e-5'>1e-5</option>
      <option value='1e-10' selected>1e-10</option>
      <option value='1e-20'>1e-20</option>
    </select></label>
    &nbsp;&nbsp;
    Strategies:
    <label><input type='checkbox' class='strat' value='ChIP-Seq' checked> ChIP-Seq</label>
    <label><input type='checkbox' class='strat' value='ATAC-Seq' checked> ATAC-Seq</label>
    <label><input type='checkbox' class='strat' value='Bisulfite-Seq' checked> BS-Seq</label>
  </div>

  <div class='card'>
    <button id='run-btn'>&#9654; Run enrichment</button>
    <span id='run-status'></span>
  </div>

  <div class='card' id='results-card' style='display:none'>
    <h2>3. Results</h2>
    <p class='label' id='results-summary'></p>
    <div style='overflow-x:auto'>
      <table id='results-table'>
        <thead><tr>
          <th data-col='rank'>#</th>
          <th data-col='accession'>accession</th>
          <th data-col='antibody'>antibody</th>
          <th data-col='strain'>strain</th>
          <th data-col='stage'>stage</th>
          <th data-col='overlap'>overlap</th>
          <th data-col='fold'>fold</th>
          <th data-col='p'>p-value</th>
          <th data-col='q'>q-value</th>
          <th></th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <p>
      <button id='top10-igv'>&#9654; Open top 10 in IGV</button>
      <button id='download-csv'>&darr; Download CSV</button>
    </p>
  </div>
</div>
<script>window.ZENIGOKE_API_BASE = window.ZENIGOKE_API_BASE || '';</script>
<script src='assets/matrix.js' defer></script>
<script src='assets/enrichment.js' defer></script>
""")
    parts.append(_page_footer())
    return "".join(parts)


# In write_pages, add:
(out_dir / "enrichment.html").write_text(render_enrichment_page())
```

Note the new env var `ZENIGOKE_API_BASE` is the EC2 API URL (e.g. `https://zenigoke.inutano.com`). Injected by build at deploy time. When unset (local dev), the frontend hits same-origin.

- [ ] **Step 3: Inject `ZENIGOKE_API_BASE` similar to the existing `ZENIGOKE_DATA_BASE`**

In `build_catalog_pages.py`'s `_data_base_inline_script()` (or a sibling helper), also emit:

```python
def _api_base_inline_script() -> str:
    base = os.getenv("ZENIGOKE_API_BASE", "").rstrip("/")
    if not base:
        return ""
    safe = base.replace("'", "")
    return f"<script>window.ZENIGOKE_API_BASE='{safe}';</script>\n"
```

Call it in `render_enrichment_page` BEFORE the matrix.js script tag, and also in `render_index` so the matrix page knows the API base.

- [ ] **Step 4: Write `report/assets/enrichment.js`**

```javascript
"use strict";

const API = (window.ZENIGOKE_API_BASE || "").replace(/\/$/, "");

const ui = {
  bed:    () => document.getElementById("bed-input"),
  file:   () => document.getElementById("bed-file"),
  q:      () => document.getElementById("q-cutoff"),
  strats: () => Array.from(document.querySelectorAll(".strat:checked")).map(e => e.value),
  runBtn: () => document.getElementById("run-btn"),
  status: () => document.getElementById("run-status"),
  card:   () => document.getElementById("results-card"),
  summary: () => document.getElementById("results-summary"),
  tbody:  () => document.querySelector("#results-table tbody"),
  top10:  () => document.getElementById("top10-igv"),
  csv:    () => document.getElementById("download-csv"),
};

let lastResults = null;

function init() {
  ui.runBtn().addEventListener("click", run);
  ui.file().addEventListener("change", async e => {
    const f = e.target.files[0]; if (!f) return;
    ui.bed().value = await f.text();
  });
  ui.top10().addEventListener("click", () => sendTopToIGV(10));
  ui.csv().addEventListener("click", downloadCsv);
}

async function run() {
  const body = {
    regions_bed: ui.bed().value,
    q_cutoff: ui.q().value,
    filter: {strategy: ui.strats()},
  };
  if (!body.regions_bed.trim()) {
    ui.status().textContent = " · paste or load a BED first";
    return;
  }
  ui.runBtn().disabled = true;
  ui.status().textContent = " · running…";
  try {
    const r = await fetch(`${API}/api/enrichment`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({detail: r.status}));
      throw new Error(err.detail || `${r.status}`);
    }
    const data = await r.json();
    render(data);
    ui.status().textContent = ` · ${data.n_experiments_tested} experiments tested`;
  } catch (e) {
    ui.status().textContent = ` · failed: ${e.message}`;
  } finally {
    ui.runBtn().disabled = false;
  }
}

function render(data) {
  lastResults = data;
  ui.card().style.display = "";
  ui.summary().textContent =
    `${data.n_user_regions} input regions × ${data.n_experiments_tested} experiments`;
  const tbody = ui.tbody();
  tbody.innerHTML = "";
  data.results.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i+1}</td>
      <td><a href="samples/${r.accession}.html">${r.accession}</a></td>
      <td>${r.antibody_target || "&mdash;"}</td>
      <td>${r.genotype_strain || "&mdash;"}</td>
      <td>${r.developmental_stage || "&mdash;"}</td>
      <td>${r.overlap_count}</td>
      <td>${r.fold_enrichment.toFixed(2)}</td>
      <td>${r.p_value.toExponential(2)}</td>
      <td>${r.q_value.toExponential(2)}</td>
      <td><a href="#" data-acc="${r.accession}" data-strat="${r.library_strategy}" class="row-igv">&#9654; IGV</a></td>`;
    tbody.appendChild(tr);
  });
  for (const a of tbody.querySelectorAll("a.row-igv")) {
    a.addEventListener("click", e => {
      e.preventDefault();
      const acc = a.dataset.acc, strat = a.dataset.strat;
      sendOneToIGV(acc, strat);
    });
  }
}

function sendOneToIGV(acc, strat) {
  const tracks = window.tracksForAccession(acc, {strategy: strat, q_cutoff: ui.q().value}, "#3060a0");
  const param = tracks.map(t => t.url + "|" + t.name).join(",");
  fetch("http://localhost:60151/load?file=" + encodeURIComponent(param))
    .catch(e => alert("Could not reach IGV at :60151."));
}

function sendTopToIGV(n) {
  if (!lastResults) return;
  const palette = ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"];
  const all = [];
  for (let i = 0; i < Math.min(n, lastResults.results.length); i++) {
    const r = lastResults.results[i];
    const tracks = window.tracksForAccession(r.accession,
      {strategy: r.library_strategy, q_cutoff: ui.q().value},
      palette[i % palette.length]);
    all.push(...tracks);
  }
  const param = all.map(t => t.url + "|" + t.name).join(",");
  fetch("http://localhost:60151/load?file=" + encodeURIComponent(param))
    .catch(e => alert("Could not reach IGV at :60151."));
}

function downloadCsv() {
  if (!lastResults) return;
  const hdr = ["rank","accession","library_strategy","antibody_target","genotype_strain",
               "developmental_stage","overlap_count","fold_enrichment","p_value","q_value"];
  const rows = lastResults.results.map((r, i) => [
    i+1, r.accession, r.library_strategy, r.antibody_target || "",
    r.genotype_strain || "", r.developmental_stage || "",
    r.overlap_count, r.fold_enrichment.toFixed(4),
    r.p_value.toExponential(4), r.q_value.toExponential(4),
  ]);
  const csv = [hdr.join(","), ...rows.map(r => r.join(","))].join("\n");
  const blob = new Blob([csv], {type: "text/csv"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "enrichment.csv";
  a.click();
}

init();
```

`window.tracksForAccession` is exposed by matrix.js (it's already a top-level function in that file; just make sure it's not closed over).

- [ ] **Step 5: Expose `tracksForAccession` on `window` in matrix.js**

In `report/assets/matrix.js`, at the bottom near `init()`, add:

```javascript
window.tracksForAccession = tracksForAccession;
window.resolveStrategy = resolveStrategy;
```

- [ ] **Step 6: Add a tiny CSS rule for the results table**

In `report/assets/matrix.css`, append:

```css
#results-table { border-collapse: collapse; font-size: 12px; width: 100%; }
#results-table th, #results-table td { border: 1px solid #ddd; padding: 4px 8px; }
#results-table thead th { background: #f4f4f4; }
```

- [ ] **Step 7: Regenerate + manual smoke**

```bash
cd /home/inutano/work/zenigoke
python3 scripts/build-catalog-pages.py 2>&1 | tail -2
ls report/enrichment.html
grep -c "tracksForAccession" report/assets/matrix.js   # expect 2+ (assignment + def)
```

- [ ] **Step 8: Commit**

```bash
git add scripts/build_catalog_pages.py report/assets/enrichment.js report/assets/matrix.js report/assets/matrix.css report/enrichment.html
git commit -m "frontend: add enrichment.html page (Phase 5 Task 2)"
```

---

## Task 3: CORS update + API base injection at workflow time

**Files:** modify `.github/workflows/pages.yml` (add `ZENIGOKE_API_BASE` env at build time), modify `scripts/server.py` (add `zenigoke.inutano.com` and Pages origin to allowed CORS).

- [ ] **Step 1: Update `.github/workflows/pages.yml`**

Add `ZENIGOKE_API_BASE: "https://zenigoke.inutano.com"` to the `Build static catalog` step's `env`.

- [ ] **Step 2: Update CORS in `scripts/server.py`**

The existing CORS reads from `ZENIGOKE_CORS_ORIGIN` env var. No code change — just document that on the EC2 we set:

```
ZENIGOKE_CORS_ORIGIN=https://inutano.github.io
```

If multiple origins are needed later, refactor the middleware to accept a comma-separated list:

```python
origins = [o.strip() for o in CORS_ORIGIN.split(",") if o.strip()] if CORS_ORIGIN != "*" else ["*"]
app.add_middleware(CORSMiddleware, allow_origins=origins, ...)
```

Apply that refactor now (one line change).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/pages.yml scripts/server.py
git commit -m "deploy: wire ZENIGOKE_API_BASE + CORS allowlist for Pages → EC2 (Phase 5 Task 3)"
```

---

## Task 4: EC2 launch script

**Files:** create `deploy/aws/03-launch-ec2.sh`.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Launch (or reuse) an EC2 t3.small for the zenigoke API.
# Idempotent: re-running is safe and prints the existing instance's EIP.
set -euo pipefail

REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
KEY_NAME="${ZENIGOKE_KEY_NAME:-zenigoke}"
SG_NAME="${ZENIGOKE_SG_NAME:-zenigoke-api}"
INST_TAG="${ZENIGOKE_INST_TAG:-zenigoke-api}"
INST_TYPE="${ZENIGOKE_INST_TYPE:-t3.small}"

# Ubuntu 22.04 LTS AMI in ap-northeast-1 (Canonical official; update if EOL)
AMI="${ZENIGOKE_AMI:-ami-0d52744d6551d851e}"

# 1. SSH key pair
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" >/dev/null 2>&1; then
  echo "Creating key pair $KEY_NAME → ~/.ssh/${KEY_NAME}.pem"
  aws ec2 create-key-pair --region "$REGION" --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$HOME/.ssh/${KEY_NAME}.pem"
  chmod 600 "$HOME/.ssh/${KEY_NAME}.pem"
fi

# 2. Security group
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  echo "Creating security group $SG_NAME"
  SG_ID=$(aws ec2 create-security-group --region "$REGION" \
    --group-name "$SG_NAME" --description "zenigoke API" \
    --query 'GroupId' --output text)
  MY_IP=$(curl -s https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 22 --cidr "${MY_IP}/32"
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
fi
echo "Security group: $SG_ID"

# 3. Instance
INST_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$INST_TAG" "Name=instance-state-name,Values=running,pending,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)
if [ -z "$INST_ID" ] || [ "$INST_ID" = "None" ]; then
  echo "Launching $INST_TYPE ($AMI)"
  INST_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI" --instance-type "$INST_TYPE" \
    --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INST_TAG}]" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3}' \
    --query 'Instances[0].InstanceId' --output text)
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$INST_ID"
fi
echo "Instance: $INST_ID"

# 4. Elastic IP
EIP=$(aws ec2 describe-addresses --region "$REGION" \
  --filters "Name=tag:Name,Values=$INST_TAG" \
  --query 'Addresses[0].PublicIp' --output text 2>/dev/null || true)
if [ -z "$EIP" ] || [ "$EIP" = "None" ]; then
  ALLOC_ID=$(aws ec2 allocate-address --region "$REGION" --domain vpc \
    --query 'AllocationId' --output text)
  aws ec2 create-tags --region "$REGION" --resources "$ALLOC_ID" \
    --tags "Key=Name,Value=$INST_TAG"
  aws ec2 associate-address --region "$REGION" \
    --instance-id "$INST_ID" --allocation-id "$ALLOC_ID"
  EIP=$(aws ec2 describe-addresses --region "$REGION" \
    --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)
fi

echo ""
echo "=== READY ==="
echo "  Instance ID:  $INST_ID"
echo "  Public IP:    $EIP"
echo "  SSH:          ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${EIP}"
echo ""
echo "Next steps:"
echo "  1. In your DNS, point zenigoke.inutano.com → ${EIP} (A record)"
echo "  2. After DNS propagates, SSH in and run:"
echo "     bash deploy/aws/04-ec2-bootstrap.sh"
```

- [ ] **Step 2: Commit**

```bash
chmod +x deploy/aws/03-launch-ec2.sh
git add deploy/aws/03-launch-ec2.sh
git commit -m "deploy: add idempotent EC2 launch script (Phase 5 Task 4)"
```

---

## Task 5: EC2 bootstrap script (run on the VM)

**Files:** create `deploy/aws/04-ec2-bootstrap.sh`, update `deploy/Caddyfile`, update `deploy/zenigoke.service`.

- [ ] **Step 1: Update `deploy/Caddyfile`** to use the real domain

```caddyfile
zenigoke.inutano.com {
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

- [ ] **Step 2: Write `deploy/aws/04-ec2-bootstrap.sh`**

```bash
#!/usr/bin/env bash
# Run this ONCE on the EC2 instance after it's launched and DNS is set.
# Idempotent: safe to re-run.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/inutano/zenigoke.git}"
BUCKET="${ZENIGOKE_BUCKET:-zenigoke-catalog}"
REGION="${ZENIGOKE_REGION:-ap-northeast-1}"
DOMAIN="${ZENIGOKE_DOMAIN:-zenigoke.inutano.com}"

# Wait for DNS to point at this instance (so caddy can get cert)
echo "== waiting for DNS $DOMAIN to point at this host =="
MY_IP=$(curl -s https://checkip.amazonaws.com)
for i in $(seq 1 30); do
  RESOLVED=$(dig +short "$DOMAIN" | tail -n1)
  if [ "$RESOLVED" = "$MY_IP" ]; then
    echo "  resolved: $RESOLVED"
    break
  fi
  echo "  attempt $i/30: DNS not yet propagated (got $RESOLVED, want $MY_IP)"
  sleep 10
done

echo "== installing system packages =="
sudo apt-get update -qq
sudo apt-get install -y \
  python3-pip python3-scipy bedtools git awscli \
  debian-keyring debian-archive-keyring apt-transport-https curl
# caddy from official repo
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -qq
sudo apt-get install -y caddy

echo "== cloning repo =="
cd "$HOME"
if [ ! -d zenigoke ]; then
  git clone "$REPO_URL"
fi
cd zenigoke
git pull --ff-only
pip install --user -r requirements.txt

echo "== syncing peak files from S3 (≈400 MB) =="
mkdir -p "$HOME/zenigoke-data"
aws s3 sync "s3://$BUCKET/output" "$HOME/zenigoke-data" \
  --region "$REGION" \
  --exclude "*.bw" --exclude "*.bb" --exclude "*.bedgraph"
# Also need the DB
mkdir -p db
aws s3 cp "s3://$BUCKET/db/kknmsmd.db" db/kknmsmd.db --region "$REGION"
# And chrom.sizes (needed by enrichment for genome_bp)
mkdir -p references/MpTak_v7.1
aws s3 cp "s3://$BUCKET/references/MpTak_v7.1/chrom.sizes" \
  references/MpTak_v7.1/chrom.sizes --region "$REGION" 2>/dev/null || \
  echo "  WARN: chrom.sizes not in S3 — upload separately"

echo "== writing /etc/zenigoke.env =="
sudo tee /etc/zenigoke.env > /dev/null <<EOF
ZENIGOKE_DB_PATH=$HOME/zenigoke/db/kknmsmd.db
ZENIGOKE_REPORT_DIR=$HOME/zenigoke/report
ZENIGOKE_BUNDLES_DIR=$HOME/zenigoke/report/bundles
ZENIGOKE_PEAKS_DIR=$HOME/zenigoke-data
ZENIGOKE_CHROM_SIZES=$HOME/zenigoke/references/MpTak_v7.1/chrom.sizes
ZENIGOKE_PUBLIC_BASE=https://${DOMAIN}
ZENIGOKE_BUNDLES_PUBLIC=https://${BUCKET}.s3.${REGION}.amazonaws.com/bundles
ZENIGOKE_CORS_ORIGIN=https://inutano.github.io
EOF

echo "== installing systemd unit =="
sudo cp deploy/zenigoke.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zenigoke

echo "== installing Caddyfile =="
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy

echo ""
echo "=== DONE ==="
echo "  https://${DOMAIN}/api/axes  ← test"
echo "  systemctl status zenigoke caddy"
```

- [ ] **Step 3: Update `deploy/zenigoke.service`** to be more robust (`-u` flag for unbuffered, plus point to the right uvicorn path)

```ini
[Unit]
Description=Zenigoke catalog (FastAPI + uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/zenigoke
EnvironmentFile=/etc/zenigoke.env
ExecStart=/home/ubuntu/.local/bin/uvicorn server:app --host 127.0.0.1 --port 8088 --app-dir scripts
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Commit**

```bash
chmod +x deploy/aws/04-ec2-bootstrap.sh
git add deploy/aws/04-ec2-bootstrap.sh deploy/Caddyfile deploy/zenigoke.service
git commit -m "deploy: EC2 bootstrap script + caddy/systemd for zenigoke.inutano.com (Phase 5 Task 5)"
```

---

## Task 6: Update aws/README.md with the EC2 + enrichment flow

**Files:** modify `deploy/aws/README.md`.

- [ ] **Step 1: Append a new "Adding enrichment (mode C)" section**

Document the launch + DNS + bootstrap sequence. ~20 lines.

- [ ] **Step 2: Commit**

```bash
git add deploy/aws/README.md
git commit -m "docs(deploy): document EC2 + enrichment setup (Phase 5 Task 6)"
```

---

## Task 7: End-to-end smoke (manual)

After all six tasks committed:

- [ ] **Step 1: Local check — server up + new endpoint reachable**

```bash
cd /home/inutano/work/zenigoke
[ -f /tmp/zenigoke-server.pid ] && kill $(cat /tmp/zenigoke-server.pid) 2>/dev/null
sleep 2
nohup python3 scripts/server.py > /tmp/zenigoke-server.log 2>&1 &
echo $! > /tmp/zenigoke-server.pid
sleep 3
curl -sI http://localhost:8088/enrichment.html | head -1     # expect 200
curl -s -X POST http://localhost:8088/api/enrichment \
  -H 'Content-Type: application/json' \
  -d "{\"regions_bed\":\"chr1\t1000000\t1010000\nchr1\t2000000\t2010000\",\"q_cutoff\":\"1e-10\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'n_user={d[\"n_user_regions\"]} tested={d[\"n_experiments_tested\"]}')
for r in d['results'][:5]:
    print(f'  {r[\"accession\"]:14} {r[\"antibody_target\"]:12} q={r[\"q_value\"]:.2e}')
"
```

- [ ] **Step 2: Run the test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: ≥51 tests (46 + 5 new).

- [ ] **Step 3: On AWS — launch + bootstrap**

(Manual, user runs these.) Document in the report; no commit needed for execution.

```bash
bash deploy/aws/03-launch-ec2.sh
# wait for output EIP, set DNS in inutano.com zone (manual, in user's DNS panel)
# ssh in:
ssh -i ~/.ssh/zenigoke.pem ubuntu@<EIP>
# inside the instance:
git clone https://github.com/<user>/zenigoke.git
cd zenigoke
bash deploy/aws/04-ec2-bootstrap.sh
# back on laptop: trigger Pages rebuild so the frontend has ZENIGOKE_API_BASE
git commit --allow-empty -m "trigger Pages rebuild for Phase 5 deploy"
git push origin main
```

- [ ] **Step 4: Browser smoke** — open `https://<user>.github.io/zenigoke/enrichment.html`, paste a small BED, click Run, confirm a table appears in <30 s.

---

## Self-review

### Spec coverage

| Spec section | Task |
|---|---|
| §1 goal — `/api/enrichment` endpoint | Task 1 |
| §1 goal — `/enrichment.html` page | Task 2 |
| §1 goal — EC2 deploy | Tasks 4, 5 |
| §1 goal — CORS update | Task 3 |
| §1 goal — deployment automation | Tasks 4–6 |
| §3 endpoint shape (req/resp, error codes, max regions) | Task 1 test cases |
| §4 frontend layout + behavior | Task 2 |
| §5 infra | Tasks 4–5 |
| §6 unit + integration tests | Task 1 |

### Placeholder scan

No "TBD" / "TODO" in plan steps. Each step has concrete code or commands.

### Type consistency

`EnrichmentRequest.q_cutoff` is `Literal["1e-5","1e-10","1e-20"]` consistent with the Phase 3 `BundleRequest.q_cutoff` pattern (post-fix from final review of Phase 3). Frontend passes the same string set. Test inputs match.

### Frontend integration

`window.tracksForAccession` exposed in matrix.js (Step 5 of Task 2) so enrichment.js can build IGV tracks without duplicating per-sample track logic.

### Known limitations to surface in the README

- bedtools wallclock for 157 samples is ~30s serial; if the t3.small struggles, switch to `t3.medium` or parallelize via `ProcessPoolExecutor` in `run_enrichment`.
- DNS propagation can add 5–30 min to the first deploy; the bootstrap script polls and waits.
- Re-syncing peak files when the catalog refreshes: re-run `04-ec2-bootstrap.sh` (idempotent).
