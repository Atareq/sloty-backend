import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy-sloty-backend"
WRITE_SCRIPT = REPO_ROOT / "scripts" / "write-backend-release-metadata"
LIB_SCRIPT = REPO_ROOT / "scripts" / "lib" / "backend-release.sh"
VALID_COMMIT = "91bd3aa28e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b"
VALID_RELEASE = "v0.7.0"
EXISTING_METADATA = {
    "service": "sloty-backend",
    "release": "v0.6.0",
    "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


def _run_deploy(tmp_path, *args, extra_env=None, check=False):
    env = os.environ.copy()
    etc_dir = tmp_path / "etc-sloty"
    stub_dir = tmp_path / "stubs"
    etc_dir.mkdir(exist_ok=True)
    stub_dir.mkdir(exist_ok=True)
    env.update(
        {
            "SLOTY_ETC_DIR": str(etc_dir),
            "SLOTY_RELEASE_FILE": str(etc_dir / "backend-release.json"),
            "SLOTY_DEPLOY_STUB_DIR": str(stub_dir),
            "SLOTY_BACKEND_PYTHON": sys.executable,
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def _metadata_path(tmp_path):
    return tmp_path / "etc-sloty" / "backend-release.json"


def _seed_existing_metadata(tmp_path):
    path = _metadata_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EXISTING_METADATA), encoding="utf-8")
    return path


def _source_deploy(command, extra_env=None):
    env = os.environ.copy()
    env["SLOTY_DEPLOY_SOURCE_ONLY"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", f"source '{DEPLOY_SCRIPT}'; {command}"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(path):
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "ci@example.com")
    _git(path, "config", "user.name", "CI")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README").write_text("init\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-m", "init")
    _git(path, "branch", "-M", "master")
    return path


def test_valid_sha_and_release_write_metadata_after_health(tmp_path):
    result = _run_deploy(tmp_path, VALID_COMMIT, VALID_RELEASE)

    assert result.returncode == 0, result.stderr
    payload = json.loads(_metadata_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["service"] == "sloty-backend"
    assert payload["release"] == VALID_RELEASE
    assert payload["commit"] == VALID_COMMIT
    assert payload["deployed_at"].endswith("Z")


def test_invalid_sha_is_rejected(tmp_path):
    _seed_existing_metadata(tmp_path)

    result = _run_deploy(tmp_path, "abc", VALID_RELEASE)

    assert result.returncode != 0
    assert json.loads(_metadata_path(tmp_path).read_text(encoding="utf-8")) == (
        EXISTING_METADATA
    )


def test_invalid_release_is_rejected(tmp_path):
    _seed_existing_metadata(tmp_path)

    result = _run_deploy(tmp_path, VALID_COMMIT, "production")

    assert result.returncode != 0
    assert json.loads(_metadata_path(tmp_path).read_text(encoding="utf-8")) == (
        EXISTING_METADATA
    )


@pytest.mark.parametrize(
    "fail_stage",
    [
        "worktree",
        "origin",
        "fetch",
        "verify",
        "ancestor",
        "checkout",
        "python",
        "install",
        "env",
        "check",
        "migrate",
        "collectstatic",
        "restart",
        "systemd",
        "health",
    ],
)
def test_metadata_is_not_written_before_successful_deployment(tmp_path, fail_stage):
    _seed_existing_metadata(tmp_path)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_FAIL_STAGE": fail_stage},
    )

    assert result.returncode != 0
    assert json.loads(_metadata_path(tmp_path).read_text(encoding="utf-8")) == (
        EXISTING_METADATA
    )


def test_metadata_is_written_only_after_health_stage(tmp_path):
    health_marker = tmp_path / "health-ran"
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    health_stub = stub_dir / "health"
    health_stub.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\ntouch '{health_marker}'\n",
        encoding="utf-8",
    )
    health_stub.chmod(health_stub.stat().st_mode | stat.S_IEXEC)

    result = _run_deploy(tmp_path, VALID_COMMIT, VALID_RELEASE)

    assert result.returncode == 0, result.stderr
    assert health_marker.exists()
    payload = json.loads(_metadata_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["release"] == VALID_RELEASE
    assert payload["commit"] == VALID_COMMIT


def test_failed_deployment_leaves_existing_metadata_unchanged(tmp_path):
    path = _seed_existing_metadata(tmp_path)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_FAIL_STAGE": "health"},
    )

    assert result.returncode != 0
    assert json.loads(path.read_text(encoding="utf-8")) == EXISTING_METADATA


def test_health_failure_prevents_metadata_replacement(tmp_path):
    path = _seed_existing_metadata(tmp_path)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_FAIL_STAGE": "health"},
    )

    assert result.returncode != 0
    assert json.loads(path.read_text(encoding="utf-8")) == EXISTING_METADATA


def test_metadata_write_is_atomic(tmp_path):
    path = _seed_existing_metadata(tmp_path)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_FAIL_BEFORE_METADATA_RENAME": "1"},
    )

    assert result.returncode != 0
    assert json.loads(path.read_text(encoding="utf-8")) == EXISTING_METADATA
    leftovers = list(path.parent.glob("backend-release.json.tmp.*"))
    assert leftovers == []


def test_write_helper_validates_arguments(tmp_path):
    env = os.environ.copy()
    env["SLOTY_ETC_DIR"] = str(tmp_path / "etc-sloty")
    env["SLOTY_BACKEND_PYTHON"] = sys.executable
    result = subprocess.run(
        ["bash", str(WRITE_SCRIPT), "abc", VALID_RELEASE],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "etc-sloty" / "backend-release.json").exists()


PRODUCTION_BASE_PREFIX = "/opt/sloty-runtime/python/cpython-3.12.14-linux-x86_64-gnu"


def _write_fake_python(path, version, prefix, base_prefix):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                "if len(sys.argv) >= 3 and sys.argv[1] == '-c':",
                "    code = sys.argv[2]",
                "    if 'python_version' in code:",
                f"        print({version!r})",
                "    elif 'base_prefix' in code:",
                f"        print({base_prefix!r})",
                "    elif 'prefix' in code:",
                f"        print({prefix!r})",
                "    else:",
                "        raise SystemExit('unexpected -c snippet')",
                "    raise SystemExit(0)",
                "raise SystemExit('unexpected invocation')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_python_verify(tmp_path, python_path=None, extra_env=None):
    venv = tmp_path / "venv"
    env = {"SLOTY_BACKEND_VENV": str(venv)}
    if python_path is not None:
        env["SLOTY_BACKEND_PYTHON"] = str(python_path)
    if extra_env:
        env.update(extra_env)
    return _source_deploy("deploy_verify_python", extra_env=env)


def test_python_verify_accepts_sloty_runtime(tmp_path):
    venv = tmp_path / "venv"
    python_path = venv / "bin" / "python"
    _write_fake_python(
        python_path,
        "3.12.14",
        str(venv),
        PRODUCTION_BASE_PREFIX,
    )

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode == 0, result.stderr


def test_python_verify_rejects_missing_interpreter(tmp_path):
    result = _run_python_verify(tmp_path, tmp_path / "missing-python")

    assert result.returncode != 0
    assert "missing" in result.stderr.lower()
    assert "system Python" in result.stderr


def test_python_verify_rejects_wrong_version(tmp_path):
    venv = tmp_path / "venv"
    python_path = venv / "bin" / "python"
    _write_fake_python(python_path, "3.13.5", str(venv), PRODUCTION_BASE_PREFIX)

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode != 0
    assert "Wrong Sloty Python version: 3.13.5" in result.stderr
    assert "3.12.14" in result.stderr


@pytest.mark.parametrize("base_prefix", ["/usr", "/usr/local", "/usr/lib/python3.13"])
def test_python_verify_rejects_system_runtime(tmp_path, base_prefix):
    venv = tmp_path / "venv"
    python_path = venv / "bin" / "python"
    _write_fake_python(python_path, "3.12.14", str(venv), base_prefix)

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode != 0
    assert "dedicated Sloty Python runtime" in result.stderr
    assert base_prefix in result.stderr


def test_python_verify_rejects_wrong_virtualenv_prefix(tmp_path):
    venv = tmp_path / "venv"
    python_path = venv / "bin" / "python"
    _write_fake_python(
        python_path,
        "3.12.14",
        str(tmp_path / "other-venv"),
        PRODUCTION_BASE_PREFIX,
    )

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode != 0
    assert "Unexpected Sloty virtualenv prefix" in result.stderr


def test_deploy_script_uses_isolated_runtime_and_health_probe():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "git -C" in script and "rev-parse --show-toplevel" in script
    assert "/srv/sloty/backend" not in script
    assert "/usr/local/sbin" not in script
    assert "git pull" not in script
    assert "${SLOTY_BACKEND_DIR}/.venv" in script
    assert "/opt/sloty-runtime/python" in script
    assert "SLOTY_PYTHON_RUNTIME_ROOT" in script
    assert "3.12.14" in script
    assert "sloty_python -m pip install" in script
    assert "systemctl restart sloty-backend.service" in script
    assert "systemctl is-active --quiet sloty-backend.service" in script
    assert "http://127.0.0.1:8020/health/" in script
    assert "Host: ${SLOTY_HEALTH_HOST}" in script
    assert "api.sloty.baronlearning.com" in script
    assert "backend.env" in script
    assert "merge-base --is-ancestor" in script
    assert "--write-out" in script and "%{http_code}" in script
    assert "--location" not in script
    lowered = script.lower()
    assert "systemctl restart postgresql" not in lowered
    assert "systemctl stop postgresql" not in lowered
    assert "systemctl start postgresql" not in lowered
    assert "systemctl reload postgresql" not in lowered
    assert "pg_ctl" not in lowered
    assert "apt " not in lowered


def test_failed_python_verify_leaves_existing_metadata_unchanged(tmp_path):
    path = _seed_existing_metadata(tmp_path)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_FAIL_STAGE": "python"},
    )

    assert result.returncode != 0
    assert json.loads(path.read_text(encoding="utf-8")) == EXISTING_METADATA


def test_repo_root_discovery_uses_git_toplevel():
    result = _source_deploy("discover_sloty_backend_dir")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(REPO_ROOT)


def test_snapshot_execution_runs_once_and_cleans_up(tmp_path):
    trace = tmp_path / "snapshot-trace"
    marker = tmp_path / "from-snapshot"
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    health_stub = stub_dir / "health"
    health_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                (
                    "printf '%s\\n' "
                    '"${SLOTY_DEPLOY_RUNNING_FROM_SNAPSHOT}" '
                    f"> '{marker}'"
                ),
                ("printf '%s\\n' " '"${SLOTY_DEPLOY_SNAPSHOT_DIR}" ' f">> '{marker}'"),
                'test -f "${SLOTY_DEPLOY_SNAPSHOT_DIR}/deploy-sloty-backend"',
                'test -f "${SLOTY_DEPLOY_SNAPSHOT_DIR}/lib/backend-release.sh"',
                'test -f "${SLOTY_DEPLOY_SNAPSHOT_DIR}/write-backend-release-metadata"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    health_stub.chmod(health_stub.stat().st_mode | stat.S_IEXEC)

    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_SNAPSHOT_TRACE": str(trace)},
    )

    assert result.returncode == 0, result.stderr
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    snapshot_dir = Path(lines[0])
    assert marker.read_text(encoding="utf-8").splitlines()[0] == "1"
    assert not snapshot_dir.exists()


def test_snapshot_does_not_recursively_snapshot_itself(tmp_path):
    trace = tmp_path / "snapshot-trace"
    result = _run_deploy(
        tmp_path,
        VALID_COMMIT,
        VALID_RELEASE,
        extra_env={"SLOTY_DEPLOY_SNAPSHOT_TRACE": str(trace)},
    )
    assert result.returncode == 0, result.stderr
    assert len(trace.read_text(encoding="utf-8").splitlines()) == 1


def test_dirty_worktree_is_rejected(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    (repo / "README").write_text("dirty\n", encoding="utf-8")
    result = _source_deploy(
        "deploy_worktree",
        extra_env={"SLOTY_BACKEND_DIR": str(repo)},
    )
    assert result.returncode != 0
    assert "unexpected changes" in result.stderr


def test_ignored_runtime_files_do_not_block_worktree_check(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("runtime.log\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore runtime")
    (repo / "runtime.log").write_text("ignored\n", encoding="utf-8")
    result = _source_deploy(
        "deploy_worktree",
        extra_env={"SLOTY_BACKEND_DIR": str(repo)},
    )
    assert result.returncode == 0, result.stderr


def test_master_ancestry_is_checked(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    bare = tmp_path / "origin.git"
    _git(repo, "clone", "--bare", str(repo), str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "fetch", "origin")
    ancestor = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        text=True,
    ).strip()
    (repo / "README").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "ahead of origin")
    descendant = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        text=True,
    ).strip()

    ok = _source_deploy(
        f'COMMIT_SHA="{ancestor}"; deploy_ancestor',
        extra_env={"SLOTY_BACKEND_DIR": str(repo)},
    )
    blocked = _source_deploy(
        f'COMMIT_SHA="{descendant}"; deploy_ancestor',
        extra_env={"SLOTY_BACKEND_DIR": str(repo)},
    )
    assert ok.returncode == 0, ok.stderr
    assert blocked.returncode != 0
    assert "not an ancestor" in blocked.stderr


@pytest.mark.parametrize("http_code", ["301", "302", "401", "403", "404", "503"])
def test_health_requires_exact_200(http_code):
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source '{DEPLOY_SCRIPT}'; "
                "curl() { printf '%s' \"${FAKE_CURL_HTTP_CODE}\"; }; "
                "deploy_health"
            ),
        ],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "SLOTY_DEPLOY_SOURCE_ONLY": "1",
            "FAKE_CURL_HTTP_CODE": http_code,
            "SLOTY_HEALTH_ATTEMPTS": "1",
            "SLOTY_HEALTH_RETRY_SLEEP": "0",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "HTTP 200" in result.stderr
    assert http_code in result.stderr


def test_health_accepts_exact_200():
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source '{DEPLOY_SCRIPT}'; "
                "curl() { printf '%s' 200; }; "
                "deploy_health"
            ),
        ],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "SLOTY_DEPLOY_SOURCE_ONLY": "1",
            "SLOTY_HEALTH_ATTEMPTS": "1",
            "SLOTY_HEALTH_RETRY_SLEEP": "0",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_bash_syntax_is_valid():
    for path in (DEPLOY_SCRIPT, WRITE_SCRIPT, LIB_SCRIPT):
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
