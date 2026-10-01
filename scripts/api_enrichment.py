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


def _chrom_sizes() -> dict[str, int]:
    """Chromosome name -> length parsed from the chrom.sizes file ({} if absent)."""
    sizes: dict[str, int] = {}
    p = _chrom_sizes_path()
    if not p.exists():
        return sizes
    for line in p.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].isdigit():
            sizes[parts[0]] = int(parts[1])
    return sizes


def _genome_bp() -> int:
    """Sum of chrom sizes; cached at module level once computed."""
    if hasattr(_genome_bp, "_cached"):
        return _genome_bp._cached
    sizes = _chrom_sizes()
    if not sizes:
        return 0
    total = sum(sizes.values())
    _genome_bp._cached = total
    return total


# ---------------------------------------------------------------------------
# Blacklist (NUPT/NUMT-like regions): excluded from user BED and sample peaks
# ---------------------------------------------------------------------------

def _blacklist_path() -> pathlib.Path | None:
    raw = os.getenv("ZENIGOKE_BLACKLIST")
    if raw is None:
        raw = str(pathlib.Path(__file__).resolve().parent.parent / "configs" / "organelle_like_regions.MpTak_v7.1.bed")
    p = pathlib.Path(raw) if raw else None
    return p if p is not None and p.is_file() else None


def _cache_dir() -> pathlib.Path:
    d = pathlib.Path(os.getenv("ZENIGOKE_ENRICH_CACHE", "/tmp/zenigoke-enrich")) / "blacklisted"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _filtered(path: pathlib.Path) -> pathlib.Path:
    """Return `path` minus blacklist overlaps (cached by path + mtimes)."""
    bl = _blacklist_path()
    if bl is None:
        return path
    key = hashlib.sha256(f"{path}:{path.stat().st_mtime_ns}:{bl}:{bl.stat().st_mtime_ns}".encode()).hexdigest()[:24]
    out = _cache_dir() / f"{key}.bed"
    if not out.exists():
        res = subprocess.run(["bedtools", "intersect", "-v", "-a", str(path), "-b", str(bl)],
                             check=True, capture_output=True, timeout=60)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(res.stdout)
        tmp.replace(out)
    return out


def _blacklist_bp(chrom_sizes: dict[str, int]) -> int:
    """Blacklisted bp clipped to the chromosomes being tested."""
    bl = _blacklist_path()
    if bl is None:
        return 0
    n = 0
    for line in bl.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] in chrom_sizes:
            s, e = max(0, int(parts[1])), min(int(parts[2]), chrom_sizes[parts[0]])
            n += max(0, e - s)
    return n


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
    try:
        peak_path = _filtered(peak_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
    bl = _blacklist_path()
    if bl is not None:
        h.update(f"{bl}:{bl.stat().st_mtime_ns}".encode())
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
    # Genome size excludes blacklisted bp so p_null matches the filtered peaks.
    genome_bp = _genome_bp()
    if genome_bp:
        genome_bp = max(0, genome_bp - _blacklist_bp(_chrom_sizes()))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".bed", delete=False) as tf:
        for chrom, start, end in regions:
            tf.write(f"{chrom}\t{start}\t{end}\n")
        raw_bed_path = tf.name

    filtered_bed_path = raw_bed_path
    try:
        try:
            filtered_bed_path = str(_filtered(pathlib.Path(raw_bed_path)))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise ValueError("could not process BED regions (check coordinates)")
        with open(filtered_bed_path) as fh:
            n_user = sum(1 for ln in fh if len(ln.rstrip("\n").split("\t")) >= 3)
        if n_user == 0:
            raise ValueError("no BED regions remain after excluding blacklisted (organelle-like) regions")
        args = [(s, req.q_cutoff, filtered_bed_path, n_user, genome_bp) for s in samples]
        results: list[dict] = []
        # Sequential is fine for small catalogs; switch to ProcessPoolExecutor
        # for >50 samples (test env stays sequential to avoid pickling fastapi).
        for a in args:
            r = _score_one(a)
            if r is not None:
                results.append(r)
    finally:
        # The filtered copy of a per-request temp file is never reused; drop it
        # so the blacklist cache only accumulates sample peak files.
        for pth in {raw_bed_path, filtered_bed_path}:
            try:
                os.unlink(pth)
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
