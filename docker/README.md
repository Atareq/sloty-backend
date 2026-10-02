# Docker

Two independent stacks. Both read **every** value from the repository-root `.env`;
neither contains a hardcoded credential, hostname, or port.

| Stack | Path | Purpose |
| :--- | :--- | :--- |
| Development | `docker/dev/` | Source bind-mounted, `runserver`, PostgreSQL + Redis |
| Production | `docker/prod/` | Multi-stage build, non-root, gunicorn, baked static files |

## Running

Always invoke from the **repository root**, and always pass `--env-file`:

```bash
# Development
docker compose --env-file .env -f docker/dev/docker-compose.yml up --build

# Production (parity stack)
docker compose --env-file .env -f docker/prod/docker-compose.yml up -d --build
```

`--env-file` is what resolves `${VAR}` interpolation inside the compose files.
The per-service `env_file:` entry is what populates the container environment.
Both point at the same root `.env`, so configuration has exactly one source.

## Required `.env` values for Docker

`.env.example` ships SQLite defaults so the project runs without Docker. To use
either stack, switch the database and point the hostnames at the compose services:

```ini
DB_ENGINE=postgres
DB_NAME=sloty
DB_USER=sloty
DB_PASSWORD=<choose one>
DB_HOST=db          # compose service name, not localhost
DB_PORT=5432
REDIS_URL=redis://redis:6379/0
```

`DB_HOST=db` and `redis://redis...` are container-network names. When running
Django on the host against the compose databases instead, use `localhost` with
the published ports (`DB_PORT_PUBLISHED`, `REDIS_PORT_PUBLISHED`).

## Migrations

Neither image runs `migrate` on start. Schema changes stay an explicit step:

```bash
docker compose --env-file .env -f docker/dev/docker-compose.yml exec web python manage.py migrate
```

Per root `AGENTS.md` section 7, the pre-release baseline expects a **recreated**
database. To reset the dev stack:

```bash
docker compose --env-file .env -f docker/dev/docker-compose.yml down -v
```

## Relationship to the production deploy

The live production deploy is [`scripts/deploy-sloty-backend`](../scripts/deploy-sloty-backend)
running on the host (root `AGENTS.md` section 10). `docker/prod/` does **not**
replace it, and nothing here manages Nginx, systemd, or the shared PostgreSQL
service. The production stack exists for local parity and future migration.

## Health

The production container's healthcheck uses `GET /health/` — the same
unauthenticated, in-process probe (no PostgreSQL, no JWT) the deploy script
requires. Do not repoint it at `/`.
