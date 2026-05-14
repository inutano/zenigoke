"""Generate static HTML catalog pages from the zenigoke SQLite catalog DB.

Stdlib + sqlite3 only.

Public API:
  render_index(samples: list[dict]) -> str   # matrix scaffold (Phase 3 Task 6)
  render_browse(samples: list[dict]) -> str  # all-samples table (Phase 3 Task 6)
  render_sample(sample: dict) -> str
  render_strategy(strategy: str, samples: list[dict]) -> str
  render_summary(samples: list[dict]) -> str
  render_methods() -> str
  render_about() -> str
  write_pages(db_path: pathlib.Path, out_dir: pathlib.Path) -> None
  main(argv=None) -> int
"""
from __future__ import annotations

import datetime
import html
import pathlib
import re
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


def _dash(x: Any) -> str:
    """Return HTML-escaped value or em-dash for empty/None."""
    if x is None or str(x).strip() == "":
        return "&mdash;"
    return html.escape(str(x))


def _fmt(x: Any, decimals: int = 1) -> str:
    """Format a numeric value or return empty string."""
    if x is None or x == "":
        return ""
    try:
        return f"{float(x):.{decimals}f}"
    except (ValueError, TypeError):
        return str(x)


def _fmt_pct(x: Any, decimals: int = 1) -> str:
    """Format a percentage value (already 0-100) with % suffix, or em-dash."""
    if x is None or x == "":
        return "&mdash;"
    try:
        return f"{float(x):.{decimals}f}%"
    except (ValueError, TypeError):
        return _dash(x)


def _fmt_frac_pct(x: Any, decimals: int = 1) -> str:
    """Format a 0-1 fraction as percentage with % suffix, or em-dash."""
    if x is None or x == "":
        return "&mdash;"
    try:
        return f"{float(x) * 100:.{decimals}f}%"
    except (ValueError, TypeError):
        return _dash(x)


def _fmt_min(x: Any, decimals: int = 1) -> str:
    """Format elapsed minutes with ' min' suffix, or em-dash."""
    if x is None or x == "":
        return "&mdash;"
    try:
        return f"{float(x):.{decimals}f} min"
    except (ValueError, TypeError):
        return _dash(x)


def _fmt_size(x: Any) -> str:
    """Format bytes as human-readable size (KB/MB/GB), or em-dash."""
    if x is None or str(x).strip() == "":
        return "&mdash;"
    try:
        b = float(x)
        if b >= 1e9:
            return f"{b / 1e9:.1f} GB"
        if b >= 1e6:
            return f"{b / 1e6:.1f} MB"
        if b >= 1e3:
            return f"{b / 1e3:.1f} KB"
        return f"{b:.0f} B"
    except (ValueError, TypeError):
        return _esc(str(x))


def _layout_label(x: Any) -> str:
    """Translate 0/1 layout to SE/PE, also accept SE/PE strings directly."""
    if x is None or str(x).strip() == "":
        return "&mdash;"
    s = str(x).strip()
    if s == "0":
        return "SE"
    if s == "1":
        return "PE"
    return _esc(s)  # pass through SE/PE etc.


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


def _nav(active: str = "", prefix: str = "") -> str:
    """Build the nav bar.

    prefix: relative path prefix to reach report root ('' for root, '../' for subdirs).
    active: the nav label to mark as active.
    """
    links = [
        ("index.html", "Matrix"),
        ("browse.html", "Browse"),
        ("strategy/chipseq.html", "ChIP-Seq"),
        ("strategy/atacseq.html", "ATAC-Seq"),
        ("strategy/bsseq.html", "BS-Seq"),
        ("summary.html", "Summary"),
        ("methods.html", "Methods"),
        ("about.html", "About"),
    ]
    items = []
    for href, label in links:
        cls = ' class="active"' if label == active else ""
        items.append(f'<a href="{prefix}{href}"{cls}>{_esc(label)}</a>')
    return "<nav>\n" + "\n".join(items) + "\n</nav>\n"


def _nav_from_root(active: str = "") -> str:
    """Nav bar for pages at report/ root."""
    return _nav(active, prefix="")


def _nav_from_subdir(active: str = "") -> str:
    """Nav bar for pages one level deep (samples/, strategy/)."""
    links = [
        ("../index.html", "Matrix"),
        ("../browse.html", "Browse"),
        ("chipseq.html", "ChIP-Seq"),
        ("atacseq.html", "ATAC-Seq"),
        ("bsseq.html", "BS-Seq"),
        ("../summary.html", "Summary"),
        ("../methods.html", "Methods"),
        ("../about.html", "About"),
    ]
    items = []
    for href, label in links:
        cls = ' class="active"' if label == active else ""
        items.append(f'<a href="{href}"{cls}>{_esc(label)}</a>')
    return "<nav>\n" + "\n".join(items) + "\n</nav>\n"


def _nav_from_sample(active: str = "") -> str:
    """Nav bar for pages inside samples/ subdirectory."""
    links = [
        ("../index.html", "Matrix"),
        ("../browse.html", "Browse"),
        ("../strategy/chipseq.html", "ChIP-Seq"),
        ("../strategy/atacseq.html", "ATAC-Seq"),
        ("../strategy/bsseq.html", "BS-Seq"),
        ("../summary.html", "Summary"),
        ("../methods.html", "Methods"),
        ("../about.html", "About"),
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


def _clean_log_snippet(text: str, n: int = 10) -> str:
    """Filter aria2c progress lines and return last n meaningful lines."""
    lines = [
        ln for ln in text.splitlines()
        if ln.strip()
        and not re.match(r'^\[#[0-9a-f]+\s', ln)
        and 'progress summary' not in ln.lower()
    ]
    return "\n".join(lines[-n:])


def _file_link(path: Optional[str], acc: str, strat_dir: str, label: Optional[str] = None) -> str:
    """Convert an absolute filesystem path to an HTTP-relative link, or '(not produced)'."""
    if not path:
        return "(not produced)"
    # Convert /data1/zenigoke/output/{strat_dir}/{acc}/{filename}
    # to ../output/{strat_dir}/{acc}/{filename}  (relative from samples/)
    p = pathlib.Path(path)
    filename = p.name
    link_text = label if label else filename
    # Build relative URL: samples/x.html -> ../output/...
    rel_url = f"../output/{strat_dir}/{acc}/{filename}"
    return f'<a href="{rel_url}">{_esc(link_text)}</a>'


# ---------------------------------------------------------------------------
# JavaScript helpers
# ---------------------------------------------------------------------------

JS_FILTER_AND_CHIPS = """<script>
const q = document.getElementById('q');
const rows = Array.from(document.querySelectorAll('#samples-table tbody tr'));
const countEl = document.getElementById('filter-count');

function updateCount() {
  const visible = rows.filter(r => r.style.display !== 'none').length;
  if (countEl) countEl.textContent = visible + ' of ' + rows.length;
}

q.addEventListener('input', () => {
  const t = q.value.toLowerCase();
  for (const row of rows) {
    row.style.display = row.textContent.toLowerCase().includes(t) ? '' : 'none';
  }
  updateCount();
});

// Quick-filter chip click handler
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const val = chip.dataset.filter || '';
    q.value = val;
    q.dispatchEvent(new Event('input'));
  });
});

// Sortable column headers
document.querySelectorAll('#samples-table th[data-col]').forEach(th => {
  th.style.cursor = 'pointer';
  th.title = 'Click to sort';
  let asc = true;
  th.addEventListener('click', () => {
    const col = parseInt(th.dataset.col);
    const tbody = document.querySelector('#samples-table tbody');
    const sortedRows = Array.from(tbody.querySelectorAll('tr')).sort((a, b) => {
      const av = a.cells[col] ? a.cells[col].textContent.trim() : '';
      const bv = b.cells[col] ? b.cells[col].textContent.trim() : '';
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    sortedRows.forEach(r => tbody.appendChild(r));
    asc = !asc;
    document.querySelectorAll('#samples-table th[data-col]').forEach(h => {
      h.textContent = h.textContent.replace(' ▲', '').replace(' ▼', '');
    });
    th.textContent += asc ? ' ▼' : ' ▲';
  });
});

updateCount();
</script>
"""

JS_SORT_ONLY = """<script>
// Sortable column headers (no filter input on strategy pages)
document.querySelectorAll('#samples-table th[data-col]').forEach(th => {
  th.style.cursor = 'pointer';
  th.title = 'Click to sort';
  let asc = true;
  th.addEventListener('click', () => {
    const col = parseInt(th.dataset.col);
    const tbody = document.querySelector('#samples-table tbody');
    const sortedRows = Array.from(tbody.querySelectorAll('tr')).sort((a, b) => {
      const av = a.cells[col] ? a.cells[col].textContent.trim() : '';
      const bv = b.cells[col] ? b.cells[col].textContent.trim() : '';
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    sortedRows.forEach(r => tbody.appendChild(r));
    asc = !asc;
    document.querySelectorAll('#samples-table th[data-col]').forEach(h => {
      h.textContent = h.textContent.replace(' ▲', '').replace(' ▼', '');
    });
    th.textContent += asc ? ' ▼' : ' ▲';
  });
});
</script>
"""


# ---------------------------------------------------------------------------
# Index page (Phase 3 Task 6: matrix scaffold)
# ---------------------------------------------------------------------------

def render_index(samples: list[dict]) -> str:
    """Top page is now the interactive matrix scaffold.

    samples is kept for signature compatibility; not used at render time
    (the matrix loads data via the API at runtime in Task 7).
    """
    n = len(samples) or 157

    parts: list[str] = []
    parts.append(_page_header("zenigoke catalog"))
    # Inject matrix.css after the stylesheet tag (matrix.css created in Task 7)
    parts.append('<link rel="stylesheet" href="assets/matrix.css">\n')
    parts.append(_nav_from_root("Matrix"))

    parts.append('<div class="container">\n')

    parts.append('<div class="card info-card">\n')
    parts.append('<h2>Marchantia polymorpha multiomics catalog</h2>\n')
    parts.append(
        f"<p>Pick two attributes to cross-tabulate the {n} samples. "
        f"Click a cell to send the bundle to IGV.</p>\n"
    )
    parts.append("</div>\n")

    parts.append('<div class="card">\n')
    parts.append('<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap">\n')
    parts.append('<label>X axis: <select id="x-axis-select"></select></label>\n')
    parts.append('<label>Y axis: <select id="y-axis-select"></select></label>\n')
    parts.append('<label><input type="checkbox" id="include-unknown"> include unknowns</label>\n')
    parts.append("</div>\n")
    parts.append("</div>\n")

    parts.append('<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap">\n')

    parts.append('<div class="card" style="flex:2;min-width:min(400px,100%)">\n')
    parts.append('<div id="matrix-grid">Loading…</div>\n')
    parts.append("</div>\n")

    parts.append('<div class="card" style="flex:1;min-width:min(280px,100%);position:sticky;top:1rem">\n')
    parts.append('<h2>Selection</h2>\n')
    parts.append('<div id="selection-panel"><p class="subtitle">Click a populated cell to begin.</p></div>\n')
    parts.append("</div>\n")

    parts.append("</div>\n")  # flex row

    parts.append("</div>\n")  # .container

    parts.append("<script src='assets/matrix.js' defer></script>\n")
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Browse page (Phase 3 Task 6: old index table moved here)
# ---------------------------------------------------------------------------

def _sample_row_html(s: dict, link_prefix: str = "samples/") -> str:
    """Return the <tr>…</tr> HTML for one sample row in the browse/index table."""
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
    sample_link = f'<a href="{link_prefix}{_esc(acc)}.html">&rarr;</a>'

    return (
        f'<tr class="{status_class}">\n'
        f'<td><a href="{link_prefix}{_esc(acc)}.html">{_esc(acc)}</a></td>\n'
        f"<td>{_esc(strat)}</td>\n"
        f'<td class="{status_class}">{_esc(status)}</td>\n'
        f"<td>{_dash(tissue)}</td>\n"
        f"<td>{_dash(dev_stage)}</td>\n"
        f"<td>{_dash(strain)}</td>\n"
        f"<td>{_dash(antibody)}</td>\n"
        f"<td>{_esc(mapping)}</td>\n"
        f"<td>{_esc(elapsed)}</td>\n"
        f"<td>{sample_link}</td>\n"
        f"</tr>\n"
    )


def render_browse(samples: list[dict]) -> str:
    """The old 'all samples table' view, now living at browse.html."""
    by_strategy, n_ok, n_failed = _summary_stats(samples)
    n_total = len(samples)
    build_date = datetime.date.today().isoformat()

    # Pull strategy counts for the info card
    n_chip = by_strategy.get("ChIP-Seq", 0)
    n_atac = by_strategy.get("ATAC-Seq", 0)
    n_bs = by_strategy.get("Bisulfite-Seq", 0)

    parts: list[str] = []
    parts.append(_page_header("Browse — zenigoke catalog"))
    parts.append(_nav_from_root("Browse"))

    parts.append('<div class="container">\n')
    parts.append("<h1>Browse all samples</h1>\n")

    # "What is this?" landing card
    parts.append('<div class="card info-card">\n')
    parts.append('<h2>What is this?</h2>\n')
    parts.append(
        f"<p><strong>zenigoke catalog &mdash; Marchantia polymorpha multiomics</strong><br>\n"
        f"{n_total} SRA experiments ({n_chip} ChIP-Seq, {n_atac} ATAC-Seq, {n_bs} Bisulfite-Seq) "
        f"processed against the <strong>MpTak v7.1 standard genome</strong> from MarpolBase. "
        f"Pipelines: <strong>chip-atlas-pipeline-v2</strong> (v1.0.0 for ChIP/ATAC, v1.1.0 for BS-seq, "
        f"plant fork local). Metadata curation via <strong>dbcls/bsllmner-mk2</strong> with "
        f"<code>qwen3:27b</code>.</p>\n"
        f"<p>Build: {_esc(build_date)}. Catalog DB: <code>db/kknmsmd.db</code>.</p>\n"
    )
    parts.append("</div>\n")

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

    # Quick-filter chips
    parts.append('<div class="chip-row">\n')
    chips = [
        ("ChIP-Seq", "ChIP-Seq"),
        ("ATAC-Seq", "ATAC-Seq"),
        ("BS-Seq", "Bisulfite-Seq"),
        ("thallus", "thallus"),
        ("Tak-1", "Tak-1"),
        ("H3K27me3", "H3K27me3"),
        ("H3K4me3", "H3K4me3"),
        ("Reset", ""),
    ]
    for label, filter_val in chips:
        parts.append(f'<button class="chip" data-filter="{_esc(filter_val)}">{_esc(label)}</button>\n')
    parts.append("</div>\n")

    # Filter input with count
    parts.append('<div class="filter-row">\n')
    parts.append('<input id="q" type="text" placeholder="filter&hellip;" autocomplete="off">\n')
    parts.append(f'<span id="filter-count">{n_total} of {n_total}</span>\n')
    parts.append("</div>\n")

    # Samples table with sortable headers
    parts.append('<table id="samples-table">\n')
    parts.append("<thead>\n<tr>\n")
    cols = ["accession", "strategy", "status", "tissue", "dev_stage",
            "strain", "antibody", "mapping_rate", "elapsed_min", ""]
    for i, col in enumerate(cols):
        if col:
            parts.append(f'<th data-col="{i}">{_esc(col)}</th>\n')
        else:
            parts.append("<th></th>\n")
    parts.append("</tr>\n</thead>\n<tbody>\n")

    for s in samples:
        parts.append(_sample_row_html(s))

    parts.append("</tbody>\n</table>\n")
    parts.append("</div>\n")  # .container

    parts.append(JS_FILTER_AND_CHIPS)
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
    strat_dir = STRATEGY_SLUGS.get(strat, strat.lower())

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
        biosample_url = f"https://www.ebi.ac.uk/biosamples/samples/{html.escape(biosample)}"
        title_body_parts.append(
            f'<p><strong>BioSample:</strong> '
            f'<a href="{biosample_url}" target="_blank">{_esc(biosample)}</a></p>\n'
        )
    # ENA / SRA link (Item 5)
    ena_url = f"https://www.ebi.ac.uk/ena/browser/view/{html.escape(acc)}"
    title_body_parts.append(
        f'<p><strong>SRA:</strong> '
        f'<a href="{ena_url}" target="_blank">{_esc(acc)}</a></p>\n'
    )
    title_body_parts.append("</div>\n")
    parts.append("".join(title_body_parts))

    # Curated metadata card (Item 8: em-dash for empty cells)
    curation_rows = [
        ("tissue", _dash(sample.get("tissue"))),
        ("cell_type", _dash(sample.get("cell_type"))),
        ("dev_stage", _dash(sample.get("developmental_stage"))),
        ("strain", _dash(sample.get("genotype_strain"))),
        ("treatment", _dash(sample.get("treatment"))),
        ("antibody_target", _dash(sample.get("antibody_target"))),
    ]
    parts.append(_card("Curated Metadata", _kv_table(curation_rows)))

    # For failed samples, show failure card instead of pipeline stats (Item 7)
    if status == "failed":
        exit_code = sample.get("exit_code") or "unknown"
        raw_snippet = sample.get("log_snippet") or ""
        cleaned = _clean_log_snippet(raw_snippet)
        fail_body = (
            f'<p class="failed"><strong>Did not complete (exit code {_esc(exit_code)})</strong></p>\n'
            f"<p>bwa-mem2 produced SAM with SEQ/QUAL length mismatch. "
            f"samtools aborted during sort/markdup.</p>\n"
            f'<pre class="log">{_esc(cleaned)}</pre>\n'
        )
        parts.append(f'<div class="card failed-card">\n<h2>Did Not Complete</h2>\n{fail_body}\n</div>\n')
    else:
        # Pipeline stats card (Item 2: layout SE/PE; Item 9: hide duplication_rate for BS-seq;
        #                      Item 11: units; Item 15: fastq_size + reads_mapped)
        pipeline_rows: list[tuple[str, str]] = [
            ("layout", _layout_label(sample.get("layout"))),
            ("fastq_size", _fmt_size(sample.get("fastq_size"))),
            ("reads_filtered", _dash(sample.get("reads_filtered"))),
        ]
        if strat != "Bisulfite-Seq":
            pipeline_rows.append(("reads_mapped", _dash(sample.get("reads_mapped"))))
        pipeline_rows.append(("mapping_rate", _fmt_pct(sample.get("mapping_rate"))))
        if strat != "Bisulfite-Seq":  # Item 9
            pipeline_rows.append(("duplication_rate", _fmt_pct(sample.get("duplication_rate"))))
        pipeline_rows.append(("elapsed_min", _fmt_min(sample.get("elapsed_min"))))
        parts.append(_card("Pipeline Stats", _kv_table(pipeline_rows)))

    # Strategy-specific outputs card (Item 3: HTTP links; Item 11: units)
    if strat in ("ChIP-Seq", "ATAC-Seq") and status == "ok":
        chip_rows: list[tuple[str, str]] = [
            ("peaks_q5", _dash(sample.get("peaks_q5"))),
            ("peaks_q10", _dash(sample.get("peaks_q10"))),
            ("peaks_q20", _dash(sample.get("peaks_q20"))),
            ("bigwig", _file_link(sample.get("bigwig_path"), acc, strat_dir)),
            ("peaks_q5 file", _file_link(sample.get("peaks_q5_path"), acc, strat_dir)),
            ("peaks_q10 file", _file_link(sample.get("peaks_q10_path"), acc, strat_dir)),
            ("peaks_q20 file", _file_link(sample.get("peaks_q20_path"), acc, strat_dir)),
        ]
        parts.append(_card("Peak Calls & BigWig", _kv_table(chip_rows)))

    elif strat == "Bisulfite-Seq" and status == "ok":
        bs_rows: list[tuple[str, str]] = [
            ("mean_CpG", _fmt_frac_pct(sample.get("mean_cpg"))),
            ("mean_CHG", _fmt_frac_pct(sample.get("mean_chg"))),
            ("mean_CHH", _fmt_frac_pct(sample.get("mean_chh"))),
            ("cpg_hmr_count", _dash(sample.get("cpg_hmr_count"))),
            ("cpg_hypermr_count", _dash(sample.get("cpg_hypermr_count"))),
            ("cpg_pmd_count", _dash(sample.get("cpg_pmd_count"))),
            ("chg_hypermr_count", _dash(sample.get("chg_hypermr_count"))),
            ("CpG methyl bw", _file_link(sample.get("cpg_methyl_bw_path"), acc, "bsseq")),
            ("CpG cover bw", _file_link(sample.get("cpg_cover_bw_path"), acc, "bsseq")),
            ("CpG HMR bed", _file_link(sample.get("cpg_hmr_path"), acc, "bsseq")),
            ("CpG hyperMR bed", _file_link(sample.get("cpg_hypermr_path"), acc, "bsseq")),
            ("CpG PMD bed", _file_link(sample.get("cpg_pmd_path"), acc, "bsseq")),
            ("CHG methyl bw", _file_link(sample.get("chg_methyl_bw_path"), acc, "bsseq")),
            ("CHG cover bw", _file_link(sample.get("chg_cover_bw_path"), acc, "bsseq")),
            ("CHG hyperMR bed", _file_link(sample.get("chg_hypermr_path"), acc, "bsseq")),
            ("CHH methyl bw", _file_link(sample.get("chh_methyl_bw_path"), acc, "bsseq")),
            ("CHH cover bw", _file_link(sample.get("chh_cover_bw_path"), acc, "bsseq")),
        ]
        parts.append(_card("Methylation Outputs", _kv_table(bs_rows)))

    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Strategy page
# ---------------------------------------------------------------------------

# Per-strategy column definitions (Item 10)
# Each entry: (header_label, data_extractor_key_or_callable)
# data_extractor is called with the sample dict and returns an HTML string

def _strategy_columns(slug: str) -> list[tuple[str, Any]]:
    """Return (header, extractor_fn) list for a strategy page."""

    def _acc_link(s: dict) -> str:
        acc = s.get("accession") or ""
        return f'<a href="../samples/{_esc(acc)}.html">{_esc(acc)}</a>'

    def _status_cell(s: dict) -> str:
        st = s.get("status") or ""
        cls = "ok" if st == "ok" else "failed"
        return f'<span class="{cls}">{_esc(st)}</span>'

    def _arrow_link(s: dict) -> str:
        acc = s.get("accession") or ""
        return f'<a href="../samples/{_esc(acc)}.html">&rarr;</a>'

    base_cols: list[tuple[str, Any]] = [
        ("accession", _acc_link),
        ("status", _status_cell),
        ("tissue", lambda s: _dash(s.get("tissue"))),
        ("dev_stage", lambda s: _dash(s.get("developmental_stage"))),
        ("strain", lambda s: _dash(s.get("genotype_strain"))),
        ("mapping_rate", lambda s: _fmt_pct(s.get("mapping_rate"))),
        ("elapsed_min", lambda s: _fmt_min(s.get("elapsed_min"))),
        ("", _arrow_link),
    ]

    if slug == "chipseq":
        # ChIP-Seq: keep antibody column
        return [
            ("accession", _acc_link),
            ("status", _status_cell),
            ("tissue", lambda s: _dash(s.get("tissue"))),
            ("dev_stage", lambda s: _dash(s.get("developmental_stage"))),
            ("strain", lambda s: _dash(s.get("genotype_strain"))),
            ("antibody", lambda s: _dash(s.get("antibody_target"))),
            ("mapping_rate", lambda s: _fmt_pct(s.get("mapping_rate"))),
            ("peaks_q5", lambda s: _dash(s.get("peaks_q5"))),
            ("elapsed_min", lambda s: _fmt_min(s.get("elapsed_min"))),
            ("", _arrow_link),
        ]
    elif slug == "atacseq":
        # ATAC-Seq: drop antibody column
        return [
            ("accession", _acc_link),
            ("status", _status_cell),
            ("tissue", lambda s: _dash(s.get("tissue"))),
            ("dev_stage", lambda s: _dash(s.get("developmental_stage"))),
            ("strain", lambda s: _dash(s.get("genotype_strain"))),
            ("mapping_rate", lambda s: _fmt_pct(s.get("mapping_rate"))),
            ("peaks_q5", lambda s: _dash(s.get("peaks_q5"))),
            ("elapsed_min", lambda s: _fmt_min(s.get("elapsed_min"))),
            ("", _arrow_link),
        ]
    elif slug == "bsseq":
        # BS-Seq: drop antibody + peak counts, add CpG/CHG/CHH methylation columns
        return [
            ("accession", _acc_link),
            ("status", _status_cell),
            ("tissue", lambda s: _dash(s.get("tissue"))),
            ("dev_stage", lambda s: _dash(s.get("developmental_stage"))),
            ("strain", lambda s: _dash(s.get("genotype_strain"))),
            ("mapping_rate", lambda s: _fmt_pct(s.get("mapping_rate"))),
            ("mean_CpG", lambda s: _fmt_frac_pct(s.get("mean_cpg"))),
            ("mean_CHG", lambda s: _fmt_frac_pct(s.get("mean_chg"))),
            ("mean_CHH", lambda s: _fmt_frac_pct(s.get("mean_chh"))),
            ("elapsed_min", lambda s: _fmt_min(s.get("elapsed_min"))),
            ("", _arrow_link),
        ]
    else:
        return base_cols


def render_strategy(strategy: str, samples: list[dict]) -> str:
    """Render a strategy-filtered page. strategy is the slug: chipseq/atacseq/bsseq."""
    strategy_name = SLUG_TO_STRATEGY.get(strategy, strategy)
    filtered = [s for s in samples if s.get("library_strategy") == strategy_name]

    by_strategy, n_ok, n_failed = _summary_stats(filtered)
    n_total = len(filtered)

    cols = _strategy_columns(strategy)

    parts: list[str] = []
    parts.append(_page_header(f"{strategy_name} — zenigoke catalog", css_path="../assets/style.css"))
    parts.append(_nav_from_subdir(strategy_name))

    parts.append('<div class="container">\n')
    parts.append(f"<h1>{_esc(strategy_name)}</h1>\n")

    parts.append('<div class="card summary-card">\n')
    parts.append(f"<p><strong>{n_total}</strong> samples &mdash; "
                 f"<span class='ok'>{n_ok} ok</span>, "
                 f"<span class='failed'>{n_failed} failed</span></p>\n")
    parts.append("</div>\n")

    # Table with sortable headers (Item 14)
    parts.append(f'<table id="samples-table">\n')
    parts.append("<thead>\n<tr>\n")
    for i, (header, _) in enumerate(cols):
        if header:
            parts.append(f'<th data-col="{i}">{_esc(header)}</th>\n')
        else:
            parts.append("<th></th>\n")
    parts.append("</tr>\n</thead>\n<tbody>\n")

    for s in filtered:
        status = s.get("status") or ""
        status_class = "ok" if status == "ok" else "failed"
        parts.append(f'<tr class="{status_class}">\n')
        for _, extractor in cols:
            parts.append(f"<td>{extractor(s)}</td>\n")
        parts.append("</tr>\n")

    parts.append("</tbody>\n</table>\n")
    parts.append("</div>\n")  # .container
    parts.append(JS_SORT_ONLY)
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Summary page (Item 6: rewritten in catalog style, no build_report dependency)
# ---------------------------------------------------------------------------

def render_summary(samples: list[dict]) -> str:
    """Render the catalog summary page in the shared catalog style."""
    by_strategy, n_ok, n_failed = _summary_stats(samples)
    n_total = len(samples)
    build_date = datetime.date.today().isoformat()

    parts: list[str] = []
    parts.append(_page_header("Summary — zenigoke catalog"))
    parts.append(_nav_from_root("Summary"))
    parts.append('<div class="container">\n')
    parts.append("<h1>Catalog Summary</h1>\n")

    # Overview card
    parts.append('<div class="card summary-card">\n')
    parts.append(f"<p><strong>{n_total}</strong> SRA experiments processed &mdash; "
                 f"<span class='ok'>{n_ok} completed successfully</span>, "
                 f"<span class='failed'>{n_failed} failed</span>.</p>\n")
    strat_items = "".join(
        f"<span class='badge'>{_esc(k)}: {v}</span>"
        for k, v in sorted(by_strategy.items())
    )
    parts.append(f"<p>{strat_items}</p>\n")
    parts.append(f"<p>Build date: {_esc(build_date)}</p>\n")
    parts.append("</div>\n")

    # Per-strategy success tables
    for strat_name, slug in STRATEGY_SLUGS.items():
        strat_samples = [s for s in samples if s.get("library_strategy") == strat_name]
        if not strat_samples:
            continue
        s_ok = [s for s in strat_samples if s.get("status") == "ok"]
        s_fail = [s for s in strat_samples if s.get("status") != "ok"]

        parts.append(f'<div class="card">\n')
        parts.append(f"<h2>{_esc(strat_name)} — {len(strat_samples)} experiments "
                     f"({len(s_ok)} ok, {len(s_fail)} failed)</h2>\n")

        if s_fail:
            parts.append("<h3>Failed</h3>\n")
            parts.append('<table>\n<thead><tr>\n')
            parts.append('<th>accession</th><th>exit_code</th>\n')
            parts.append('</tr></thead>\n<tbody>\n')
            for s in s_fail:
                acc = s.get("accession") or ""
                ec = s.get("exit_code") or "&mdash;"
                parts.append(f'<tr class="failed">\n')
                parts.append(f'<td><a href="samples/{_esc(acc)}.html">{_esc(acc)}</a></td>\n')
                parts.append(f'<td>{ec}</td>\n')
                parts.append('</tr>\n')
            parts.append('</tbody>\n</table>\n')

        # Stats summary for ok samples
        if s_ok:
            mapping_rates = [s.get("mapping_rate") for s in s_ok if s.get("mapping_rate") is not None]
            elapsed = [s.get("elapsed_min") for s in s_ok if s.get("elapsed_min") is not None]
            avg_map = sum(mapping_rates) / len(mapping_rates) if mapping_rates else None
            avg_elapsed = sum(elapsed) / len(elapsed) if elapsed else None

            parts.append(f"<p>Successful: {len(s_ok)} samples. "
                         f"Avg mapping rate: {_fmt(avg_map, 1)}%. "
                         f"Avg elapsed: {_fmt(avg_elapsed, 1)} min.</p>\n")

        parts.append("</div>\n")

    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Methods page (Item 16)
# ---------------------------------------------------------------------------

def render_methods() -> str:
    """Render the methods.html page."""
    parts: list[str] = []
    parts.append(_page_header("Methods — zenigoke catalog"))
    parts.append(_nav_from_root("Methods"))
    parts.append('<div class="container">\n')
    parts.append("<h1>Methods &amp; Versions</h1>\n")

    # Reference genome
    parts.append('<div class="card">\n')
    parts.append("<h2>Reference Genome</h2>\n")
    parts.append(
        "<p><strong>Marchantia polymorpha</strong> MpTak v7.1 standard genome, "
        "downloaded 2026-04-25 from "
        '<a href="https://marchantia.info/data/MpTak_v7.1_standard_genome/" target="_blank">'
        "marchantia.info</a>. "
        "MACS3 effective genome size: <strong>248,042,180 bp</strong>.</p>\n"
    )
    parts.append("</div>\n")

    # ChIP/ATAC pipeline
    parts.append('<div class="card">\n')
    parts.append("<h2>ChIP-Seq &amp; ATAC-Seq Pipeline</h2>\n")
    parts.append(
        "<p>Container: <code>ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0</code></p>\n"
        "<p>Tools:</p>\n"
        "<ul>\n"
        "<li>fastp 1.3.1 (QC + adapter trimming)</li>\n"
        "<li>bwa-mem2 2.3 (alignment)</li>\n"
        "<li>samtools 1.23.1 (sorting, deduplication)</li>\n"
        "<li>MACS3 3.0.4 (peak calling; q-value thresholds 0.05, 0.10, 0.20)</li>\n"
        "<li>bedtools 2.31.1 (BED operations)</li>\n"
        "<li>UCSC tools 482 (bigWig generation)</li>\n"
        "</ul>\n"
    )
    parts.append("</div>\n")

    # BS-seq pipeline
    parts.append('<div class="card">\n')
    parts.append("<h2>Bisulfite-Seq Pipeline</h2>\n")
    parts.append(
        "<p>Container: <code>ghcr.io/inutano/chip-atlas-pipeline-v2-bs:v1.1.0</code> "
        "+ local <code>pipeline-v2-bs-plant.sh</code> (plant fork adding CpG/CHG/CHH context).</p>\n"
        "<p>Tools:</p>\n"
        "<ul>\n"
        "<li>DNMTools 1.5.1 (methylation calling)</li>\n"
        "<li>samtools 1.22.1</li>\n"
        "</ul>\n"
        "<p>Outputs per sample: methyl bigWig + cover bigWig for CpG, CHG, CHH; "
        "HMR, hyperMR, PMD BED files for CpG; hyperMR BED for CHG.</p>\n"
    )
    parts.append("</div>\n")

    # Metadata curation
    parts.append('<div class="card">\n')
    parts.append("<h2>Metadata Curation</h2>\n")
    parts.append(
        "<p>Curation pipeline: "
        '<a href="https://github.com/dbcls/bsllmner-mk2" target="_blank">dbcls/bsllmner-mk2</a> '
        "with <code>select-config-plants.json</code> (PR #3) and local "
        "<code>select-config-zenigoke.json</code> (stage/strain/treatment) + "
        "<code>select-config-antibody.json</code> (ChIP antibody).</p>\n"
        "<p>LLM model: <strong>qwen3:27b</strong> via Ollama (local inference).</p>\n"
        "<p>Two-pass strategy: BioSample attributes (pass 1) + SRA Experiment XML "
        "for antibody (pass 2, <code>extract_experiment</code>).</p>\n"
    )
    parts.append("</div>\n")

    # Known limitations
    parts.append('<div class="card">\n')
    parts.append("<h2>Known Limitations</h2>\n")
    parts.append(
        "<ul>\n"
        "<li>One failed sample (SRX29617452): bwa-mem2 produced SAM with SEQ/QUAL "
        "length mismatch — likely a corrupted FASTQ in SRA. Manual inspection needed.</li>\n"
        "<li>ATAC-Seq samples may have lower mapping rates due to plastid/mitochondrial reads; "
        "no organelle filtering applied.</li>\n"
        "<li>BS-seq <code>duplication_rate</code> is not computed (uses <code>dnmtools uniq</code> "
        "not samtools markdup).</li>\n"
        "<li>Antibody curation depends on SRA Experiment XML metadata quality; some targets "
        "may be ambiguous or missing.</li>\n"
        "<li>Genome version: MpTak v7.1 standard (not the v7.1 alternative haplotype assembly).</li>\n"
        "</ul>\n"
    )
    parts.append("</div>\n")

    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# About page (Item 17)
# ---------------------------------------------------------------------------

def render_about() -> str:
    """Render the about.html page."""
    parts: list[str] = []
    parts.append(_page_header("About — zenigoke catalog"))
    parts.append(_nav_from_root("About"))
    parts.append('<div class="container">\n')
    parts.append("<h1>About This Catalog</h1>\n")

    parts.append('<div class="card">\n')
    parts.append("<h2>What is this?</h2>\n")
    parts.append(
        "<p>The <strong>zenigoke catalog</strong> is a static HTML catalog of public SRA experiments "
        "for <em>Marchantia polymorpha</em> (liverwort) processed through standardized epigenomics "
        "pipelines. It covers ChIP-Seq, ATAC-Seq, and Bisulfite-Seq data aligned to the "
        "MpTak v7.1 standard genome. The catalog was built in 2026 as part of a project to "
        "systematically characterize the Marchantia epigenome from publicly available data.</p>\n"
        "<p>Contact: <em>&lt;contact info to be added&gt;</em></p>\n"
    )
    parts.append("</div>\n")

    parts.append('<div class="card">\n')
    parts.append("<h2>Data Use</h2>\n")
    parts.append(
        "<p>All sequencing data originates from public SRA submissions and was processed locally. "
        "The processed outputs (BigWig, peak BED files, methylation tracks) are made available "
        "for collaboration and research use. We encourage you to cite the original data "
        "depositors when using this resource.</p>\n"
        "<p>Suggested attribution / citation:</p>\n"
        "<ul>\n"
        "<li>Pipeline: "
        '<a href="https://github.com/inutano/chip-atlas-pipeline-v2" target="_blank">'
        "chip-atlas-pipeline-v2</a> (inutano/chip-atlas-pipeline-v2)</li>\n"
        "<li>Metadata curation: dbcls/bsllmner-mk2 — "
        '<a href="https://doi.org/10.1101/2025.02.17.638570" target="_blank">'
        "doi:10.1101/2025.02.17.638570</a></li>\n"
        "<li>Reference genome: MarpolBase MpTak v7.1 — "
        '<a href="https://marchantia.info" target="_blank">marchantia.info</a></li>\n'
        "</ul>\n"
    )
    parts.append("</div>\n")

    parts.append('<div class="card">\n')
    parts.append("<h2>Database Schema &amp; Queries</h2>\n")
    parts.append(
        "<p>The catalog is backed by a SQLite database at <code>db/kknmsmd.db</code>. "
        "You can query it directly:</p>\n"
        '<pre class="log">'
        "sqlite3 db/kknmsmd.db \"SELECT accession, library_strategy, mapping_rate FROM sample "
        "WHERE library_strategy='ChIP-Seq' ORDER BY mapping_rate DESC LIMIT 10\""
        "</pre>\n"
        "<p>Tables: <code>sample</code>, <code>sample_curation</code>, "
        "<code>sample_chipseq</code>, <code>sample_bsseq</code>.</p>\n"
    )
    parts.append("</div>\n")

    parts.append("</div>\n")  # .container
    parts.append(_page_footer())
    return "".join(parts)


# ---------------------------------------------------------------------------
# Bundle shell page (Phase 3 Task 5)
# ---------------------------------------------------------------------------

def render_bundle_shell() -> str:
    """A static HTML shell that reads the bundle hash from location.hash and
    fetches the manifest via /bundle/{hash}. One shell handles all bundles —
    per-bundle data lives in report/bundles/{hash}/manifest.json.
    """
    parts: list[str] = []
    parts.append(_page_header("Bundle — zenigoke"))
    parts.append(_nav_from_root())
    parts.append("""<div class='container'>
  <h1 id='bundle-title'>Bundle</h1>
  <p class='subtitle' id='bundle-subtitle'></p>
  <div class='card'>
    <h2>Tracks</h2>
    <table id='tracks-table' class='kv'><thead>
      <tr><th>name</th><th>type</th><th>color</th><th>file</th></tr>
    </thead><tbody></tbody></table>
  </div>
  <div class='card'>
    <h2>Actions</h2>
    <button id='igv-btn'>&#9654; Send to IGV</button>
  </div>
</div>
<script>
const hash = location.hash.replace('#', '');
if (!hash) {
  document.getElementById('bundle-subtitle').textContent = 'No bundle hash in URL.';
} else {
  fetch('/bundle/' + hash).then(r => {
    if (!r.ok) throw new Error('bundle ' + hash + ' not found');
    return r.json();
  }).then(b => {
    document.getElementById('bundle-title').textContent = 'Bundle ' + b.hash;
    document.getElementById('bundle-subtitle').textContent = b.tracks.length + ' tracks';
    const tbody = document.querySelector('#tracks-table tbody');
    for (const t of b.tracks) {
      const row = document.createElement('tr');
      const fname = t.url.split('/').pop();
      row.innerHTML = '<td>' + t.name + '</td>' +
        '<td>' + t.type + '</td>' +
        '<td><span style=\"color:' + t.color + '\">&#9608;</span></td>' +
        '<td><a href=\"' + t.url + '\">' + fname + '</a></td>';
      tbody.appendChild(row);
    }
    document.getElementById('igv-btn').onclick = () => {
      const param = b.tracks.map(t => t.url + '|' + t.name).join(',');
      fetch('http://localhost:60151/load?file=' + encodeURIComponent(param))
        .catch(e => alert('Could not reach IGV at :60151. Make sure IGV is running with the port enabled.'));
    };
  }).catch(err => {
    document.getElementById('bundle-subtitle').textContent = err.message;
  });
}
</script>
""")
    parts.append(_page_footer())
    return "".join(parts)


def write_bundle_shell(out_dir: pathlib.Path) -> None:
    (out_dir / "bundle.html").write_text(render_bundle_shell())


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
            s.fastq_size,
            s.reads_filtered,
            s.reads_mapped,
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
# Stylesheet (with additions for chips, info card, failed card)
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
h3 { font-size: 0.95rem; margin: 0.6rem 0 0.4rem; color: #555; }

.card {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
}

.summary-card { border-left: 4px solid #4a90d9; }
.info-card { border-left: 4px solid #7c3aed; background: #faf5ff; }
.failed-card { border-left: 4px solid #b91c1c; background: #fff5f5; }

.badge {
  display: inline-block;
  background: #e8f0fe;
  color: #1a56db;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.82rem;
  margin-right: 0.4rem;
}

.chip-row {
  margin-bottom: 0.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.chip {
  background: #e8f0fe;
  color: #1a56db;
  border: 1px solid #aac4ff;
  border-radius: 20px;
  padding: 0.2rem 0.7rem;
  font-size: 0.82rem;
  cursor: pointer;
}

.chip:hover { background: #1a56db; color: #fff; }

.filter-row {
  margin-bottom: 0.8rem;
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

#q {
  width: 100%;
  max-width: 400px;
  padding: 0.4rem 0.6rem;
  font-size: 0.95rem;
  border: 1px solid #ccc;
  border-radius: 4px;
}

#filter-count {
  font-size: 0.85rem;
  color: #666;
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

th[data-col]:hover { background: #dce7ff; }

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

code {
  font-family: monospace;
  background: #f0f0f0;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  font-size: 0.9em;
}

ul { margin: 0.3rem 0 0.5rem 1.2rem; }
ul li { margin-bottom: 0.2rem; }
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
        "browse": 0,
        "samples": 0,
        "strategy": 0,
        "summary": 0,
        "methods": 0,
        "about": 0,
    }

    # Write stylesheet
    (assets_dir / "style.css").write_text(STYLE_CSS)

    # Write index.html (matrix scaffold)
    (out_dir / "index.html").write_text(render_index(samples))
    counts["index"] = 1

    # Write browse.html (the old all-samples table)
    (out_dir / "browse.html").write_text(render_browse(samples))
    counts["browse"] = 1

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

    # Write summary.html (Item 6: catalog-styled, no build_report dependency)
    (out_dir / "summary.html").write_text(render_summary(samples))
    counts["summary"] = 1

    # Write methods.html (Item 16)
    (out_dir / "methods.html").write_text(render_methods())
    counts["methods"] = 1

    # Write about.html (Item 17)
    (out_dir / "about.html").write_text(render_about())
    counts["about"] = 1

    # Write bundle.html shell (Phase 3 Task 5)
    write_bundle_shell(out_dir)
    counts["bundle_shell"] = 1

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
