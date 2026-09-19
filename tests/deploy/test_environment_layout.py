import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_values(path):
    return {
        line.partition("=")[0]: line.partition("=")[2]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def test_production_env_template_matches_backend_configuration():
    template = REPO_ROOT / "env" / "prod.env.example"
    values = _env_values(template)
    names = set(values)

    assert {
        "SECRET_KEY",
        "DEBUG",
        "ALLOWED_HOSTS",
        "DB_ENGINE",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_SSLMODE",
        "JWT_ACCESS_TOKEN_MINUTES",
        "JWT_REFRESH_TOKEN_DAYS",
        "SQL_QUERY_STATS_ENABLED",
        "SQL_QUERY_STATS_VERBOSE",
        "SQL_QUERY_STATS_WARN_QUERY_COUNT",
        "SQL_QUERY_STATS_SLOW_QUERY_MS",
        "SQL_QUERY_STATS_SLOW_REQUEST_MS",
        "SQL_QUERY_STATS_MAX_QUERY_SAMPLES",
        "SQL_QUERY_STATS_MAX_SQL_LENGTH",
    } <= names
    assert not any(name.startswith("R2_") for name in names)
    assert not any(name.startswith("EMAIL_") for name in names)
    assert all(
        values[name] == ""
        for name in ("SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")
    )


def test_backup_env_template_contains_only_r2_configuration():
    template = REPO_ROOT / "env" / "backup.env.example"
    values = _env_values(template)
    names = set(values)

    assert names == {
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_ENDPOINT",
    }
    assert all(
        values[name] == ""
        for name in (
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_ENDPOINT",
        )
    )
    assert "API_Token" not in template.read_text(encoding="utf-8")


def test_service_and_deploy_script_load_only_new_production_env():
    service = (REPO_ROOT / "deploy" / "systemd" / "sloty-backend.service").read_text(
        encoding="utf-8"
    )
    deploy = (REPO_ROOT / "scripts" / "deploy-sloty-backend").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=/etc/sloty/env/prod.env" in service
    assert "backup.env" not in service
    assert "${SLOTY_ETC_DIR}/env/prod.env" in deploy
    assert "/etc/sloty/backend.env" not in service
    assert "/etc/sloty/backend.env" not in deploy


def test_real_production_env_files_are_absent_untracked_and_ignored():
    for relative_path in ("env/prod.env", "env/backup.env"):
        assert not (REPO_ROOT / relative_path).exists()
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative_path],
            cwd=REPO_ROOT,
        )
        assert tracked.returncode != 0
        assert ignored.returncode == 0
