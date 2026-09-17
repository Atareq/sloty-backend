import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy-sloty-backend"
WRITE_SCRIPT = REPO_ROOT / "scripts" / "write-backend-release-metadata"
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
        "fetch",
        "verify",
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
    result = subprocess.run(
        ["bash", str(WRITE_SCRIPT), "abc", VALID_RELEASE],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "etc-sloty" / "backend-release.json").exists()


def _write_fake_python(path, version, base_prefix):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                "if len(sys.argv) >= 3 and sys.argv[1] == '-c':",
                f"    print({version!r})",
                f"    print({base_prefix!r})",
                "    raise SystemExit(0)",
                "raise SystemExit('unexpected invocation')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_python_verify(tmp_path, python_path=None, extra_env=None):
    env = os.environ.copy()
    env["SLOTY_DEPLOY_SOURCE_ONLY"] = "1"
    if python_path is not None:
        env["SLOTY_BACKEND_PYTHON"] = str(python_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            "-c",
            f"source '{DEPLOY_SCRIPT}' && deploy_verify_python",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_python_verify_accepts_sloty_runtime(tmp_path):
    python_path = tmp_path / "venv" / "bin" / "python"
    _write_fake_python(python_path, "3.12.14", "/opt/sloty-runtime/python")

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode == 0, result.stderr


def test_python_verify_rejects_missing_interpreter(tmp_path):
    result = _run_python_verify(tmp_path, tmp_path / "missing-python")

    assert result.returncode != 0
    assert "missing" in result.stderr.lower()
    assert "system Python" in result.stderr


def test_python_verify_rejects_wrong_version(tmp_path):
    python_path = tmp_path / "venv" / "bin" / "python"
    _write_fake_python(python_path, "3.12.7", "/opt/sloty-runtime/python")

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode != 0
    assert "3.12.14" in result.stderr
    assert "3.12.7" in result.stderr


def test_python_verify_rejects_wrong_runtime_prefix(tmp_path):
    python_path = tmp_path / "venv" / "bin" / "python"
    _write_fake_python(python_path, "3.12.14", "/usr")

    result = _run_python_verify(tmp_path, python_path)

    assert result.returncode != 0
    assert "wrong Python runtime" in result.stderr


def test_deploy_script_uses_isolated_runtime_and_health_probe():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "/srv/sloty/backend" in script
    assert "${SLOTY_BACKEND_VENV}/bin/python" in script
    assert "/opt/sloty-runtime/python" in script
    assert "3.12.14" in script
    assert "sloty_python -m pip install" in script
    assert "systemctl restart sloty-backend.service" in script
    assert "systemctl is-active --quiet sloty-backend.service" in script
    assert "http://127.0.0.1:8020/health/" in script
    assert "Host: ${SLOTY_HEALTH_HOST}" in script
    assert "api.sloty.baronlearning.com" in script
    assert "backend.env" in script
    assert "${SLOTY_ETC_DIR:-/etc/sloty}" in script
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
