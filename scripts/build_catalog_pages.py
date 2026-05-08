"""Generate static HTML catalog pages from the zenigoke SQLite catalog DB.

Stdlib + sqlite3 only.

Public API:
  render_index(samples: list[dict]) -> str
  render_sample(sample: dict) -> str
  render_strategy(strategy: str, samples: list[dict]) -> str
  render_summary(samples: list[dict]) -> str
  write_pages(db_path: pathlib.Path, out_dir: pathlib.Path) -> None
  main(argv=None) -> int
"""
from __future__ import annotations

import html
import pathlib
import shutil
import sqlite3
from typing import Any, Optional

# Strategy display names to URL slug mapping
STRATEGY_SLUGS: dict[str, str] = {
    "ChIP-Seq": "chipseq",
    "ATAC-Seq": "atacseq",
    "Bisulfite-Seq": "bsseq",
}

SLUG_TO_STRATEGY: dict[str, str] = {v: k for k, v in STRATEGY_SLUGS.items()}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _esc(x: Any) -> str:
    """HTML-escape any value, converting None to empty string."""
    if x is None:
        return ""
    return html.escape(str(x))


def _fmt(x: Any, decimals: int = 1) -> str:
    """Format a numeric value or return empty string."""
    if x is None or x == "":
        return ""
    try:
        return f"{float(x):.{decimals}f}"
    except (ValueError, TypeError):
        return str(x)


def _page_header(title: str, css_path: str = "assets/style.css") -> str:
    return (
        f"<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        f"<meta charset='utf-8'>\n"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"<title>{_esc(title)}</title>\n"
        f"<link rel='stylesheet' href='{css_path}'>\n"
        f"</head>\n<body>\n"
    )


def _page_footer() -> str:
    return "\n</body>\n</html>\n"


def _nav(active: str = "") -> str:
    links = [
        ("index.html", "All Samples"),
        ("strategy/chipseq.html", "ChIP-Seq"),
        ("strategy/atacseq.html", "ATAC-Seq"),
        ("strategy/bsseq.html", "BS-Seq"),
        ("summary.html", "Phase 1 Summary"),
    ]
    items = []
    for href, label in links:
        cls = ' class="active"' if label == active else ""
        items.append(f'<a href="{href}"{cls}>{_esc(label)}</a>')
    return "<nav>\n" + "\n".join(items) + "\n</nav>\n"


def _nav_from_sample(active: str = "") -> str:
    """Nav bar for pages inside samples/ subdirectory."""
    links = [
        ("../index.html", "All Samples"),
        ("../strategy/chipseq.html", "ChIP-Seq"),
        ("../strategy/atacseq.html", "ATAC-Seq"),
        ("../strategy/bsseq.html", "BS-Seq"),
        ("../summary.html", "Phase 1 Summary"),
    ]
    items = []
    for href, label in links:
        cls = ' class="active"' if label == active else ""
        items.append(f'<a href="{href}"{cls}>{_esc(label)}</a>')
    return "<nav>\n" + "\n".join(items) + "\n</nav>\n"


def _card(title: str, body: str) -> str:
    return (
        f'<div class="card">\n'
        f'<h2>{_esc(title)}</h2>\n'
        f'{body}\n'
        f'</div>\n'
    )


def _kv_table(rows: list[tuple[str, str]]) -> str:
    parts = ['<table class="kv">\n']
    for k, v in rows:
        parts.append(f"<tr><th>{_esc(k)}</th><td>{v}</td></tr>\n")
    parts.append("</table>\n")
    return "".join(parts)


def _summary_stats(samples: list[dict]) -> tuple[dict, int, int]:
    """Return (counts_by_strategy, n_ok, n_failed) from flat sample dicts."""
    by_strategy: dict[str, int] = {}
    n_ok = 0
    n_failed = 0
    for s in samples:
        strat = s.get("library_strategy") or "unknown"
        by_strategy[strat] = by_strategy.get(strat, 0) + 1
        if s.get("status") == "ok":
            n_ok += 1
        else:
            n_failed += 1
    return by_strategy, n_ok, n_failed


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

JS_FILTER = """<script>
const q = document.getElementById('q');
const rows = Array.from(document.querySelectorAll('#samples-table tbody tr'));
q.addEventListener('input', () => {
  const t = q.value.toLowerCase();
  for (const row of rows) {
    row.style.display = row.textContent.toLowerCase().includes(t) ? '' : 'none';
  }
});
</script>
"""


def render_index(samples: list[dict]) -> str:
    """Render the index.html page with all samples and a JS filter."""
    by_strategy, n_ok, n_failed = _summary_stats(samples)
    n_total = len(samples)

    parts: list[str] = []
    parts.append(_page_header("zenigoke catalog"))
    parts.append(_nav("All Samples"))

    parts.append('<div class="container">\n')
    parts.append("<h1>zenigoke catalog</h1>\n")

    # Summary stats card
    parts.append('<div class="card summary-card">\n')
    parts.append(f"<p><strong>{n_total}</strong> samples &mdash; "
                 f"<span class='ok'>{n_ok} ok</span>, "
                 f"<span class='failed'>{n_failed} failed</span></p>\n")
    strat_items = "".join(
        f"<span class='badge'>{_esc(k)}: {v}</span>"
        for k, v in sorted(by_strategy.items())
    )
    parts.append(f"<p>{strat_items}</p>\n")
    parts.append("</div>\n")

    # Filter input
    parts.append('<div class="filter-row">\n')
    parts.append('<input id="q" type="text" placeholder="filter&hellip;" autocomplete="off">\n')
    parts.append("</div>\n")

    # Samples table
    parts.append('<table id="samples-table">\n')
    parts.append("<thead>\n<tr>\n")
    for col in ["accession", "strategy", "status", "tissue", "dev_stage",
                "strain", "antibody", "mapping_rate", "elapsed_min", ""]:
        parts.append(f"<th>{_esc(col)}</th>\n")
    parts.append("</tr>\n</thead>\n<tbody>\n")

    for s in samples:
        acc = s.get("accession") or ""
        strat = s.get("library_strategy") or ""
        status = s.get("status") or ""
        tissue = s.get("tissue") or ""
        dev_stage = s.get("developmental_stage") or ""
        strain = s.get("genotype_strain") or ""
        antibody = s.get("antibody_target") or ""
        mapping = _fmt(s.get("mapping_rate"), 1)
        elapsed = _fmt(s.get("elapsed_min"), 1)

        status_class = "ok" if status == "ok" else "failed"
        sample_link = f'<a href="samples/{_esc(acc)}.html">&rarr;</a>'

        parts.append(f'<tr class="{status_class}">\n')
        parts.append(f'<td><a href="samples/{_esc(acc)}.html">{_esc(acc)}</a></td>\n')
        parts.append(f"<td>{_esc(strat)}</td>\n")
        parts.append(f'<td class="{status_class}">{_esc(status)}</td>\n')
        parts.append(f"<td>{_esc(tissue)}</td>\n")
        parts.append(f"<td>{_esc(dev_stage)}</td>\n")
        parts.append(f"<td>{_esc(strain)}</td>\n")
        parts.append(f"<td>{_esc(antibody)}</td>\n")
        parts.append(f"<td>{_esc(mapping)}</td>\n")
        parts.append(f"<td>{_esc(elapsed)}</td>\n")
        parts.append(f"<td>{sample_link}</td>\n")
        parts.append("</tr>\n")

    parts.append("</tbody>\n</table>\n")
    parts.append("</div>\n")  # .container

    parts.append(JS_FILTER)
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Per-sample page
# ---------------------------------------------------------------------------

def render_sample(sample: dict) -> str:
    """Render a single sample detail page."""
    acc = sample.get("accession") or ""
    strat = sample.get("library_strategy") or ""
    status = sample.get("status") or ""
    biosample = sample.get("biosample_accession")

    parts: list[str] = []
    css_path = "../assets/style.css"
    parts.append(_page_header(f"{acc} — zenigoke", css_path=css_path))
    parts.append(_nav_from_sample())

    parts.append('<div class="container">\n')

    # Title card
    title_body_parts: list[str] = ['<div class="title-meta">\n']
    title_body_parts.append(f"<h1>{_esc(acc)}</h1>\n")
    title_body_parts.append(f"<p><strong>Strategy:</strong> {_esc(strat)}</p>\n")
    status_class = "ok" if status == "ok" else "failed"
    title_body_parts.append(
        f'<p><strong>Status:</strong> <span class="{status_class}">{_esc(status)}</span></p>\n'
    )
    if biosample:
        ena_url = f"https://www.ebi.ac.uk/biosamples/samples/{html.escape(biosample)}"
        title_body_parts.append(
            f'<p><strong>BioSample:</strong> '
            f'<a href="{ena_url}" target="_blank">{_esc(biosample)}</a></p>\n'
        )
    title_body_parts.append("</div>\n")
    parts.append("".join(title_body_parts))

    # Curated metadata card
    curation_rows = [
        ("tissue", _esc(sample.get("tissue"))),
        ("cell_type", _esc(sample.get("cell_type"))),
        ("dev_stage", _esc(sample.get("developmental_stage"))),
        ("strain", _esc(sample.get("genotype_strain"))),
        ("treatment", _esc(sample.get("treatment"))),
        ("antibody_target", _esc(sample.get("antibody_target"))),
    ]
    parts.append(_card("Curated Metadata", _kv_table(curation_rows)))

    # Pipeline stats card
    pipeline_rows = [
        ("layout", _esc(sample.get("layout"))),
        ("reads_filtered", _esc(sample.get("reads_filtered"))),
        ("mapping_rate", _fmt(sample.get("mapping_rate"), 2)),
        ("duplication_rate", _fmt(sample.get("duplication_rate"), 2)),
        ("elapsed_min", _fmt(sample.get("elapsed_min"), 2)),
    ]
    parts.append(_card("Pipeline Stats", _kv_table(pipeline_rows)))

    # Strategy-specific outputs card
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        chip_rows: list[tuple[str, str]] = [
            ("peaks_q5", _esc(sample.get("peaks_q5"))),
            ("peaks_q10", _esc(sample.get("peaks_q10"))),
            ("peaks_q20", _esc(sample.get("peaks_q20"))),
            ("bigwig_path", _esc(sample.get("bigwig_path"))),
            ("peaks_q5_path", _esc(sample.get("peaks_q5_path"))),
            ("peaks_q10_path", _esc(sample.get("peaks_q10_path"))),
            ("peaks_q20_path", _esc(sample.get("peaks_q20_path"))),
        ]
        parts.append(_card("Peak Calls & BigWig", _kv_table(chip_rows)))

    elif strat == "Bisulfite-Seq":
        bs_rows: list[tuple[str, str]] = [
            ("mean_CpG", _fmt(sample.get("mean_cpg"), 4)),
            ("mean_CHG", _fmt(sample.get("mean_chg"), 4)),
            ("mean_CHH", _fmt(sample.get("mean_chh"), 4)),
            ("cpg_hmr_count", _esc(sample.get("cpg_hmr_count"))),
            ("cpg_hypermr_count", _esc(sample.get("cpg_hypermr_count"))),
            ("cpg_pmd_count", _esc(sample.get("cpg_pmd_count"))),
            ("chg_hypermr_count", _esc(sample.get("chg_hypermr_count"))),
            ("CpG methyl bw", _esc(sample.get("cpg_methyl_bw_path"))),
            ("CpG cover bw", _esc(sample.get("cpg_cover_bw_path"))),
            ("CpG HMR bed", _esc(sample.get("cpg_hmr_path"))),
            ("CpG hyperMR bed", _esc(sample.get("cpg_hypermr_path"))),
            ("CpG PMD bed", _esc(sample.get("cpg_pmd_path"))),
            ("CHG methyl bw", _esc(sample.get("chg_methyl_bw_path"))),
            ("CHG cover bw", _esc(sample.get("chg_cover_bw_path"))),
            ("CHG hyperMR bed", _esc(sample.get("chg_hypermr_path"))),
            ("CHH methyl bw", _esc(sample.get("chh_methyl_bw_path"))),
            ("CHH cover bw", _esc(sample.get("chh_cover_bw_path"))),
        ]
        parts.append(_card("Methylation Outputs", _kv_table(bs_rows)))

    # Failure card
    if status == "failed":
        exit_code = sample.get("exit_code") or ""
        log_snippet = sample.get("log_snippet") or ""
        fail_body = (
            f"<p><strong>Exit code:</strong> {_esc(exit_code)}</p>\n"
            f'<pre class="log">{_esc(log_snippet)}</pre>\n'
        )
        parts.append(_card("Failure Info", fail_body))

    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Strategy page
# ---------------------------------------------------------------------------

def render_strategy(strategy: str, samples: list[dict]) -> str:
    """Render a strategy-filtered page. strategy is the slug: chipseq/atacseq/bsseq."""
    strategy_name = SLUG_TO_STRATEGY.get(strategy, strategy)
    filtered = [s for s in samples if s.get("library_strategy") == strategy_name]

    by_strategy, n_ok, n_failed = _summary_stats(filtered)
    n_total = len(filtered)

    parts: list[str] = []
    parts.append(_page_header(f"{strategy_name} — zenigoke catalog"))
    parts.append(_nav(strategy_name))

    parts.append('<div class="container">\n')
    parts.append(f"<h1>{_esc(strategy_name)}</h1>\n")

    parts.append('<div class="card summary-card">\n')
    parts.append(f"<p><strong>{n_total}</strong> samples &mdash; "
                 f"<span class='ok'>{n_ok} ok</span>, "
                 f"<span class='failed'>{n_failed} failed</span></p>\n")
    parts.append("</div>\n")

    # Table (no filter input — the page IS the filter)
    parts.append(f'<table id="samples-table">\n')
    parts.append("<thead>\n<tr>\n")
    for col in ["accession", "strategy", "status", "tissue", "dev_stage",
                "strain", "antibody", "mapping_rate", "elapsed_min", ""]:
        parts.append(f"<th>{_esc(col)}</th>\n")
    parts.append("</tr>\n</thead>\n<tbody>\n")

    for s in filtered:
        acc = s.get("accession") or ""
        strat = s.get("library_strategy") or ""
        status = s.get("status") or ""
        tissue = s.get("tissue") or ""
        dev_stage = s.get("developmental_stage") or ""
        strain = s.get("genotype_strain") or ""
        antibody = s.get("antibody_target") or ""
        mapping = _fmt(s.get("mapping_rate"), 1)
        elapsed = _fmt(s.get("elapsed_min"), 1)

        status_class = "ok" if status == "ok" else "failed"
        sample_link = f'<a href="../samples/{_esc(acc)}.html">&rarr;</a>'

        parts.append(f'<tr class="{status_class}">\n')
        parts.append(f'<td><a href="../samples/{_esc(acc)}.html">{_esc(acc)}</a></td>\n')
        parts.append(f"<td>{_esc(strat)}</td>\n")
        parts.append(f'<td class="{status_class}">{_esc(status)}</td>\n')
        parts.append(f"<td>{_esc(tissue)}</td>\n")
        parts.append(f"<td>{_esc(dev_stage)}</td>\n")
        parts.append(f"<td>{_esc(strain)}</td>\n")
        parts.append(f"<td>{_esc(antibody)}</td>\n")
        parts.append(f"<td>{_esc(mapping)}</td>\n")
        parts.append(f"<td>{_esc(elapsed)}</td>\n")
        parts.append(f"<td>{sample_link}</td>\n")
        parts.append("</tr>\n")

    parts.append("</tbody>\n</table>\n")
    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Summary page (reuses build_report logic)
# ---------------------------------------------------------------------------

def render_summary(samples: Optional[list[dict]] = None) -> str:
    """Invoke build_report.render_html to produce the Phase 1 summary page.

    If samples is provided (for testing), it must be in build_report format
    (with 'curated' sub-dict). Otherwise we return a placeholder.
    This is intentionally a thin wrapper — the real work is done in
    write_pages() which reads from the live data sources.
    """
    try:
        import importlib.util
        import sys as _sys

        here = pathlib.Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "build_report", here / "build_report.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if samples is not None:
            return mod.render_html(samples)

        # No samples provided — return placeholder
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>zenigoke summary</title></head><body>"
            "<h1>Summary</h1><p>Run build-report.py to generate.</p>"
            "</body></html>"
        )
    except Exception:
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>zenigoke summary</title></head><body>"
            "<h1>Summary</h1><p>Could not load build_report module.</p>"
            "</body></html>"
        )


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

def _load_samples(db_path: pathlib.Path) -> list[dict]:
    """Load all samples from the SQLite catalog DB as flat dicts."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            s.accession,
            s.library_strategy,
            s.status,
            s.layout,
            s.reads_filtered,
            s.mapping_rate,
            s.duplication_rate,
            s.elapsed_min,
            s.biosample_accession,
            s.output_dir,
            c.tissue,
            c.cell_type,
            c.developmental_stage,
            c.genotype_strain,
            c.treatment,
            c.antibody_target,
            ch.peaks_q5,
            ch.peaks_q10,
            ch.peaks_q20,
            ch.bigwig_path,
            ch.peaks_q5_path,
            ch.peaks_q10_path,
            ch.peaks_q20_path,
            b.mean_cpg,
            b.mean_chg,
            b.mean_chh,
            b.cpg_hmr_count,
            b.cpg_hypermr_count,
            b.cpg_pmd_count,
            b.chg_hypermr_count,
            b.cpg_methyl_bw_path,
            b.cpg_cover_bw_path,
            b.cpg_hmr_path,
            b.cpg_hypermr_path,
            b.cpg_pmd_path,
            b.chg_methyl_bw_path,
            b.chg_cover_bw_path,
            b.chg_hypermr_path,
            b.chh_methyl_bw_path,
            b.chh_cover_bw_path
        FROM sample s
        LEFT JOIN sample_curation c USING (accession)
        LEFT JOIN sample_chipseq ch USING (accession)
        LEFT JOIN sample_bsseq b USING (accession)
        ORDER BY s.library_strategy, s.accession
        """
    ).fetchall()

    conn.close()
    return [dict(row) for row in rows]


def _load_failed_info(data_root: pathlib.Path, acc: str) -> dict:
    """Read exit code and log snippet from a .failed status file."""
    status_file = data_root / "status" / f"{acc}.failed"
    if not status_file.exists():
        return {"exit_code": "", "log_snippet": ""}
    text = status_file.read_text()
    exit_code = ""
    snippet_lines: list[str] = []
    in_snippet = False
    for line in text.splitlines():
        if line.startswith("exit_code="):
            exit_code = line.split("=", 1)[1].strip()
        elif "last 50 lines of log" in line:
            in_snippet = True
        elif in_snippet:
            snippet_lines.append(line)
    return {
        "exit_code": exit_code,
        "log_snippet": "\n".join(snippet_lines).strip(),
    }


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

STYLE_CSS = """\
/* zenigoke catalog — shared stylesheet */
*, *::before, *::after { box-sizing: border-box; }

body {
  font: 14px/1.5 system-ui, -apple-system, sans-serif;
  margin: 0;
  color: #222;
  background: #f7f7f7;
}

nav {
  background: #1a1a2e;
  padding: 0.5rem 1.5rem;
  display: flex;
  gap: 1.2rem;
  flex-wrap: wrap;
}

nav a {
  color: #aac4ff;
  text-decoration: none;
  font-size: 0.9rem;
  font-weight: 500;
}

nav a:hover, nav a.active {
  color: #fff;
  text-decoration: underline;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1.5rem;
}

h1 { font-size: 1.5rem; margin: 0 0 1rem; }
h2 { font-size: 1.1rem; margin: 0 0 0.6rem; color: #333; }

.card {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
}

.summary-card { border-left: 4px solid #4a90d9; }

.badge {
  display: inline-block;
  background: #e8f0fe;
  color: #1a56db;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.82rem;
  margin-right: 0.4rem;
}

.filter-row {
  margin-bottom: 0.8rem;
}

#q {
  width: 100%;
  max-width: 400px;
  padding: 0.4rem 0.6rem;
  font-size: 0.95rem;
  border: 1px solid #ccc;
  border-radius: 4px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  background: #fff;
}

th, td {
  border: 1px solid #e0e0e0;
  padding: 0.3rem 0.5rem;
  text-align: left;
}

th {
  background: #f0f0f0;
  font-weight: 600;
  position: sticky;
  top: 0;
}

tr:hover td { background: #f5f9ff; }

tr.ok td  { background: #f0fff0; }
tr.failed td { background: #fff0f0; }

td.ok  { color: #2d7a2d; font-weight: 600; }
td.failed { color: #b91c1c; font-weight: 600; }

.kv th {
  width: 160px;
  color: #555;
  font-weight: 500;
  background: #fafafa;
}

.title-meta { margin-bottom: 1rem; }
.title-meta h1 { margin-bottom: 0.3rem; }

a { color: #1a56db; }
a:visited { color: #7e3af2; }

pre.log {
  font: 11px/1.4 monospace;
  background: #1a1a2e;
  color: #e0e0e0;
  padding: 0.6rem;
  border-radius: 4px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.ok  { color: #2d7a2d; }
.failed { color: #b91c1c; }

span.ok, span.failed {
  font-weight: 600;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

span.ok { background: #dcfce7; }
span.failed { background: #fee2e2; }
"""


# ---------------------------------------------------------------------------
# Main page writer
# ---------------------------------------------------------------------------

def write_pages(
    db_path: pathlib.Path,
    out_dir: pathlib.Path,
    data_root: pathlib.Path = pathlib.Path("/data1/zenigoke"),
    phase1_summary_src: Optional[pathlib.Path] = None,
) -> dict:
    """Build all static pages. Returns a dict with counts."""
    samples = _load_samples(db_path)

    # Enrich failed samples with exit code / log snippet
    for s in samples:
        if s.get("status") == "failed":
            fail_info = _load_failed_info(data_root, s["accession"])
            s.update(fail_info)

    # Create directories
    samples_dir = out_dir / "samples"
    strategy_dir = out_dir / "strategy"
    assets_dir = out_dir / "assets"

    # Atomic: clear and recreate samples/ and strategy/
    if samples_dir.exists():
        shutil.rmtree(samples_dir)
    if strategy_dir.exists():
        shutil.rmtree(strategy_dir)

    samples_dir.mkdir(parents=True, exist_ok=True)
    strategy_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    counts = {
        "index": 0,
        "samples": 0,
        "strategy": 0,
        "summary": 0,
    }

    # Write stylesheet
    (assets_dir / "style.css").write_text(STYLE_CSS)

    # Write index.html
    (out_dir / "index.html").write_text(render_index(samples))
    counts["index"] = 1

    # Write per-sample pages
    for s in samples:
        acc = s.get("accession")
        if acc:
            page = render_sample(s)
            (samples_dir / f"{acc}.html").write_text(page)
            counts["samples"] += 1

    # Write strategy pages
    for slug in ("chipseq", "atacseq", "bsseq"):
        page = render_strategy(slug, samples)
        (strategy_dir / f"{slug}.html").write_text(page)
        counts["strategy"] += 1

    # Write summary.html
    # Try to reuse the existing phase1-summary.html content, or regenerate
    old_phase1 = out_dir / "phase1-summary.html"
    if old_phase1.exists():
        # Rename by reading + writing to summary.html, then deleting old
        (out_dir / "summary.html").write_text(old_phase1.read_text())
        old_phase1.unlink()
        counts["summary"] = 1
    else:
        # Generate from scratch using build_report
        try:
            import importlib.util
            here = pathlib.Path(__file__).resolve().parent
            spec = importlib.util.spec_from_file_location(
                "build_report", here / "build_report.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            br_samples = mod.collect_samples(
                data_root,
                data_root / "output",
                pathlib.Path("metadata"),
                pathlib.Path("zenigoke_sra_experiments.csv")
                if pathlib.Path("zenigoke_sra_experiments.csv").exists() else None,
            )
            (out_dir / "summary.html").write_text(mod.render_html(br_samples))
        except Exception:
            (out_dir / "summary.html").write_text(
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>zenigoke summary</title></head><body>"
                "<h1>Summary</h1><p>Run build-report.py to generate.</p>"
                "</body></html>"
            )
        counts["summary"] = 1

    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Build static HTML catalog pages from kknmsmd.db"
    )
    p.add_argument("--db", default="db/kknmsmd.db", help="Path to SQLite catalog DB")
    p.add_argument("--out", default="report", help="Output directory for HTML pages")
    p.add_argument("--data-root", default="/data1/zenigoke", help="Pipeline data root")
    args = p.parse_args(argv)

    db_path = pathlib.Path(args.db)
    out_dir = pathlib.Path(args.out)
    data_root = pathlib.Path(args.data_root)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", flush=True)
        return 1

    print(f"Building static catalog pages")
    print(f"  DB:        {db_path}")
    print(f"  Output:    {out_dir}")
    print(f"  Data root: {data_root}")

    counts = write_pages(db_path, out_dir, data_root)

    print("\nPages written:")
    for k, v in counts.items():
        print(f"  {k:12s}: {v}")

    total = sum(counts.values())
    print(f"\nTotal HTML files: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
