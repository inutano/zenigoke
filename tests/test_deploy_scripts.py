"""Deploy scripts run against stub commands (no AWS, no sudo, no network)."""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SYNC = REPO / "deploy" / "aws" / "02-sync-data.sh"
BOOT = REPO / "deploy" / "aws" / "04-ec2-bootstrap.sh"

# Each stub appends "<name> <args...>" to $STUB_LOG. `aws s3 cp <s3://src> <local>` creates
# the local file; `sudo tee FILE` saves stdin to $STUB_DIR/tee.<basename FILE>.
STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0")" in
  aws) if [ "$1 $2" = "s3 cp" ] && [ "${4#s3://}" = "$4" ]; then : > "$4"; fi ;;
  sudo) if [ "$1" = "tee" ]; then cat > "$STUB_DIR/tee.$(basename "${@: -1}")"; fi ;;
  curl) case "$*" in *checkip*) echo 192.0.2.1 ;; esac ;;
  dig) echo 192.0.2.1 ;;
esac
exit 0
"""


def _stubs(tmp_path: pathlib.Path, names) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for n in names:
        p = bin_dir / n
        p.write_text(STUB)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.touch()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log),
           "STUB_DIR": str(tmp_path)}
    for k in ("ZENIGOKE_BUCKET", "ZENIGOKE_DB_PATH", "ZENIGOKE_OUTPUT_DIR"):
        env.pop(k, None)
    return env


def _calls(tmp_path, prefix):
    return [ln for ln in (tmp_path / "calls.log").read_text().splitlines() if ln.startswith(prefix)]


def _run_sync(tmp_path, *args):
    env = _stubs(tmp_path, ["aws"])
    (tmp_path / "out").mkdir()
    (tmp_path / "k.db").write_text("x")
    env.update(ZENIGOKE_OUTPUT_DIR=str(tmp_path / "out"), ZENIGOKE_DB_PATH=str(tmp_path / "k.db"))
    return subprocess.run(["bash", str(SYNC), *args], env=env, capture_output=True, text=True, timeout=60)


def test_sync_data_default_run(tmp_path):
    r = _run_sync(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(tmp_path, "aws ")
    sync = [c for c in calls if c.startswith("aws s3 sync")]
    assert len(sync) == 1 and "s3://zenigoke-catalog-dsc/output" in sync[0]
    assert "--size-only" not in sync[0]          # same-size changed files must not be skipped
    assert "--delete" not in sync[0] and not any("--dryrun" in c for c in calls)
    assert any(c.startswith("aws s3 cp") and "s3://zenigoke-catalog-dsc/db/kknmsmd.db" in c for c in calls)
    bed = [c for c in calls if "organelle_like_regions.MpTak_v7.1.bed" in c]
    assert len(bed) == 1
    assert str(REPO / "configs" / "organelle_like_regions.MpTak_v7.1.bed") in bed[0]
    assert ("s3://zenigoke-catalog-dsc/references/MpTak_v7.1/organelle_like_regions.MpTak_v7.1.bed"
            in bed[0])


def test_sync_data_dryrun_and_delete_flags(tmp_path):
    r = _run_sync(tmp_path, "--dryrun", "--delete", "other-bucket")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(tmp_path, "aws ")
    assert len(calls) == 3
    assert all("--dryrun" in c for c in calls)               # every aws call
    assert all("s3://other-bucket/" in c for c in calls)
    assert [c.startswith("aws s3 sync") for c in calls if "--delete" in c] == [True]   # output sync only


def test_sync_data_rejects_unknown_option(tmp_path):
    r = _run_sync(tmp_path, "--size-only")
    assert r.returncode != 0
    assert _calls(tmp_path, "aws ") == []


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_ec2_bootstrap_rerun(tmp_path):
    env = _stubs(tmp_path, ["aws", "sudo", "curl", "dig", "git", "pip", "sleep"])
    home = tmp_path / "home"
    (home / "zenigoke").mkdir(parents=True)                  # existing checkout: re-run path
    env["HOME"] = str(home)
    r = subprocess.run(["bash", str(BOOT)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    aws = _calls(tmp_path, "aws ")
    peaks = [c for c in aws if c.startswith("aws s3 sync") and "/output" in c]
    assert len(peaks) == 1 and "--delete" in peaks[0]        # stale v1 peak files are removed
    # the DB is refreshed on every run, via a temp file renamed into place
    db_cp = [c for c in aws if "db/kknmsmd.db" in c]
    assert len(db_cp) == 1
    assert (home / "zenigoke" / "db" / "kknmsmd.db").exists()
    assert not (home / "zenigoke" / "db" / "kknmsmd.db.tmp").exists()
    env_file = (tmp_path / "tee.zenigoke.env").read_text()
    assert "ZENIGOKE_ENRICH_CACHE=/var/cache/zenigoke-enrich\n" in env_file
    sudo = _calls(tmp_path, "sudo ")
    assert any(c.startswith("sudo install -d") and c.endswith("/var/cache/zenigoke-enrich") for c in sudo)
    # a re-run must restart the service so it picks up the new env, DB and peaks
    assert any(c == "sudo systemctl restart zenigoke" for c in sudo)
