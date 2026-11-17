# Operations

Production runs on a single VPS: gunicorn under systemd, PostgreSQL 17,
and Nginx managed outside this repository.

## Configuration Notes

Business calendar rules use `TIME_ZONE=Africa/Cairo` with timezone-aware UTC
storage (`USE_TZ=True`).

JWT access/refresh lifetimes are set with `JWT_ACCESS_TOKEN_MINUTES` (default
60) and `JWT_REFRESH_TOKEN_DAYS` (default 7). Token claims are unchanged. A
12-hour working shift is supported when the frontend silently refreshes;
production must not override refresh lifetime to an incompatible short value.
Do not set refresh lifetime to exactly 12 hours.

## Deployment

Keep `scripts/deploy-sloty-backend` as the canonical production executable.
CI SSHs to the VPS and runs that file from the git worktree:

```bash
sudo /srv/sloty/backend/scripts/deploy-sloty-backend <SHA> <TAG>
```

There is no `/usr/local/sbin` copy. The script discovers the repository root
from git, snapshots its own `scripts/` bundle to `/tmp` before checkout, uses
`<repo>/.venv` (`Python 3.12.14` from `/opt/sloty-runtime/python/`), loads
`/etc/sloty/env/prod.env`, restarts only `sloty-backend.service`, and must not
manage PostgreSQL, Nginx, or system Python. The canonical unit is
`deploy/systemd/sloty-backend.service`; it loads only `prod.env`, never the
backup credentials. CI calculates the release tag once, deploys that exact SHA
and tag, then creates or reuses the GitHub Release with the same values.
A release starts only after both the test job and the PostgreSQL concurrency
job pass.

## Runtime Version

Authenticated `GET /api/system/version/` returns the backend release running on
this host:

```json
{
  "service": "sloty-backend",
  "release": "v0.7.0",
  "commit": "91bd3aa28e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b"
}
```

- `release` is the human-readable deployment tag (`v<major>.<minor>.<patch>`).
- `commit` is the exact deployed Git SHA and the authoritative technical identity.
- The endpoint reads `/etc/sloty/backend-release.json`. It does not query GitHub,
  `master`, `.git`, or the database.
- Local development and tests usually have no metadata file; the response stays
  HTTP 200 with `"release": null` and `"commit": null`.
- Production writes that file only after a successful deploy and
  `GET http://127.0.0.1:8020/health/` (`Host: api.sloty.baronlearning.com`),
  using an atomic rename. A failed deploy leaves the previous file in place.
- Responses use `Cache-Control: no-store`.

## Production Environment Files

The repository tracks placeholder-only templates in `env/`. Django production
configuration is defined by `config/settings/base.py`: `SECRET_KEY`, `DEBUG`,
`ALLOWED_HOSTS`, PostgreSQL `DB_*` settings, optional `DB_SSLMODE`, and the
optional JWT and SQL-query-stat settings. `config.settings.production` refuses
to start unless `DB_ENGINE` is PostgreSQL. Local and test settings still
accept SQLite. `DEBUG` is read from the environment and is not forced off.
No email-specific environment variables are currently consumed by this backend.
R2 uses one shared `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, and
`R2_SECRET_ACCESS_KEY` connection configuration. Bucket names remain
purpose-specific: application images use `R2_BUCKET_IMAGE` in `prod.env`,
while PostgreSQL backups use `R2_BUCKET_BACKUP` in `backup.env`. The shared
credentials require Cloudflare token access to each intended bucket. Backup-only
settings are not loaded by Gunicorn.

On each trusted operator machine, secure local copies may use the same paths as
the VPS. These files are private and are never part of Git:

```text
/etc/sloty/env/prod.env
/etc/sloty/env/backup.env
```

To migrate the VPS without restarting against a missing env file, run the
following in order. The `--no-clobber` copy preserves an already prepared new
production file when retrying. Keep the old file until health verification
passes.

```bash
sudo install -d -o root -g sloty -m 0750 /etc/sloty/env
sudo cp -a /etc/sloty/backend.env /root/sloty-backend.env.before-env-migration
sudo cp --no-clobber /etc/sloty/backend.env /etc/sloty/env/prod.env
sudo chown root:sloty /etc/sloty/env/prod.env
sudo chmod 0640 /etc/sloty/env/prod.env
sudo touch /etc/sloty/env/backup.env
sudo chown root:root /etc/sloty/env/backup.env
sudo chmod 0600 /etc/sloty/env/backup.env
sudoedit /etc/sloty/env/backup.env
sudo cp -a "$(systemctl show -p FragmentPath --value sloty-backend.service)" /root/sloty-backend.service.before-env-migration
sudo install -o root -g root -m 0644 /srv/sloty/backend/deploy/systemd/sloty-backend.service /etc/systemd/system/sloty-backend.service
sudo systemctl daemon-reload
sudo systemctl restart sloty-backend.service
sudo systemctl is-active --quiet sloty-backend.service
curl --fail --silent --show-error --header 'Host: api.sloty.baronlearning.com' http://127.0.0.1:8020/health/
sudo rm /etc/sloty/backend.env
```

If verification fails, restore the old path and the saved unit, then reload
and restart:

```bash
sudo cp -a /root/sloty-backend.env.before-env-migration /etc/sloty/backend.env
sudo cp -a /root/sloty-backend.service.before-env-migration /etc/systemd/system/sloty-backend.service
sudo systemctl daemon-reload
sudo systemctl restart sloty-backend.service
sudo systemctl is-active --quiet sloty-backend.service
```

## PostgreSQL Backups and Restore

The backup and restore commands are `scripts/backup-sloty-db` and
`scripts/restore-sloty-db`. The host installer is
`scripts/install-host-dependencies`; it installs only `rclone` when needed and
validates PostgreSQL 17 `pg_dump`, `pg_restore`, and `psql`. These are host
tools, not Python requirements. Debian `needrestart` is set to list-only mode
during package installation; schedule any reported service restarts or reboot
separately during a maintenance window.

Prepare the root-only Sloty backup parent and install the repo-owned systemd
units as symlinks. Keep `/etc/sloty/env/backup.env` root-only (`root:root`, mode
`0600`); systemd reads both environment files before starting the backup as
`root`. The script creates `/var/backups/sloty/postgres` as `root:root` mode
`0700`; archive files are mode `0600`. The backend service itself does not load
`backup.env`.

```bash
sudo /srv/sloty/backend/scripts/install-host-dependencies
sudo install -d -o root -g root -m 0700 /var/backups/sloty
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-db-backup.service /etc/systemd/system/sloty-db-backup.service
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-db-backup.timer /etc/systemd/system/sloty-db-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now sloty-db-backup.timer
```

The timer runs once daily at 09:00 `Africa/Cairo`, independent of the host
timezone, and catches up once after downtime. The service writes a PostgreSQL
custom archive compressed with Zstandard level 9, validates it with
`pg_restore --list`, uploads only to `sloty-prod-backups/db/`, and compares the
listed remote filename and size with the local archive before deleting it.
Rclone uses environment-backed Cloudflare S3 settings and `/dev/null` as its
config path; it does not create a persistent `rclone.conf`. Its S3 `no_head`
setting skips the post-PUT HEAD request that R2 rejected; the script performs
its own list-based size/name check afterward. See the
[PostgreSQL 17 `pg_dump` options](https://www.postgresql.org/docs/17/app-pgdump.html)
and [rclone S3 options](https://rclone.org/s3/). No ACL header is forced.

Successful uploads remove their local temporary archive. Failed uploads or
remote verification leave a valid local dump for recovery; dumps older than
seven days are pruned at the next run. R2 lifecycle retention remains configured
on the bucket, not by this script.

To run the first backup and inspect its result without showing environment
contents:

```bash
sudo systemctl start sloty-db-backup.service
sudo systemctl --no-pager --full status sloty-db-backup.service
sudo journalctl -u sloty-db-backup.service --since today --no-pager
```

For a restore test, first create an empty `sloty_restore_test` database owned
by or accessible to the configured DB role. Replace the sample object name with
one listed under the R2 `db/` prefix. `systemd-run` supplies the same env files
without shell-parsing them; the restore command validates the archive before
restoring and will not drop or clean the target.

```bash
sudo systemd-run --wait --pipe --collect --unit=sloty-db-restore-test \
  --property=EnvironmentFile=/etc/sloty/env/prod.env \
  --property=EnvironmentFile=/etc/sloty/env/backup.env \
  /srv/sloty/backend/scripts/restore-sloty-db \
  --database sloty_restore_test \
  --remote 'sloty_prod-YYYY-MM-DDTHHMMSS+0300.dump'
```

Restoring to `sloty_prod` additionally requires
`--confirm-production sloty_prod`. Do not use that confirmation for a test
restore. To disable and remove only the scheduled backup units while preserving
local and R2 archives:

```bash
sudo systemctl disable --now sloty-db-backup.timer
sudo systemctl stop sloty-db-backup.service
sudo rm /etc/systemd/system/sloty-db-backup.timer /etc/systemd/system/sloty-db-backup.service
sudo systemctl daemon-reload
```

HOLD expiry is evaluated in real time by booking logic.
The systemd timer periodically persists cleanup.
The timer is not required for slot availability correctness.

Install the oneshot cleanup the same way as the backup timer (user `sloty`,
`prod.env` only, every 5 minutes, `Persistent=true`):

```bash
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-hold-expiry.service /etc/systemd/system/sloty-hold-expiry.service
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-hold-expiry.timer /etc/systemd/system/sloty-hold-expiry.timer
sudo systemctl daemon-reload
sudo systemctl enable --now sloty-hold-expiry.timer
```

Unauthenticated `GET /health/` is the dedicated deploy/systemd probe and returns
`{"status": "ok"}` without touching PostgreSQL.

## Court Image Storage

Production requires these host-local `prod.env` values (never commit real
credentials): `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET_IMAGE`, and `IMAGES_PUBLIC_BASE_URL`. Test storage is faked/mocked and makes no R2
network calls. Production also needs an R2 bucket, S3-compatible API
credentials limited to that bucket, and a public Cloudflare/CDN custom-domain
mapping for the configured base URL. See [the image pipeline guide](image-pipeline.md).
