"""Collect zenigoke pipeline outputs + curated metadata, render an HTML report.

Stdlib only. Two public entry points:
  collect_samples(...)  -> list[dict]
  render_html(samples)  -> str
"""
from __future__ import annotations

import csv
import html
import json
import pathlib
from typing import Any

STRATEGY_DIRS = {
    "ChIP-Seq": "chipseq",
    "ATAC-Seq": "atacseq",
    "Bisulfite-Seq": "bsseq",
}


def _read_stats_tsv(path: pathlib.Path) -> list[str]:
    """Return the single data row split into fields, or empty list if missing."""
    if not path.exists():
        return []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            return line.split("\t")
    return []


def _read_failed(path: pathlib.Path) -> dict[str, str]:
    info: dict[str, str] = {"exit_code": "", "strategy": "", "log_snippet": ""}
    if not path.exists():
        return info
    text = path.read_text()
    snippet_lines: list[str] = []
    in_snippet = False
    for line in text.splitlines():
        if line.startswith("exit_code="):
            info["exit_code"] = line.split("=", 1)[1].strip()
        elif line.startswith("strategy="):
            info["strategy"] = line.split("=", 1)[1].strip()
        elif "last 50 lines of log" in line:
            in_snippet = True
        elif in_snippet:
            snippet_lines.append(line)
    info["log_snippet"] = "\n".join(snippet_lines).strip()
    return info


def _strategy_from_output(output_root: pathlib.Path, acc: str) -> str | None:
    for strat, sub in STRATEGY_DIRS.items():
        if (output_root / sub / acc).exists():
            return strat
    return None


def _chipseq_stats_fields(row: list[str]) -> dict[str, str]:
    # Upstream pipeline-v2.sh stats.tsv is 15 columns; see pipeline README.
    keys = [
        "sample", "layout", "fastq_size", "reads_raw", "reads_filt",
        "mapping_rate", "duplication_rate", "dedup_bam_size",
        "bedgraph_size", "bigwig_size", "peaks_q5", "peaks_q10",
        "peaks_q20", "elapsed_min", "extra",
    ]
    return {k: v for k, v in zip(keys, row)}


def _bsseq_stats_fields(row: list[str]) -> dict[str, str]:
    # Upstream 11 columns + our 3 extended columns (MEAN_CPG/CHG/CHH).
    keys = [
        "sample", "layout", "fastq_size", "dedup_bam_size", "read_count",
        "mapping_rate", "methylation_rate", "cpg_coverage",
        "hmr_count", "pmd_count", "hypermr_count", "elapsed_min",
        "mean_cpg", "mean_chg", "mean_chh",
    ]
    return {k: v for k, v in zip(keys, row)}


def collect_samples(
    data_root: pathlib.Path,
    output_root: pathlib.Path,
    metadata_root: pathlib.Path,
    csv_path: pathlib.Path | None,
) -> list[dict[str, Any]]:
    status_dir = data_root / "status"
    curated_dir = metadata_root / "curated"

    # Seed strategy from CSV when provided (so failed samples still know type)
    csv_strategies: dict[str, str] = {}
    if csv_path and csv_path.exists():
        with csv_path.open() as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                csv_strategies[row["experiment_accession"]] = row["library_strategy"]

    samples: list[dict[str, Any]] = []
    if not status_dir.exists():
        return samples

    for marker in sorted(status_dir.iterdir()):
        acc = marker.stem
        if marker.suffix == ".ok":
            status = "ok"
        elif marker.suffix == ".failed":
            status = "failed"
        else:
            continue

        sample: dict[str, Any] = {"accession": acc, "status": status}
        strat = _strategy_from_output(output_root, acc) or csv_strategies.get(acc, "")

        if status == "failed":
            sample.update(_read_failed(marker))
            sample.setdefault("strategy", strat)
        else:
            sample["strategy"] = strat
            if strat in ("ChIP-Seq", "ATAC-Seq"):
                stats = _read_stats_tsv(
                    output_root / STRATEGY_DIRS[strat] / acc / f"{acc}.stats.tsv"
                )
                sample.update(_chipseq_stats_fields(stats))
            elif strat == "Bisulfite-Seq":
                stats = _read_stats_tsv(
                    output_root / "bsseq" / acc / f"{acc}.stats.tsv"
                )
                sample.update(_bsseq_stats_fields(stats))

        curated_path = curated_dir / f"{acc}.json"
        sample["curated"] = (
            json.loads(curated_path.read_text()) if curated_path.exists() else {}
        )
        samples.append(sample)

    return samples


def _esc(x: Any) -> str:
    return html.escape(str(x)) if x is not None else ""


def render_html(samples: list[dict[str, Any]]) -> str:
    n_total = len(samples)
    n_ok = sum(1 for s in samples if s["status"] == "ok")
    n_failed = n_total - n_ok

    by_strat: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        by_strat.setdefault(s.get("strategy") or "unknown", []).append(s)

    parts: list[str] = []
    parts.append(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>zenigoke phase 1 summary</title>"
        "<style>"
        "body{font:14px/1.4 system-ui,sans-serif;margin:2rem;color:#222}"
        "h1{font-size:1.5rem}h2{margin-top:2rem;font-size:1.2rem}"
        "table{border-collapse:collapse;margin:0.5rem 0;font-size:12px}"
        "th,td{border:1px solid #ccc;padding:0.25rem 0.5rem;text-align:left}"
        "th{background:#f4f4f4}.failed{background:#fee}.ok{background:#efe}"
        "pre{font:11px monospace;background:#f8f8f8;padding:0.25rem;max-width:40em;white-space:pre-wrap}"
        "</style></head><body>"
    )
    parts.append(
        f"<h1>zenigoke phase 1 summary</h1>"
        f"<p>{n_total} samples — {n_ok} ok, {n_failed} failed.</p>"
    )

    if n_failed:
        parts.append("<h2>Failed samples</h2>")
        parts.append("<table><tr><th>accession</th><th>strategy</th><th>exit</th><th>log snippet</th></tr>")
        for s in samples:
            if s["status"] != "failed":
                continue
            parts.append(
                f"<tr class='failed'><td>{_esc(s['accession'])}</td>"
                f"<td>{_esc(s.get('strategy'))}</td>"
                f"<td>{_esc(s.get('exit_code'))}</td>"
                f"<td><pre>{_esc(s.get('log_snippet'))}</pre></td></tr>"
            )
        parts.append("</table>")

    for strat, rows in by_strat.items():
        ok_rows = [r for r in rows if r["status"] == "ok"]
        if not ok_rows:
            continue
        parts.append(f"<h2>{_esc(strat)} ({len(ok_rows)} samples)</h2>")
        # Column set picked per strategy
        if strat in ("ChIP-Seq", "ATAC-Seq"):
            cols = ["accession", "mapping_rate", "duplication_rate",
                    "peaks_q5", "peaks_q10", "peaks_q20",
                    "antibody_target", "tissue", "developmental_stage",
                    "genotype_strain", "elapsed_min"]
        elif strat == "Bisulfite-Seq":
            cols = ["accession", "mapping_rate", "mean_cpg", "mean_chg", "mean_chh",
                    "hmr_count", "pmd_count", "tissue", "developmental_stage",
                    "genotype_strain", "elapsed_min"]
        else:
            cols = ["accession", "strategy"]
        parts.append("<table><tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>")
        for r in ok_rows:
            cur = r.get("curated") or {}
            vals = []
            for c in cols:
                if c in ("tissue", "developmental_stage", "genotype_strain",
                         "treatment", "antibody_target"):
                    vals.append(cur.get(c, "") or "")
                else:
                    vals.append(r.get(c, "") or "")
            parts.append("<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in vals) + "</tr>")
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", default="/data1/zenigoke")
    p.add_argument("--metadata-root", default="metadata")
    p.add_argument("--csv", default="zenigoke_sra_experiments.csv")
    p.add_argument("--output", default="report/phase1-summary.html")
    args = p.parse_args(argv)

    data_root = pathlib.Path(args.data_root)
    metadata_root = pathlib.Path(args.metadata_root)
    csv_path = pathlib.Path(args.csv) if args.csv else None
    output = pathlib.Path(args.output)

    samples = collect_samples(data_root, data_root / "output", metadata_root, csv_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(samples))
    print(f"wrote {output} with {len(samples)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
