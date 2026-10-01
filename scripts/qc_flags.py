"""QC flags for the zenigoke catalog (thresholds from the 2026-10-01 review).

Pure functions: metrics dict -> flag. Missing metrics are skipped, so the same
rules work on subsample estimates (docs/qc TSVs) and on zenigoke-v2 pipeline
stats. A sample that completed but has no metrics at all gets "NA".

Reason strings print rates with one decimal (more when rounding would print the
limit of a strict test, or the FAIL limit above a WARN-range value), so a value
just under a threshold never reads like "70% < 70%" or "80.0% ≥ 50%".

Organelle (ChIP/ATAC) has two bases: `organelle_pct` is the share of
fastp-passed reads (2026-10-01 subsample) and is what the >= 20% rule was set
on; `organelle_dedup_pct` is the zenigoke-v2 pipeline share of deduplicated
usable records. The rule uses `organelle_pct` when present and falls back to
`organelle_dedup_pct` (same threshold, its own label) only when it is absent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Sample-page labels of the displayed QC metrics, in page order (metric key ->
# label). build_catalog_db records which of them came from the 2026-10-01 QC
# TSVs (sample_qc.subsample_fields, ", "-joined) and build_catalog_pages uses
# the same strings as row labels, so labels must not contain commas.
CHIP_QC_LABELS: Dict[str, str] = {
    "aligned_pct": "alignment rate",
    "dup_pct": "duplication rate",
    "usable_fragments": "usable fragments",
    "organelle_pct": "organelle reads",
    "foreign_pct": "non-Marchantia DNA",
    "peaks_in_organelle_like_pct": "peaks in organelle-like regions",
}
BS_QC_LABELS: Dict[str, str] = {
    "nonconv": "non-conversion (chloroplast CHH)",
    "foreign_pct": "non-Marchantia reads",
    "frac_cpg_cov_ge5": "CpGs covered ≥5×",
}
ORGANELLE_DEDUP_LABEL = "organelle share of deduplicated reads"


def _num(m: Dict[str, Any], k: str) -> Optional[float]:
    v = m.get(k)
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _vs(x: float, *limits: float) -> str:
    """x with one decimal, or more when rounding would print one of `limits`.

    Pass the limit of a strict test ("70% < 70%") and, for a WARN-range value, the
    FAIL limit above it too, so a WARN never reads like a FAIL ("80.0% ≥ 50%").
    """
    for d in range(1, 7):
        s = f"{x:.{d}f}"
        if float(s) not in limits:
            return s
    return s


def _worst(fails: List[str], warns: List[str], any_metric: bool) -> str:
    if fails:
        return "FAIL"
    if warns:
        return "WARN"
    return "PASS" if any_metric else "NA"


def chip_flag(m: Dict[str, Any]) -> Tuple[str, List[str], str]:
    if m.get("status") not in (None, "ok"):
        return "FAIL", ["pipeline did not complete"], "NA"
    a, d = _num(m, "aligned_pct"), _num(m, "dup_pct")
    frag, org = _num(m, "usable_fragments"), _num(m, "organelle_pct")
    org_label = CHIP_QC_LABELS["organelle_pct"]
    if org is None:
        org, org_label = _num(m, "organelle_dedup_pct"), ORGANELLE_DEDUP_LABEL
    hum, bac = _num(m, "human_pct"), _num(m, "bacterial_pct")
    fails: List[str] = []
    warns: List[str] = []
    if a is not None:
        if a < 30:
            fails.append(f"alignment rate {_vs(a, 30)}% < 30%")
        elif a < 70:
            warns.append(f"alignment rate {_vs(a, 70)}% < 70%")
    if d is not None:
        if d >= 80:
            fails.append(f"duplication {d:.1f}% ≥ 80%")
        elif d >= 50:
            warns.append(f"duplication {_vs(d, 80)}% ≥ 50%")
    if frag is not None:
        if frag < 1e6:
            fails.append(f"{_vs(frag / 1e6, 1)}M usable fragments < 1M")
        elif frag < 4e6:
            warns.append(f"{_vs(frag / 1e6, 4)}M usable fragments < 4M")
    if hum is not None or bac is not None:
        foreign = (hum or 0) + (bac or 0)
        if foreign >= 10:
            parts = []
            if bac and bac >= 1:
                parts.append(f"bacterial-like {bac:.1f}%")
            if hum and hum >= 1:
                parts.append(f"human {hum:.1f}%")
            warns.append("non-Marchantia DNA " + ", ".join(parts))
    if org is not None and org >= 20:
        warns.append(f"{org_label} {org:.1f}%")
    any_metric = any(x is not None for x in (a, d, frag, org, hum, bac))

    # peak_flag is reported separately; it does not change qc_flag on its own,
    # but its reason is listed after the others.
    p05, pov = _num(m, "peaks_q05"), _num(m, "peaks_in_organelle_like_pct")
    peak_reasons: List[str] = []
    if not p05:
        peak_flag = "NA"
    elif pov is not None and pov >= 20 and p05 >= 50:
        peak_flag = "WARN"
        peak_reasons.append(f"{pov:.1f}% of peaks in organelle-like (NUPT/NUMT) regions")
    else:
        peak_flag = "OK"
    return _worst(fails, warns, any_metric), fails + warns + peak_reasons, peak_flag


def bs_flag(m: Dict[str, Any]) -> Tuple[str, List[str]]:
    if m.get("status") not in (None, "ok"):
        return "FAIL", ["pipeline did not complete"]
    cg, chg, chh = _num(m, "mean_cpg"), _num(m, "mean_chg"), _num(m, "mean_chh")
    nc, fo, cov = _num(m, "nonconv"), _num(m, "foreign_pct"), _num(m, "frac_cpg_cov_ge5")
    fails: List[str] = []
    warns: List[str] = []
    if None not in (cg, chg, chh) and min(cg, chg, chh) >= 0.9:
        fails.append("methylation ≥ 0.9 in all contexts (reads mapped to the wrong strand)")
    if nc is not None:
        if nc >= 0.05:
            fails.append(f"non-conversion {nc * 100:.1f}% ≥ 5%")
        elif nc > 0.02:
            warns.append(f"non-conversion {_vs(nc * 100, 2, 5)}% > 2%")
    if fo is not None and fo >= 50:
        warns.append(f"non-Marchantia reads {fo:.1f}%")
    if cov is not None and cov < 0.05:
        warns.append(f"only {_vs(cov * 100, 5)}% of CpGs covered ≥5×")
    any_metric = any(x is not None for x in (cg, chg, chh, nc, fo, cov))
    return _worst(fails, warns, any_metric), fails + warns


def chip_metrics_from_tsv(r: Dict[str, str]) -> Dict[str, Any]:
    """Map a docs/qc/2026-10-01-chip-atlas-qc.tsv row to chip_flag() input."""
    return {
        "status": r.get("status") or "ok",
        "aligned_pct": r.get("aligned_pct"),
        "dup_pct": r.get("dup_pct"),
        "usable_fragments": r.get("usable_fragments"),
        "organelle_pct": r.get("organelle_pct"),
        "human_pct": r.get("human_pct"),
        "bacterial_pct": r.get("other_unmapped_pct"),
        "peaks_q05": r.get("peaks_q05"),
        "peaks_in_organelle_like_pct": r.get("peaks_in_organelle_like_pct"),
    }


def bs_metrics_from_tsv(r: Dict[str, str]) -> Dict[str, Any]:
    """Map a docs/qc/2026-10-01-bsseq-qc.tsv row to bs_flag() input."""
    unmapped = _num(r, "pct_reads_unmapped_all_refs")
    bacteria = _num(r, "pct_reads_bacteria_panel")
    foreign = None if unmapped is None and bacteria is None else (unmapped or 0) + (bacteria or 0)
    return {
        "status": "ok",
        "mean_cpg": r.get("mean_cpg"),
        "mean_chg": r.get("mean_chg"),
        "mean_chh": r.get("mean_chh"),
        "nonconv": r.get("nonconv_chrC_CHH"),
        "foreign_pct": foreign,
        "frac_cpg_cov_ge5": r.get("frac_cpg_cov_ge5"),
    }
