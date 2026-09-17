import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_SCRIPT = REPO_ROOT / "scripts" / "lib" / "backend-release.sh"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path):
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "ci@example.com")
    _git(path, "config", "user.name", "CI")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README").write_text("init\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-m", "init")
    return path


def _sha(path):
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(path),
        text=True,
    ).strip()


def _lib(command, cwd):
    return subprocess.run(
        ["bash", "-c", f"source '{LIB_SCRIPT}'; {command}"],
        cwd=str(cwd),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )


def test_same_sha_reuses_existing_release_tag(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sha = _sha(repo)
    _git(repo, "tag", "v0.4.0", sha)

    result = _lib(f'resolve_production_release_tag "{sha}"', repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v0.4.0"


def test_untagged_sha_increments_minor_and_resets_patch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _sha(repo)
    _git(repo, "tag", "v0.2.0", first)
    (repo / "README").write_text("next\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "next")
    second = _sha(repo)

    result = _lib(f'resolve_production_release_tag "{second}"', repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v0.3.0"


def test_first_release_is_v0_1_0(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sha = _sha(repo)

    result = _lib(f'resolve_production_release_tag "{sha}"', repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v0.1.0"


def test_existing_release_on_same_sha_is_rerun_safe(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sha = _sha(repo)
    _git(repo, "tag", "v0.4.0", sha)

    result = _lib(f'release_tag_sha_status "v0.4.0" "{sha}"', repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "match"


def test_conflicting_release_tag_fails(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _sha(repo)
    _git(repo, "tag", "v0.4.0", first)
    (repo / "README").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "other")
    second = _sha(repo)

    result = _lib(f'release_tag_sha_status "v0.4.0" "{second}"', repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "conflict"
