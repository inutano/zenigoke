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
# `sudo gpg ... -o FILE` mimics real gpg on a re-run: FILE "exists" once a previous run wrote it,
# and without --batch --yes gpg would prompt to overwrite on /dev/tty, which a headless run
# cannot answer (exit 2). `sudo systemctl restart zenigoke` snapshots $STUB_SNAPSHOT_DIR (if
# set) to $STUB_DIR/at-restart.txt, to show what the service restart sees.
STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0")" in
  aws) if [ "$1 $2" = "s3 cp" ] && [ "${4#s3://}" = "$4" ]; then : > "$4"; fi ;;
  sudo)
    case "$1" in
      tee) cat > "$STUB_DIR/tee.$(basename "${@: -1}")" ;;
      gpg)
        cat > /dev/null
        out="$STUB_DIR/gpg-out.$(basename "${@: -1}")"
        case " $* " in *" --batch "*) batch=1 ;; *) batch= ;; esac
        case " $* " in *" --yes "*) yes=1 ;; *) yes= ;; esac
        if [ -e "$out" ] && { [ -z "$batch" ] || [ -z "$yes" ]; }; then
          echo "gpg: $out already exists. Overwrite? (y/N)" >&2; exit 2
        fi
        : > "$out" ;;
      systemctl)
        if [ "$2 $3" = "restart zenigoke" ] && [ -n "${STUB_SNAPSHOT_DIR:-}" ]; then
          ls -A "$STUB_SNAPSHOT_DIR" > "$STUB_DIR/at-restart.txt"
        fi ;;
    esac ;;
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


def _boot_env(tmp_path, cache=None):
    """Stubbed environment for a bootstrap run on an instance that already has a checkout."""
    env = _stubs(tmp_path, ["aws", "sudo", "curl", "dig", "git", "pip", "sleep"])
    home = tmp_path / "home"
    (home / "zenigoke").mkdir(parents=True)                  # existing checkout: re-run path
    env["HOME"] = str(home)
    # never let a test see (or clear) a real /var/cache/zenigoke-enrich on the host running it
    env["ZENIGOKE_ENRICH_CACHE"] = str(cache or tmp_path / "enrich-cache")
    return env, home


def _run_boot(env):
    return subprocess.run(["bash", str(BOOT)], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_ec2_bootstrap_rerun(tmp_path):
    env, home = _boot_env(tmp_path)
    r = _run_boot(env)
    assert r.returncode == 0, r.stdout + r.stderr
    aws = _calls(tmp_path, "aws ")
    peaks = [c for c in aws if c.startswith("aws s3 sync") and "/output" in c]
    assert len(peaks) == 1 and "--delete" in peaks[0]        # stale v1 peak files are removed
    # the DB is refreshed on every run, via a temp file renamed into place
    db_cp = [c for c in aws if "db/kknmsmd.db" in c]
    assert len(db_cp) == 1
    assert (home / "zenigoke" / "db" / "kknmsmd.db").exists()
    assert not (home / "zenigoke" / "db" / "kknmsmd.db.tmp").exists()
    cache = env["ZENIGOKE_ENRICH_CACHE"]
    env_file = (tmp_path / "tee.zenigoke.env").read_text()
    assert f"ZENIGOKE_ENRICH_CACHE={cache}\n" in env_file
    sudo = _calls(tmp_path, "sudo ")
    assert any(c.startswith("sudo install -d") and c.endswith(cache) for c in sudo)
    # a re-run must restart the service so it picks up the new env, DB and peaks
    assert any(c == "sudo systemctl restart zenigoke" for c in sudo)


def test_ec2_bootstrap_cache_default_is_var_cache():
    # /var/cache survives reboots (the /tmp default of the API does not); override is for tests
    assert 'ENRICH_CACHE="${ZENIGOKE_ENRICH_CACHE:-/var/cache/zenigoke-enrich}"' in BOOT.read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_ec2_bootstrap_runs_twice_with_existing_caddy_keyring(tmp_path):
    """Second run finds the caddy keyring already written; gpg must not prompt to overwrite it."""
    env, _ = _boot_env(tmp_path)
    first = _run_boot(env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (tmp_path / "gpg-out.caddy-stable-archive-keyring.gpg").exists()
    second = _run_boot(env)                                  # no tty, set -euo pipefail
    assert second.returncode == 0, second.stdout + second.stderr
    gpg = _calls(tmp_path, "sudo gpg")
    assert len(gpg) == 2 and all("--batch" in c and "--yes" in c and "--dearmor" in c for c in gpg)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_ec2_bootstrap_keeps_local_caddyfile_on_caddy_upgrade(tmp_path):
    """The script overwrites /etc/caddy/Caddyfile (a package conffile); a later `apt-get install
    caddy` upgrade must keep it instead of stopping at dpkg's conffile prompt."""
    env, _ = _boot_env(tmp_path)
    assert _run_boot(env).returncode == 0
    caddy = [c for c in _calls(tmp_path, "sudo apt-get install") if c.endswith(" caddy")]
    assert len(caddy) == 1 and "Dpkg::Options::=--force-confold" in caddy[0]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_ec2_bootstrap_clears_blacklist_cache_before_restart(tmp_path):
    """Filtered-peak cache entries are keyed by path+mtime, so peaks removed or replaced by the
    --delete sync would leave orphans forever: every refresh clears them before the restart."""
    cache = tmp_path / "enrich-cache"
    bl = cache / "blacklisted"
    bl.mkdir(parents=True)
    for name in ("0123abcd.bed", "4567ef01.tmp"):
        (bl / name).write_text("chr1\t1\t2\n")
    (cache / "keep.txt").write_text("not ours")               # only blacklisted/ is pruned
    env, _ = _boot_env(tmp_path, cache)
    env["STUB_SNAPSHOT_DIR"] = str(bl)
    r = _run_boot(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "at-restart.txt").read_text() == ""    # already empty when the service restarts
    assert bl.is_dir() and list(bl.iterdir()) == []
    assert (cache / "keep.txt").exists()


BETA = REPO / "deploy" / "aws" / "05-sync-beta.sh"


def _run_beta(tmp_path, *args):
    env = _stubs(tmp_path, ["aws"])
    env["ZENIGOKE_SKIP_BUILD"] = "1"      # never rebuild the real web/data/catalog.json in a test
    return env, subprocess.run(["bash", str(BETA), *args], env=env, capture_output=True, text=True,
                               timeout=60)


@pytest.mark.skipif(not (REPO / "web" / "data" / "catalog.json").exists(),
                    reason="web/data/catalog.json not built (python3 web/make_index.py)")
def test_sync_beta_publishes_only_page_files_under_beta(tmp_path):
    _, r = _run_beta(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(tmp_path, "aws ")
    assert len(calls) == 1 and calls[0].startswith(f"aws s3 sync {REPO / 'web'} s3://zenigoke-catalog-dsc/beta ")
    for inc in ("index.html", "css/*", "js/*", "data/*"):
        assert f"--include {inc}" in calls[0]
    assert calls[0].index("--exclude *") < calls[0].index("--include")   # exclude-all first
    assert "--delete" in calls[0] and "--cache-control no-cache" in calls[0]
    assert "--dryrun" not in calls[0]
    assert "beta/index.html" in r.stdout


@pytest.mark.skipif(not (REPO / "web" / "data" / "catalog.json").exists(),
                    reason="web/data/catalog.json not built (python3 web/make_index.py)")
def test_sync_beta_dryrun_and_bucket(tmp_path):
    _, r = _run_beta(tmp_path, "--dryrun", "other-bucket")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(tmp_path, "aws ")
    assert len(calls) == 1 and "s3://other-bucket/beta" in calls[0] and "--dryrun" in calls[0]


def test_sync_beta_rejects_unknown_option(tmp_path):
    _, r = _run_beta(tmp_path, "--delete")
    assert r.returncode != 0
    assert _calls(tmp_path, "aws ") == []
