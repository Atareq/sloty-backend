import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-sloty-db"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore-sloty-db"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-host-dependencies"
BACKUP_NAME = "sloty_prod-2026-09-19T090000+0300.dump"
ARCHIVE_CONTENT = b"stub custom archive\n"


def _write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def harness(tmp_path):
    bin_dir = tmp_path / "bin"
    backup_dir = tmp_path / "backups"
    home_dir = tmp_path / "home"
    bin_dir.mkdir()
    home_dir.mkdir()
    events_path = tmp_path / "events.log"
    command_log = tmp_path / "commands.log"

    _write_executable(
        bin_dir / "install",
        """#!/usr/bin/env bash
[[ "$#" == 9 && "$1" == '-d' && "$2" == '-o' && "$3" == 'root' &&
  "$4" == '-g' && "$5" == 'root' && "$6" == '-m' && "$7" == '0700' &&
  "$8" == '--' && "$9" == "$SLOTY_BACKUP_DIR" ]] || exit 34
mkdir -p -- "$9"
chmod 0700 -- "$9"
""",
    )
    _write_executable(
        bin_dir / "date",
        "#!/usr/bin/env bash\n"
        "[[ \"${TZ:-}\" == 'Africa/Cairo' ]] || exit 30\n"
        "printf '2026-09-19T090000+0300\\n'\n",
    )
    _write_executable(
        bin_dir / "pg_dump",
        """#!/usr/bin/env bash
printf 'pg_dump\\n' >> "$EVENT_LOG"
printf 'pg_dump <%s>\\n' "$*" >> "$COMMAND_LOG"
output=''
while (($#)); do
  case "$1" in
    --file=*) output="${1#*=}" ;;
    --file) output="$2"; shift ;;
  esac
  shift
done
printf 'stub custom archive\\n' > "$output"
""",
    )
    _write_executable(
        bin_dir / "pg_restore",
        """#!/usr/bin/env bash
if [[ "${1:-}" == '--list' ]]; then
  printf 'pg_restore-list\\n' >> "$EVENT_LOG"
  [[ "${STUB_INVALID_ARCHIVE:-0}" != '1' ]] || exit 7
else
  printf 'pg_restore-run\\n' >> "$EVENT_LOG"
  printf 'pg_restore <%s>\\n' "$*" >> "$COMMAND_LOG"
  [[ "${STUB_RESTORE_FAIL:-0}" != '1' ]] || exit 8
fi
""",
    )
    _write_executable(
        bin_dir / "rclone",
        """#!/usr/bin/env bash
printf 'rclone <%s>\\n' "$*" >> "$COMMAND_LOG"
[[ "${RCLONE_CONFIG:-}" == '/dev/null' ]] || exit 20
[[ "${RCLONE_CONFIG_SLOTY_TYPE:-}" == 's3' ]] || exit 21
[[ "${RCLONE_CONFIG_SLOTY_PROVIDER:-}" == 'Cloudflare' ]] || exit 22
[[ "${RCLONE_CONFIG_SLOTY_REGION:-}" == 'auto' ]] || exit 23
[[ "${RCLONE_CONFIG_SLOTY_ACCESS_KEY_ID:-}" == 'access-key-canary' ]] || exit 31
[[ "${RCLONE_CONFIG_SLOTY_ENDPOINT:-}" == \
  'https://account123.r2.cloudflarestorage.com' ]] || exit 32
[[ "${RCLONE_CONFIG_SLOTY_NO_CHECK_BUCKET:-}" == 'true' ]] || exit 24
[[ "${RCLONE_CONFIG_SLOTY_NO_HEAD:-}" == 'true' ]] || exit 25
[[ -z "${RCLONE_CONFIG_SLOTY_ACL:-}" ]] || exit 33
[[ "${RCLONE_CONFIG_SLOTY_SECRET_ACCESS_KEY:-}" == \
  "$STUB_EXPECTED_R2_SECRET" ]] || exit 26

while [[ "${1:-}" == --* ]]; do shift; done
operation="${1:-}"
shift || true
case "$operation" in
  copyto)
    source="$1"
    destination="$2"
    if [[ "$source" == sloty:* ]]; then
      printf 'rclone-download\\n' >> "$EVENT_LOG"
      printf 'stub custom archive\\n' > "$destination"
    else
      printf 'rclone-upload\\n' >> "$EVENT_LOG"
      [[ "${STUB_RCLONE_UPLOAD_FAIL:-0}" != '1' ]] || exit 27
    fi
    ;;
  lsf)
    printf 'rclone-list\\n' >> "$EVENT_LOG"
    [[ "${STUB_RCLONE_LIST_FAIL:-0}" != '1' ]] || exit 28
    printf '%s\\t%s\\n' "$STUB_REMOTE_SIZE" "$STUB_REMOTE_NAME"
    ;;
  *) exit 29 ;;
esac
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "HOME": str(home_dir),
            "EVENT_LOG": str(events_path),
            "COMMAND_LOG": str(command_log),
            "SLOTY_BACKUP_DIR": str(backup_dir),
            "DB_ENGINE": "postgresql",
            "DB_NAME": "sloty_prod",
            "DB_USER": "sloty_db_user",
            "DB_PASSWORD": "db-password-canary",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "DB_SSLMODE": "",
            "R2_ACCESS_KEY_ID": "access-key-canary",
            "R2_SECRET_ACCESS_KEY": "r2-secret-canary",
            "R2_BUCKET_BACKUP": "sloty-prod-backups",
            "R2_ENDPOINT": "https://account123.r2.cloudflarestorage.com",
            "STUB_EXPECTED_R2_SECRET": "r2-secret-canary",
            "STUB_REMOTE_SIZE": str(len(ARCHIVE_CONTENT)),
            "STUB_REMOTE_NAME": BACKUP_NAME,
        }
    )
    return {
        "backup_dir": backup_dir,
        "bin_dir": bin_dir,
        "command_log": command_log,
        "env": env,
        "events": events_path,
        "home_dir": home_dir,
        "tmp_path": tmp_path,
    }


def _run(script, harness, *args, env_overrides=None, path=None):
    env = harness["env"].copy()
    if env_overrides:
        env.update(env_overrides)
    if path is not None:
        env["PATH"] = str(path)
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _read_lines(path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_backup_validates_env_and_commands_without_printing_credentials(harness):
    missing_env = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={"R2_SECRET_ACCESS_KEY": ""},
    )
    assert missing_env.returncode != 0
    assert "R2_SECRET_ACCESS_KEY" in missing_env.stderr
    assert "r2-secret-canary" not in missing_env.stdout + missing_env.stderr

    missing_bucket = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={"R2_BUCKET_BACKUP": ""},
    )
    assert missing_bucket.returncode != 0
    assert "R2_BUCKET_BACKUP" in missing_bucket.stderr

    missing_endpoint = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={"R2_ENDPOINT": ""},
    )
    assert missing_endpoint.returncode != 0
    assert "R2_ENDPOINT" in missing_endpoint.stderr

    empty_path = harness["tmp_path"] / "empty-path"
    empty_path.mkdir()
    missing_command = _run(BACKUP_SCRIPT, harness, path=empty_path)
    assert missing_command.returncode != 0
    assert "Required command not found: pg_dump" in missing_command.stderr


def test_backup_uses_zstd_custom_archive_verifies_remote_then_deletes_local(harness):
    result = _run(BACKUP_SCRIPT, harness)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Creating backup...",
        "Validating backup...",
        "Uploading backup...",
        "Verifying remote backup...",
        "Backup completed successfully.",
    ]
    events = _read_lines(harness["events"])
    assert events == ["pg_dump", "pg_restore-list", "rclone-upload", "rclone-list"]
    commands = "\n".join(_read_lines(harness["command_log"]))
    assert "--format=custom" in commands
    assert "--compress=zstd:9" in commands
    assert "--dbname=sloty_prod" in commands
    assert "--config=/dev/null" in commands
    assert "--retries=1" in commands
    assert "sloty:sloty-prod-backups/db/" + BACKUP_NAME in commands
    assert not list(harness["backup_dir"].glob("*.dump"))
    assert not (harness["home_dir"] / ".config" / "rclone" / "rclone.conf").exists()
    assert "db-password-canary" not in result.stdout + result.stderr
    assert "r2-secret-canary" not in result.stdout + result.stderr
    assert "access-key-canary" not in result.stdout + result.stderr


def test_invalid_archive_is_never_uploaded(harness):
    result = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={"STUB_INVALID_ARCHIVE": "1"},
    )

    assert result.returncode != 0
    assert _read_lines(harness["events"]) == ["pg_dump", "pg_restore-list"]
    assert not list(harness["backup_dir"].glob("*.dump"))


def test_failed_upload_preserves_the_valid_local_dump(harness):
    result = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={"STUB_RCLONE_UPLOAD_FAIL": "1"},
    )

    assert result.returncode != 0
    assert _read_lines(harness["events"]) == [
        "pg_dump",
        "pg_restore-list",
        "rclone-upload",
    ]
    backup_path = harness["backup_dir"] / BACKUP_NAME
    assert backup_path.read_bytes() == ARCHIVE_CONTENT
    assert harness["backup_dir"].stat().st_mode & 0o777 == 0o700
    assert backup_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "remote_name,remote_size",
    [("another-backup.dump", str(len(ARCHIVE_CONTENT))), (BACKUP_NAME, "1")],
)
def test_failed_remote_verification_preserves_the_local_dump(
    harness, remote_name, remote_size
):
    result = _run(
        BACKUP_SCRIPT,
        harness,
        env_overrides={
            "STUB_REMOTE_NAME": remote_name,
            "STUB_REMOTE_SIZE": remote_size,
        },
    )

    assert result.returncode != 0
    assert _read_lines(harness["events"])[-1] == "rclone-list"
    assert (harness["backup_dir"] / BACKUP_NAME).read_bytes() == ARCHIVE_CONTENT


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"STUB_RCLONE_UPLOAD_FAIL": "1"},
        {"STUB_REMOTE_SIZE": "1"},
    ],
    ids=["upload-failure", "verification-failure"],
)
def test_failed_backup_does_not_prune_older_dumps(harness, env_overrides):
    harness["backup_dir"].mkdir(mode=0o700)
    stale = harness["backup_dir"] / "sloty_prod-old.dump"
    stale.write_bytes(ARCHIVE_CONTENT)
    old_time = time.time() - 8 * 24 * 60 * 60
    os.utime(stale, (old_time, old_time))

    result = _run(BACKUP_SCRIPT, harness, env_overrides=env_overrides)

    assert result.returncode != 0
    assert stale.exists()
    assert (harness["backup_dir"] / BACKUP_NAME).exists()


def test_backup_prunes_old_dumps_only_after_verified_upload(harness):
    harness["backup_dir"].mkdir(mode=0o700)
    stale = harness["backup_dir"] / "sloty_prod-old.dump"
    recent = harness["backup_dir"] / "sloty_prod-recent.dump"
    stale.write_bytes(ARCHIVE_CONTENT)
    recent.write_bytes(ARCHIVE_CONTENT)
    old_time = time.time() - 8 * 24 * 60 * 60
    os.utime(stale, (old_time, old_time))

    result = _run(BACKUP_SCRIPT, harness)

    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert recent.exists()
    assert not (harness["backup_dir"] / BACKUP_NAME).exists()


def test_local_restore_requires_target_validates_then_restores_safely(harness):
    local_dump = harness["tmp_path"] / "local.dump"
    local_dump.write_bytes(ARCHIVE_CONTENT)

    missing_target = _run(
        RESTORE_SCRIPT,
        harness,
        "--file",
        str(local_dump),
    )
    assert missing_target.returncode != 0
    assert "explicit --database target" in missing_target.stderr

    result = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_restore_test",
        "--file",
        str(local_dump),
    )

    assert result.returncode == 0, result.stderr
    assert _read_lines(harness["events"]) == ["pg_restore-list", "pg_restore-run"]
    restore_command = _read_lines(harness["command_log"])[-1]
    assert "--exit-on-error" in restore_command
    assert "--single-transaction" in restore_command
    assert "--dbname=sloty_restore_test" in restore_command
    assert "--clean" not in restore_command
    assert "--create" not in restore_command
    assert local_dump.exists()


def test_production_restore_requires_exact_confirmation(harness):
    local_dump = harness["tmp_path"] / "production.dump"
    local_dump.write_bytes(ARCHIVE_CONTENT)

    denied = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_prod",
        "--file",
        str(local_dump),
    )
    assert denied.returncode != 0
    assert "--confirm-production sloty_prod" in denied.stderr
    assert not _read_lines(harness["events"])

    confirmed = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_prod",
        "--confirm-production",
        "sloty_prod",
        "--file",
        str(local_dump),
    )
    assert confirmed.returncode == 0, confirmed.stderr
    assert _read_lines(harness["events"]) == ["pg_restore-list", "pg_restore-run"]


def test_remote_restore_downloads_validates_before_restore_and_cleans_on_success(
    harness,
):
    harness["backup_dir"].mkdir(mode=0o700)
    result = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_restore_test",
        "--remote",
        BACKUP_NAME,
    )

    assert result.returncode == 0, result.stderr
    assert _read_lines(harness["events"]) == [
        "rclone-download",
        "pg_restore-list",
        "pg_restore-run",
    ]
    commands = "\n".join(_read_lines(harness["command_log"]))
    assert "sloty:sloty-prod-backups/db/" + BACKUP_NAME in commands
    assert not (harness["backup_dir"] / BACKUP_NAME).exists()


def test_invalid_remote_restore_is_not_restored_and_partial_is_removed(harness):
    harness["backup_dir"].mkdir(mode=0o700)
    result = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_restore_test",
        "--remote",
        BACKUP_NAME,
        env_overrides={"STUB_INVALID_ARCHIVE": "1"},
    )

    assert result.returncode != 0
    assert _read_lines(harness["events"]) == ["rclone-download", "pg_restore-list"]
    assert not list(harness["backup_dir"].iterdir())


def test_remote_restore_rejects_unselected_or_unsafe_object_path(harness):
    result = _run(
        RESTORE_SCRIPT,
        harness,
        "--database",
        "sloty_restore_test",
        "--remote",
        "../sloty_prod.dump",
    )

    assert result.returncode != 0
    assert "timestamped Sloty dump filename" in result.stderr
    assert not _read_lines(harness["events"])


def test_backup_systemd_timer_installer_and_templates_are_repository_owned():
    service = (REPO_ROOT / "scripts/systemd/sloty-db-backup.service").read_text(
        encoding="utf-8"
    )
    timer = (REPO_ROOT / "scripts/systemd/sloty-db-backup.timer").read_text(
        encoding="utf-8"
    )
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    backup_template = (REPO_ROOT / "env/backup.env.example").read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "User=root" in service
    assert "Group=root" in service
    assert "EnvironmentFile=/etc/sloty/env/prod.env" in service
    assert "EnvironmentFile=/etc/sloty/env/backup.env" in service
    assert "ExecStart=/srv/sloty/backend/scripts/backup-sloty-db" in service
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/var/backups/sloty" in service
    assert "install -d -o root -g root -m 0700" in (
        BACKUP_SCRIPT.read_text(encoding="utf-8")
    )
    assert "OnCalendar=*-*-* 09:00:00 Africa/Cairo" in timer
    assert "Persistent=true" in timer
    assert "R2_ENDPOINT=" in backup_template
    assert "R2_ACCESS_KEY_ID=" in backup_template
    assert "R2_SECRET_ACCESS_KEY=" in backup_template
    assert "R2_BUCKET_BACKUP=sloty-prod-backups" in backup_template
    assert "API_Token" not in backup_template
    assert "rclone" in installer
    assert "PACKAGES+=(rclone)" in installer
    assert "postgresql-client-17" not in installer
    assert "for command_name in pg_dump pg_restore psql rclone" in installer
    assert "NEEDRESTART_MODE=l" in installer
    assert "DEBIAN_FRONTEND=noninteractive" in installer
    assert "systemctl" not in installer
    assert "invoke-rc.d" not in installer
    assert "service " not in installer
