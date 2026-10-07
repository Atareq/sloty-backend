import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
R2_CONFIGURATION_FILES = (
    ".env.example",
    "AGENTS.md",
    "README.md",
    "apps/images/AGENTS.md",
    "apps/images/storage.py",
    "config/settings/base.py",
    "docs/image-pipeline.md",
    "env/backup.env.example",
    "env/prod.env.example",
    "scripts/backup-sloty-db",
    "scripts/lib/sloty-db-backup.sh",
    "scripts/restore-sloty-db",
)


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
    assert {
        "R2_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_IMAGE",
        "IMAGES_PUBLIC_BASE_URL",
        "IMAGE_MAX_UPLOAD_BYTES",
        "IMAGE_MAX_WIDTH",
        "IMAGE_MAX_HEIGHT",
        "IMAGE_MAX_PIXELS",
    } <= names
    assert "R2_BUCKET_BACKUP" not in names
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
        "R2_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_BACKUP",
    }
    assert all(
        values[name] == ""
        for name in (
            "R2_ENDPOINT",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        )
    )
    assert "API_Token" not in template.read_text(encoding="utf-8")


def test_r2_bucket_configuration_is_purpose_specific_and_isolated():
    sources = {
        relative_path: (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in R2_CONFIGURATION_FILES
    }
    forbidden_names = (
        "R2_" + "BUCKET",
        "SLOTY" + "_R2_",
        "SLOTY" + "_IMAGES_",
        "SLOTY" + "_IMAGE_",
        "R2_" + "ACCOUNT_ID",
    )

    for source in sources.values():
        for name in forbidden_names:
            assert not re.search(r"(?<![A-Z_])" + name + r"(?![A-Z_])", source)

    image_storage = sources["apps/images/storage.py"]
    backup_sources = "\n".join(
        sources[path]
        for path in (
            "scripts/backup-sloty-db",
            "scripts/lib/sloty-db-backup.sh",
            "scripts/restore-sloty-db",
        )
    )
    assert "R2_BUCKET_IMAGE" in image_storage
    assert "R2_BUCKET_BACKUP" not in image_storage
    assert "R2_BUCKET_BACKUP" in backup_sources
    assert "R2_BUCKET_IMAGE" not in backup_sources
    for name in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        assert name in image_storage
        assert name in backup_sources


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


def test_production_settings_require_postgresql_and_do_not_force_debug_off():
    production = (REPO_ROOT / "config/settings/production.py").read_text(
        encoding="utf-8"
    )

    assert "ImproperlyConfigured" in production
    assert "Production settings require DB_ENGINE=postgresql." in production
    assert "DEBUG = False" not in production
    assert "DEBUG=False" not in production.replace(" ", "")


def test_real_production_env_files_are_untracked_and_ignored():
    for relative_path in ("env/prod.env", "env/backup.env"):
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
