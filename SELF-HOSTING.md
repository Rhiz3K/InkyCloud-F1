# Self-Hosting Guide

This guide covers generic Docker and local-development operation. For Coolify, use
[`COOLIFY.md`](./COOLIFY.md). The complete configuration reference is
[`.env.example`](./.env.example); it is the single source of truth for environment names,
defaults, and comments.

## Requirements

- Docker Engine with Docker Compose v2, or Python 3.14 and `uv` for development
- one persistent volume for `/app/data`
- one application replica
- outbound HTTPS access to the F1 and optional weather APIs

## Run the released image

Copy the environment template and choose an immutable release tag:

```bash
git clone https://github.com/Rhiz3K/InkyCloud-F1.git
cd InkyCloud-F1
cp .env.example .env
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z docker compose pull
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z docker compose up -d --no-build
```

Edit `.env` before exposing the service. At minimum, set `SITE_URL` to the public URL. The
Compose file maps a named volume to `/app/data` and exposes port 8000.

Use these commands for normal operation:

```bash
docker compose ps
docker compose logs -f f1-eink-cal
docker compose restart f1-eink-cal
docker compose down
```

`docker compose down` keeps the named data volume. Do not add `--volumes` unless permanent data
deletion is intentional.

## Build from source

For unreleased development branches:

```bash
git clone https://github.com/Rhiz3K/InkyCloud-F1.git
cd InkyCloud-F1
cp .env.example .env
docker compose up -d --build
```

The multi-stage Dockerfile uses Python 3.14, installs the hash-locked production dependencies,
and runs as a non-root user. Editable source artwork under `artwork/` is excluded; only runtime
assets are copied into the image.

## Persistent data and replicas

The default paths are:

- SQLite: `/app/data/f1.db`
- pregenerated BMPs: `/app/data/images`
- mutable circuit history: `/app/data/circuits_data.json`

Mount the common `/app/data` root, including when custom paths are nested below it. Readiness
rejects a container path under `/app/data` when that root is not a real mount, and performs a
write probe without blocking the event loop.

Keep the replica count at one. Multiple replicas would run independent schedulers and caches
against the same SQLite database.

## Health and monitoring

- `GET /health` reports process liveness and has no dependency checks.
- `GET /health/ready` returns 200 only when SQLite responds, storage is mounted and writable, and
  at least one core calendar BMP was generated within the last six hours. Its top-level status is
  `ready` after a complete run or `degraded` when optional weather or secondary variants failed.
- `/api/stats` reports request totals including separate 200 and 304 status counts.

The Dockerfile and Compose health checks use `/health/ready`. Initial startup remains unready
until the first core calendar artifact is published. A partial run refreshes the marker and reports
`degraded` when the service can still serve calendars; a run with no core output leaves the marker
unchanged. The built-in five-minute start period accommodates the first upstream refresh and
localized render cycle without marking a healthy cold deployment as failed.

## Reverse proxy

Terminate HTTPS at your proxy and forward to port 8000. Set `FORWARDED_ALLOW_IPS` only to the
proxy addresses or CIDRs you control. Bind the published Compose port to loopback when devices do
not connect directly over the LAN:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

Set `SITE_URL=https://f1.example.com`; it controls canonical URLs, the sitemap, and apex/www
redirect behavior.

## Upgrade and rollback

Back up first, pull the new immutable tag, and recreate the service:

```bash
docker compose exec f1-eink-cal backup now
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vNEW docker compose pull
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vNEW docker compose up -d --no-build
```

Rollback is the same operation with the previous tag and is intentionally one command after the
image is available locally:

```bash
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vPREVIOUS docker compose up -d --no-build
```

Never use `latest` as the only rollback reference.

## Backup and restore

Configure an S3-compatible target using the backup section of `.env.example`, then validate it:

```bash
docker compose exec f1-eink-cal backup info
docker compose exec f1-eink-cal backup test
docker compose exec f1-eink-cal backup now
```

`BACKUP_CRON` uses standard five-field cron syntax in UTC. Sunday may be `0` or `7`; wrapped ranges
and steps are normalized for APScheduler. An invalid enabled backup schedule fails startup rather
than silently disabling backups.

To restore, stop the service, preserve the current `/app/data` directory, restore the selected
SQLite backup as `/app/data/f1.db`, ensure ownership matches container UID 1000, and start the
service. Keep the old data copy until readiness and expected statistics are verified.

## Database maintenance

The bundled reset tool provides explicit scopes:

```bash
docker compose exec f1-eink-cal reset-db info
docker compose exec f1-eink-cal reset-db stats
docker compose exec f1-eink-cal reset-db cache
docker compose exec f1-eink-cal reset-db all
```

The last three commands delete data. Take a backup first. Schema versioning and additive
migrations run automatically when the shared database is opened.

## Data updates

The running scheduler refreshes completed-race historical results daily and regenerates BMPs
hourly. Season calendar source files are maintained by the GitHub Actions workflow at
`.github/workflows/update-f1-data.yml`:

- weekly from March through November
- daily from December through February
- manual `workflow_dispatch` at any time

The workflow validates upstream structure and opens a pull request only when files changed.
Manual commands and the weekly artwork procedure are listed in
[`scripts/README.md`](./scripts/README.md).

## Local development

Use the same interpreter and lock as CI and production:

```bash
uv python install 3.14
uv sync --locked --group dev
cp .env.example .env
DATABASE_PATH=./data/f1.db IMAGES_PATH=./data/images uv run uvicorn app.main:app --reload
```

Run the complete local verification set:

```bash
uv lock --check
uv run ruff check .
uv run mypy
uv run interrogate -c pyproject.toml app scripts
uv run pytest -m "not benchmark" --cov=app --cov-branch
docker build -t f1-eink-cal:test .
```

Asset preprocessing requires the development group because monochrome flags use NumPy and
scikit-learn. See [`scripts/README.md`](./scripts/README.md) for the unified CLI.

## Troubleshooting

### Readiness is 503

Inspect the JSON body of `/health/ready` and container logs. The response identifies database,
storage, and generation checks separately. Generation status distinguishes `starting`, `stale`,
and a still-servable `degraded` state. Common 503 causes are a missing `/app/data` mount,
read-only ownership, or failure to publish any core calendar for more than six hours.

### Statistics disappear after redeploy

Confirm `/app/data` is a persistent volume and `DATABASE_PATH` remains inside it. A bind mount to
only the database file does not preserve generated images or mutable circuit history.

### Images are stale

Check scheduler logs and the readiness generation age. Requests fall back to on-demand rendering
after a pregenerated file exceeds the shared six-hour tolerance.

### Backups do not start

Validate `backup test`, credentials, bucket permissions, and `BACKUP_CRON`. Invalid cron syntax is
reported as a startup error.
