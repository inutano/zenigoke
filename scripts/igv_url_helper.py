"""Helpers to build IGV-ready track URL lists.

URL templating is governed by env vars so the same code runs in local mode
(URLs point at the catalog server) and cloud mode (URLs point at S3).
"""
from __future__ import annotations
import os

TRACK_GROUP_PALETTE = [
    "#3060a0", "#a04030", "#308050", "#a0a030",
    "#603090", "#308090", "#a07030", "#7060a0",
]


def _public_base() -> str:
    return os.getenv("ZENIGOKE_PUBLIC_BASE", "http://localhost:8088").rstrip("/")


def _bundles_public() -> str:
    return os.getenv("ZENIGOKE_BUNDLES_PUBLIC", f"{_public_base()}/bundles").rstrip("/")


def per_sample_tracks(sample: dict, q_cutoff: str, color: str) -> list[dict]:
    """Return the IGV track entries for one sample."""
    acc = sample["accession"]
    strat = sample["library_strategy"]
    base = _public_base()
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        sub = "chipseq" if strat == "ChIP-Seq" else "atacseq"
        q_label = {"1e-5": "05", "1e-10": "10", "1e-20": "20"}[q_cutoff]
        return [
            {"name": f"{acc} bigwig", "url": f"{base}/output/{sub}/{acc}/{acc}.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} peaks q≤{q_cutoff}",
             "url": f"{base}/output/{sub}/{acc}/{acc}.{q_label}_peaks.narrowPeak",
             "type": "annotation", "color": color, "group": sample.get("_group", "")},
        ]
    if strat == "Bisulfite-Seq":
        return [
            {"name": f"{acc} CpG methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CpG.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} CHG methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CHG.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
            {"name": f"{acc} CHH methyl",
             "url": f"{base}/output/bsseq/{acc}/{acc}.CHH.methyl.bw",
             "type": "wig", "color": color, "group": sample.get("_group", "")},
        ]
    return []


def consensus_track(strategy: str, hash_id: str, q_cutoff: str, n_samples: int) -> dict:
    return {
        "name": f"consensus {strategy} q≤{q_cutoff} (n={n_samples})",
        "url": f"{_bundles_public()}/{hash_id}/consensus.{strategy}.bed",
        "type": "annotation",
        "color": "#1a9970",
        "group": "consensus",
    }


def color_for_group(idx: int) -> str:
    return TRACK_GROUP_PALETTE[idx % len(TRACK_GROUP_PALETTE)]
