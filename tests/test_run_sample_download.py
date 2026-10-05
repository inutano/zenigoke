"""run-sample.sh download fallback (ENA -> DDBJ -> NCBI) against stub commands.

No network, no docker: curl, aria2c and docker are stubs on PATH, and PIPELINES_REPO points at a
stub fast-download.sh. Each stub appends "<name> <args...>" to $STUB_LOG.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "run-sample.sh"
EXP = "SRX2613733"
RUN = "SRR5314024"
DDBJ_URL = ("https://ddbj.nig.ac.jp/public/ddbj_database/dra/sralite/ByExp/litesra/"
            f"SRX/SRX261/{EXP}/{RUN}/{RUN}.sra")

# STUB_ENA: error (HTTP 200 with ENA's error text, the 2026-10-03 failure), http400 (curl exit
# 22) or ok (valid TSVs). STUB_NCBI_ID empty -> esearch finds nothing. STUB_LAYOUT is the runinfo
# LibraryLayout and what the fasterq-dump stub writes (PAIRED also leaves an unpaired <acc>.fastq;
# PAIRED_UNEVEN writes one read more in _2). STUB_DDBJ_CODE is the DDBJ HEAD status.
STUB = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
rec() { printf '@%s.%s\nACGT\n+\nIIII\n' "$1" "$2"; }
case "$(basename "$0")" in
  curl)
    url="${@: -1}"
    case "$url" in
      *ebi.ac.uk/ena/*)
        case "${STUB_ENA:-error}" in
          error) echo 'ERROR occurred. Not all results may have been written.Query: {"bool":{}}' ;;
          http400) echo "curl: (22) The requested URL returned error: 400" >&2; exit 22 ;;
          ok)
            case "$url" in
              *"fields=run_accession&"*) printf 'run_accession\n%s\n' "$STUB_RUN" ;;
              *) printf 'run_accession\tlibrary_layout\tfastq_ftp\tfastq_md5\n%s\tPAIRED\tftp.example/%s_1.fastq.gz;ftp.example/%s_2.fastq.gz\tm1;m2\n' \
                   "$STUB_RUN" "$STUB_RUN" "$STUB_RUN" ;;
            esac ;;
        esac ;;
      *esearch.fcgi*)
        if [ -n "${STUB_NCBI_ID:-}" ]; then
          printf '{"header":{},"esearchresult":{"count":"1","idlist":["%s"]}}\n' "$STUB_NCBI_ID"
        else
          printf '{"header":{},"esearchresult":{"count":"0","idlist":[]}}\n'
        fi ;;
      *efetch.fcgi*)
        # quoted commas before LibraryLayout: the columns must be found by header name
        printf 'Run,ReleaseDate,LibraryName,Experiment,LibraryLayout,CenterName\n'
        printf '%s,2017-12-19,"Anth, 11",%s,%s,"LAB, INSTITUTE"\n\n' "$STUB_RUN" "$STUB_EXP" "${STUB_LAYOUT%_UNEVEN}" ;;
      *ddbj.nig.ac.jp*) printf '%s' "${STUB_DDBJ_CODE:-200}" ;;
    esac ;;
  aria2c)
    dir=""; out=""
    for a in "$@"; do
      case "$a" in --dir=*) dir="${a#--dir=}" ;; --out=*) out="${a#--out=}" ;; esac
    done
    mkdir -p "$dir"; echo SRA > "$dir/$out" ;;
  docker)
    case " $* " in
      *" vdb-validate "*) exit "${STUB_VALIDATE_RC:-0}" ;;
      *" fasterq-dump "*)
        prev=""; src=""; out=""
        for a in "$@"; do
          [ "$prev" = fasterq-dump ] && src="$a"
          [ "$prev" = --outdir ] && out="$a"
          prev="$a"
        done
        acc=$(basename "$src" .sra)
        case "${STUB_LAYOUT:-PAIRED}" in
          PAIRED) rec "$acc" 1 > "$out/${acc}_1.fastq"; rec "$acc" 1 > "$out/${acc}_2.fastq"
                  rec "$acc" 2 > "$out/${acc}.fastq" ;;
          PAIRED_UNEVEN) rec "$acc" 1 > "$out/${acc}_1.fastq"
                         { rec "$acc" 1; rec "$acc" 2; } > "$out/${acc}_2.fastq" ;;
          *) rec "$acc" 1 > "$out/${acc}.fastq" ;;
        esac ;;
      *)  # pipeline container: record what the FASTQ dir holds when the pipeline starts
        prev=""
        for a in "$@"; do
          [ "$prev" = --fastq-fwd ] && ls "$(dirname "$a")" > "$STUB_DIR/fastq-dir.txt"
          prev="$a"
        done ;;
    esac ;;
esac
exit 0
"""

# fast-download.sh <run> <outdir>: STUB_FD=ok writes <run>_1/_2.fastq.gz; fail leaves a partial
# <run>.fastq.gz and exits 1 (as an interrupted ENA download would).
FAST_DOWNLOAD = r"""#!/usr/bin/env bash
echo "fast-download.sh $*" >> "$STUB_LOG"
case "${STUB_FD:-ok}" in
  ok) printf '@r\nA\n+\nI\n' | gzip > "$2/${1}_1.fastq.gz"; printf '@r\nA\n+\nI\n' | gzip > "$2/${1}_2.fastq.gz" ;;
  fail) echo partial > "$2/${1}.fastq.gz"; exit 1 ;;
esac
"""


def _run(tmp_path, **stub_env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for n in ("curl", "aria2c", "docker"):
        p = bin_dir / n
        p.write_text(STUB)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    fd = tmp_path / "pipelines" / "scripts" / "fast-download.sh"
    fd.parent.mkdir(parents=True)
    fd.write_text(FAST_DOWNLOAD)
    (tmp_path / "calls.log").touch()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "STUB_LOG": str(tmp_path / "calls.log"), "STUB_DIR": str(tmp_path),
           "STUB_RUN": RUN, "STUB_EXP": EXP, "STUB_NCBI_ID": "3785227", "STUB_LAYOUT": "PAIRED",
           "DATA_ROOT": str(tmp_path / "data"), "PIPELINES_REPO": str(tmp_path / "pipelines"),
           "ZENIGOKE_REPO": str(REPO), "THREADS": "4"}
    for k in ("OUTPUT_ROOT", "STATUS_DIR", "LOG_DIR", "SKIP_ENA", "TMPDIR"):
        env.pop(k, None)
    env.update(stub_env)
    return subprocess.run(["bash", str(SCRIPT), EXP, "Bisulfite-Seq"], env=env,
                          capture_output=True, text=True, timeout=60)


def _calls(tmp_path, prefix=""):
    return [ln for ln in (tmp_path / "calls.log").read_text().splitlines() if ln.startswith(prefix)]


def _pipeline_call(tmp_path):
    calls = [c for c in _calls(tmp_path, "docker ") if "chip-atlas-pipeline-v2-bs" in c]
    assert len(calls) == 1
    return calls[0]


def _fastq_dir(tmp_path):
    return sorted((tmp_path / "fastq-dir.txt").read_text().split())


def _ok(tmp_path, r):
    assert r.returncode == 0, r.stdout + r.stderr
    status = tmp_path / "data" / "status"
    assert (status / f"{EXP}.ok").exists() and not (status / f"{EXP}.failed").exists()
    assert not (tmp_path / "data" / "tmp" / EXP).exists()      # the EXIT trap removed TMP_DIR


def test_invalid_ena_response_resolves_paired_run_from_ncbi_and_downloads_from_ddbj(tmp_path):
    r = _run(tmp_path, STUB_ENA="error")
    _ok(tmp_path, r)
    assert any("esearch.fcgi" in c and f"term={EXP}" in c for c in _calls(tmp_path, "curl "))
    assert any("efetch.fcgi" in c and "id=3785227" in c and "rettype=runinfo" in c
               for c in _calls(tmp_path, "curl "))
    assert f"download source: {RUN} ddbj" in r.stdout
    aria = _calls(tmp_path, "aria2c ")
    assert len(aria) == 1 and aria[0].endswith(DDBJ_URL)
    tmp_dir = str(tmp_path / "data" / "tmp" / EXP)
    assert f"--dir={tmp_dir}" in aria[0]                      # the .sra lands in TMP_DIR
    docker = _calls(tmp_path, "docker ")
    validate = [c for c in docker if " vdb-validate " in c]
    dump = [c for c in docker if " fasterq-dump " in c]
    assert len(validate) == 1 and validate[0].endswith(f"{RUN}.sra")
    assert len(dump) == 1 and f"fasterq-dump {tmp_dir}/" in dump[0] and f"{RUN}.sra " in dump[0]
    for c in validate + dump:
        assert "sra-tools" in c and f"--user {os.getuid()}:{os.getgid()}" in c
        assert f"-v {tmp_dir}:{tmp_dir}" in c and f"-v {tmp_path / 'data'}:" not in c
    assert "--split-files" in dump[0] and "--skip-technical" in dump[0]
    assert f"--temp {tmp_dir}/" in dump[0] and "--threads 4" in dump[0]
    # PE: only _1/_2 reach FASTQ_DIR; the unpaired leftover is dropped so SE/PE detection is clean
    assert _fastq_dir(tmp_path) == [f"{RUN}_1.fastq.gz", f"{RUN}_2.fastq.gz"]
    fastq_dir = tmp_path / "data" / "fastq" / EXP
    call = _pipeline_call(tmp_path)
    assert f"--fastq-fwd {fastq_dir}/{RUN}_1.fastq.gz" in call
    assert f"--fastq-rev {fastq_dir}/{RUN}_2.fastq.gz" in call
    # the invalid per-run ENA report never reaches fast-download.sh
    assert _calls(tmp_path, "fast-download.sh") == []


def test_single_runinfo_layout_names_the_file_without_mate_suffix(tmp_path):
    r = _run(tmp_path, STUB_ENA="http400", STUB_LAYOUT="SINGLE")
    _ok(tmp_path, r)
    assert f"download source: {RUN} ddbj" in r.stdout
    assert _fastq_dir(tmp_path) == [f"{RUN}.fastq.gz"]
    call = _pipeline_call(tmp_path)
    assert f"--fastq-fwd {tmp_path / 'data' / 'fastq' / EXP}/{RUN}.fastq.gz" in call
    assert "--fastq-rev" not in call


def test_ddbj_404_downloads_from_ncbi(tmp_path):
    r = _run(tmp_path, SKIP_ENA="1", STUB_DDBJ_CODE="404")
    _ok(tmp_path, r)
    assert f"download source: {RUN} ncbi" in r.stdout
    assert _calls(tmp_path, "aria2c ") == []
    dump = [c for c in _calls(tmp_path, "docker ") if " fasterq-dump " in c]
    assert len(dump) == 1 and f"fasterq-dump {RUN} " in dump[0]   # NCBI fetch by accession
    assert "--split-files" in dump[0] and "--skip-technical" in dump[0]
    assert _fastq_dir(tmp_path) == [f"{RUN}_1.fastq.gz", f"{RUN}_2.fastq.gz"]


def test_ddbj_validation_failure_falls_back_to_ncbi(tmp_path):
    r = _run(tmp_path, SKIP_ENA="1", STUB_VALIDATE_RC="3")
    _ok(tmp_path, r)
    assert len(_calls(tmp_path, "aria2c ")) == 1
    assert f"download source: {RUN} ncbi" in r.stdout
    dump = [c for c in _calls(tmp_path, "docker ") if " fasterq-dump " in c]
    assert len(dump) == 1 and f"fasterq-dump {RUN} " in dump[0]


def test_skip_ena_never_calls_ena(tmp_path):
    r = _run(tmp_path, SKIP_ENA="1", STUB_ENA="ok")
    _ok(tmp_path, r)
    assert not [c for c in _calls(tmp_path) if "ebi.ac.uk" in c]
    assert _calls(tmp_path, "fast-download.sh") == []
    assert f"download source: {RUN} ddbj" in r.stdout


def test_valid_ena_keeps_the_ena_path(tmp_path):
    r = _run(tmp_path, STUB_ENA="ok")
    _ok(tmp_path, r)
    assert f"download source: {RUN} ena" in r.stdout
    assert _calls(tmp_path, "fast-download.sh") == [
        f"fast-download.sh {RUN} {tmp_path / 'data' / 'fastq' / EXP}"]
    assert not [c for c in _calls(tmp_path) if "ncbi.nlm.nih.gov" in c or "ddbj" in c]
    assert _calls(tmp_path, "aria2c ") == []
    assert _fastq_dir(tmp_path) == [f"{RUN}_1.fastq.gz", f"{RUN}_2.fastq.gz"]


def test_failed_ena_download_removes_partials_before_ddbj(tmp_path):
    r = _run(tmp_path, STUB_ENA="ok", STUB_FD="fail")
    _ok(tmp_path, r)
    assert len(_calls(tmp_path, "fast-download.sh")) == 1
    assert f"download source: {RUN} ddbj" in r.stdout
    assert _fastq_dir(tmp_path) == [f"{RUN}_1.fastq.gz", f"{RUN}_2.fastq.gz"]   # no partial .fastq.gz


def test_no_runs_from_ena_or_ncbi_fails_clearly(tmp_path):
    r = _run(tmp_path, STUB_ENA="http400", STUB_NCBI_ID="")
    assert r.returncode != 0
    assert f"ERROR: no runs for {EXP} from ENA or NCBI" in r.stdout
    assert (tmp_path / "data" / "status" / f"{EXP}.failed").exists()
    assert _calls(tmp_path, "aria2c ") == [] and not [c for c in _calls(tmp_path, "docker ")]


def test_uneven_pairs_fail_instead_of_mispairing(tmp_path):
    r = _run(tmp_path, SKIP_ENA="1", STUB_LAYOUT="PAIRED_UNEVEN")
    assert r.returncode != 0
    assert "read counts differ" in r.stdout
    assert f"ERROR: could not download {RUN} from DDBJ or NCBI" in r.stdout
    assert not [c for c in _calls(tmp_path, "docker ") if "chip-atlas-pipeline-v2-bs" in c]
