"""POST /api/bundle — build consensus BEDs and return IGV track manifest.

Synchronous bedtools shell-out per strategy. Cache key is sha256 of the
sorted sample list + q-cutoff. Cached artifacts live in $ZENIGOKE_BUNDLES_DIR.
"""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_axes import _db
from igv_url_helper import per_sample_tracks, consensus_track, color_for_group

router = APIRouter()


class GroupSpec(BaseModel):
    label: str
    accessions: List[str]


class BundleRequest(BaseModel):
    accessions: List[str] = Field(..., min_length=1)
    q_cutoff: str = "1e-10"
    groups: List[GroupSpec] = []


def _hash(accs: list[str], q: str) -> str:
    s = ",".join(sorted(set(accs))) + "|" + q
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _bundles_dir() -> pathlib.Path:
    p = pathlib.Path(os.getenv("ZENIGOKE_BUNDLES_DIR", "report/bundles"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _peak_path_for(sample: dict, q_cutoff: str, report_dir: pathlib.Path) -> pathlib.Path | None:
    """Locate the per-sample peak file at the requested q-cutoff."""
    acc = sample["accession"]
    strat = sample["library_strategy"]
    if strat in ("ChIP-Seq", "ATAC-Seq"):
        sub = "chipseq" if strat == "ChIP-Seq" else "atacseq"
        q_label = {"1e-5": "05", "1e-10": "10", "1e-20": "20"}[q_cutoff]
        return report_dir / "output" / sub / acc / f"{acc}.{q_label}_peaks.narrowPeak"
    if strat == "Bisulfite-Seq":
        return report_dir / "output" / "bsseq" / acc / f"{acc}.CpG.hmr.bed"
    return None


def _load_samples(accessions: list[str]) -> list[dict]:
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        placeholder = ",".join("?" for _ in accessions)
        rows = conn.execute(f"""
            SELECT s.accession, s.library_strategy, s.status,
                   c.antibody_target, c.genotype_strain
            FROM sample s LEFT JOIN sample_curation c USING (accession)
            WHERE s.accession IN ({placeholder})
            ORDER BY s.accession
        """, accessions).fetchall()
    return [dict(r) for r in rows]


def build_bundle(req: BundleRequest) -> dict:
    if not req.accessions:
        raise ValueError("accessions cannot be empty")
    h = _hash(req.accessions, req.q_cutoff)
    out_dir = _bundles_dir() / h
    manifest_path = out_dir / "manifest.json"

    if manifest_path.exists():
        return json.loads(manifest_path.read_text())

    out_dir.mkdir(parents=True, exist_ok=True)
    samples = _load_samples(req.accessions)
    report_dir = pathlib.Path(os.getenv("ZENIGOKE_REPORT_DIR", "report"))

    by_strategy: dict[str, list[dict]] = {}
    for s in samples:
        if s["status"] != "ok":
            continue
        by_strategy.setdefault(s["library_strategy"], []).append(s)

    consensus_tracks: list[dict] = []
    warnings: list[str] = []
    for strat, strat_samples in by_strategy.items():
        if len(strat_samples) < 2:
            continue
        peak_files = [_peak_path_for(s, req.q_cutoff, report_dir) for s in strat_samples]
        peak_files = [p for p in peak_files if p and p.exists()]
        if len(peak_files) < 2:
            warnings.append(f"{strat}: not enough peak files; skipped consensus")
            continue
        out_bed = out_dir / f"consensus.{strat}.bed"
        cmd = (
            f"cat {' '.join(str(p) for p in peak_files)} "
            "| sort -k1,1 -k2,2n "
            f"| bedtools merge -i - > {out_bed}"
        )
        try:
            subprocess.run(["bash", "-c", cmd], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            warnings.append(f"{strat}: bedtools failed: {e.stderr.decode()[-200:]}")
            continue
        consensus_tracks.append(consensus_track(strat, h, req.q_cutoff, len(strat_samples)))

    acc_to_group: dict[str, int] = {}
    for idx, g in enumerate(req.groups):
        for a in g.accessions:
            acc_to_group[a] = idx
    sample_tracks: list[dict] = []
    for s in samples:
        if s["status"] != "ok":
            continue
        gidx = acc_to_group.get(s["accession"], 0)
        s_with_group = dict(s, _group=req.groups[gidx].label if gidx < len(req.groups) else "")
        sample_tracks.extend(per_sample_tracks(s_with_group, req.q_cutoff, color_for_group(gidx)))

    manifest = {
        "hash": h,
        "drilldown_url": f"/bundle/{h}",
        "consensus_url": consensus_tracks[0]["url"] if consensus_tracks else None,
        "tracks": consensus_tracks + sample_tracks,
        "warnings": warnings,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


@router.post("/api/bundle")
def bundle_endpoint(req: BundleRequest) -> dict:
    try:
        return build_bundle(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/bundle/{hash_id}")
def bundle_drilldown(hash_id: str) -> dict:
    """Return the manifest JSON. The HTML page is generated by
    build_catalog_pages.py (Task 5); this endpoint serves the raw JSON
    that the page fetches."""
    manifest_path = _bundles_dir() / hash_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="bundle not found")
    return json.loads(manifest_path.read_text())
