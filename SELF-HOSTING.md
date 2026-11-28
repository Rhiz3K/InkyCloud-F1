# Self-Hosting Guide

This guide covers generic Docker and local-development operation. For Coolify, use
[`COOLIFY.md`](./COOLIFY.md). The complete configuration reference is
[`.env.example`](./.env.example); it is the single source of truth for environment names,
defaults, and comments.

## Requirements

- Docker Engine with Docker Compose v2, or Python 3.15 and `uv` for development
- one persistent volume for `/app/data`
- one application replica
- outbound HTTPS access to the F1 and optional weather APIs

## Run the released image

Copy the environment template and choose an immutable release tag. Set `F1_VERSION` to the
newest tag on the [Releases page](https://github.com/Rhiz3K/InkyCloud-F1/releases):

```bash
git clone https://github.com/Rhiz3K/InkyCloud-F1.git
cd InkyCloud-F1
cp .env.example .env
F1_VERSION=v1.4.0
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose pull
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose up -d --no-build
```

Edit `.env` before exposing the service. At minimum, set `SITE_URL` to the public URL. The
Compose file maps a named volume to `/app/data` and exposes port 8000.

The image runs with `APP_ENV=production` and refuses to start unless `OPERATOR_NAME` and
`PRIVACY_CONTACT_EMAIL` are set, because the privacy page names the data controller. The
template's `APP_ENV=development` overrides the image default, so set `APP_ENV=production` in
`.env` for a public deployment. `PRIVACY_MAIL_RETENTION_DAYS` (default 365, at most 3650) states
how long e-mails to the privacy contact are kept after the request is closed.

Use these commands for normal operation:

```bash
docker compose ps
docker compose restart f1-eink-cal
docker compose down
```

The Compose file uses the `none` logging driver, so container output is not stored and
`docker compose logs` prints nothing. Application logging is a separate switch: while
`MINIMAL_DATA_MODE=true` or `AGGREGATE_STATS_ONLY=true` (the default), the application turns
Python logging off, so changing the logging driver alone shows almost nothing. To read logs
while troubleshooting:

1. Add a `docker-compose.override.yml` next to `docker-compose.yml`:

   ```yaml
   services:
     f1-eink-cal:
       logging:
         driver: local
   ```

2. For application messages, also set `AGGREGATE_STATS_ONLY=false` in `.env` (with
   `MINIMAL_DATA_MODE=false`). This is a privacy tradeoff: log lines can contain request
   details and error payloads, and this legacy profile can store individual request records
   instead of hourly totals (see [data collection](./docs/data-collection.md)).
3. Recreate the container with the same pinned release, so Compose does not build or select the
   local `f1-eink-cal:local` image, and follow the logs:

   ```bash
   F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose up -d --no-build
   docker compose logs -f f1-eink-cal
   ```

When you are done, delete `docker-compose.override.yml`, restore `AGGREGATE_STATS_ONLY=true`, and
run the same `docker compose up -d --no-build` command again. Until the container is recreated it
keeps the `local` driver and keeps storing its output. For a [source build](#build-from-source),
use `docker compose up -d` instead of the pinned-image command.

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

The multi-stage Dockerfile uses Python 3.15, installs the hash-locked production dependencies,
and runs as a non-root user. Editable source artwork under `artwork/` is excluded; only runtime
assets are copied into the image.

## Persistent data and replicas

The default paths are:

- SQLite: `/app/data/f1.db`
- pregenerated BMPs: `/app/data/images`
- mutable circuit history: `/app/data/circuits_jolpica.json`

History is migrated once from a legacy `circuits_data.json`, copying only historical results.

Mount the common `/app/data` root, including when custom paths are nested below it. Readiness
rejects a container path under `/app/data` when that root is not a real mount, and performs a
write probe without blocking the event loop.

Keep the replica count at one. Multiple replicas would run independent schedulers and caches
against the same SQLite database.

## Small instance profile

By default the hourly job pregenerates every language, palette and weather variant (13 × 4 × 3
calendars, 52 teams images and their PNG previews) and refreshes current weather for every
circuit of the season. A Raspberry Pi or a 1-vCPU VPS that feeds one or two displays at home
only needs a fraction of that. Limit it in `.env`:

```bash
# One Czech BWRY display: 3 calendars, 1 teams image and 4 previews per hour
PREGENERATE_LANGUAGES=cs
PREGENERATE_DISPLAYS=bwry
# Optional: only the weather=off calendar (off = no current/race_day variants)
PREGENERATE_WEATHER_TYPES=off
# Refresh current weather only for the next race (about 1 instead of ~25 requests per hour)
WEATHER_PREFETCH_ALL_CIRCUITS=false
```

- Values are comma-separated and case-insensitive; empty means all. Unknown entries are ignored
  with a warning, and a list without any supported entry falls back to all.
- `weather=off` calendars are always pregenerated because readiness depends on them.
- Weather variants are `current`, `race_day` and `weekend`. An empty list keeps `current` and
  `race_day`; list `weekend` explicitly (e.g. `PREGENERATE_WEATHER_TYPES=race_day,weekend`) to
  pregenerate `weather_type=weekend` calendars and prefetch their session forecasts hourly. The
  weekend forecast shares the race-day request, so it adds no Open-Meteo requests. Fahrenheit
  (`temp_unit=f`) is always rendered on demand.
- Everything outside the profile keeps working: a request for another language, palette or
  weather type is rendered on demand and cached in memory, exactly like a timezone variant.
  The first such request costs one render.
- With `WEATHER_PREFETCH_ALL_CIRCUITS=false`, the next race (and, during the hours after its
  start, the following one) stays warm. A calendar for another circuit fetches its weather when
  it is requested, within the same Open-Meteo quota.
- Pregenerated files that fall outside a new profile are deleted after the next fully
  successful hourly run; PNG previews outside it are deleted by the next run. With
  `PREGENERATE_WEATHER_TYPES=off` the hourly job does not fetch weather at all, so an
  unavailable weather API does not mark the run degraded.

`ops status` (below) prints the active profile and how many files one run writes.

## Health and monitoring

- `GET /health` reports process liveness and has no dependency checks.
- `GET /health/ready` returns 200 only when SQLite responds, storage is mounted and writable, and
  at least one core calendar BMP was generated within the last six hours. Its top-level status is
  `ready` after a complete run or `degraded` when optional weather or secondary variants failed.
- `/api/stats` reports request totals including separate 200 and 304 status counts.
- `ops status` inside the container explains a `degraded` or unready state with stable reason
  codes (see [Diagnostics](#diagnostics)).

The Dockerfile and Compose health checks use `/health/ready`. Initial startup remains unready
until the first core calendar artifact is published. A partial run refreshes the marker and reports
`degraded` when the service can still serve calendars; a run with no core output leaves the marker
unchanged. The built-in five-minute start period accommodates the first upstream refresh and
localized render cycle without marking a healthy cold deployment as failed.

## Diagnostics

The Compose file stores no logs, so a `degraded` readiness does not say why. Ask the container:

```bash
docker compose exec f1-eink-cal ops status
docker compose exec f1-eink-cal ops status --json
```

The report reads the database read-only (it also works while the service is stopped) and
contains no visitor statistics, file paths, hostnames, bucket names or secrets. It shows the
overall `status` (`ready`, `degraded`, `not_ready`; exit code 0, 1, 2), the `reasons`, the last
generation, Open-Meteo quota usage per window, the daily historical refresh (last success and
incomplete streak), stored weekend podiums, pregenerated files on disk, the last external and
local backup and the active pregeneration profile.

Reason codes are stable and only ever added:

| Code | Meaning |
| --- | --- |
| `database_unavailable`, `storage_unavailable` | SQLite cannot be read, or `/app/data` is not mounted or not writable |
| `generation_starting`, `generation_invalid`, `generation_stale` | no calendar generated yet, an unreadable marker, or none within six hours (`/health/ready` reports the same states as `checks.generation.status`) |
| `calendar_variant_failed`, `weather_variant_failed`, `timezone_variant_failed`, `teams_variant_failed` | some pregenerated images of the last run failed to render |
| `weather_unavailable` | weather expected for the next race could not be loaded in the last run |
| `teams_data_unavailable` | teams or standings data could not be fetched or was incomplete |
| `generation_degraded` | a degraded run recorded by an older version, without details |
| `weather_quota_exhausted` | an Open-Meteo window (minute, hour, day, 31 days) is used up |
| `historical_refresh_incomplete`, `historical_refresh_overdue` | three or more incomplete daily refreshes in a row, or no successful one for two days |
| `backup_failed` | the latest external backup failed after the last successful one |

Quota, historical refresh and backup reasons appear only here; `/health/ready` stays as
minimal as before and reports `degraded` only for generation problems.

For remote monitoring, set `ADMIN_API_TOKEN` and request the same JSON (plus in-memory cache
occupancy) over HTTP:

```bash
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" https://f1.example.com/api/ops/status
```

Without `ADMIN_API_TOKEN` the endpoint answers 404; a missing or wrong token answers 401. The
token is compared in constant time, attempts share the statistics rate limit, and responses are
sent with `Cache-Control: no-store`. The endpoint also works with `MINIMAL_DATA_MODE=true`.

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

Each backup is a consistent SQLite snapshot. With `BACKUP_INCLUDE_DATA_FILES=true` (default)
the mutable circuit history `circuits_jolpica.json` travels inside the same file (in a
`inkycloud_backup_files` table that a restore removes again), so a restore brings back both.

External backups stay off with `MINIMAL_DATA_MODE=true`. Local backups never leave the machine
and work in every profile; they are written to `BACKUP_LOCAL_DIR` (default
`/app/data/backups`, on the same volume, so also copy them elsewhere):

```bash
docker compose exec f1-eink-cal backup now --local
```

Each local backup is completed in a private file on the backup volume and published atomically. Backups made in the same second receive numeric suffixes. `latest` selects the highest suffix for that timestamp.

Local backups older than `BACKUP_RETENTION_DAYS` are removed when a new one is written.

Run `backup list --verify` to check each archive's SQLite header, InkyCloud schema and page integrity. S3 archives are downloaded to a temporary file. A failed check gives a nonzero exit status in text and JSON output.

```bash
docker compose exec f1-eink-cal backup list
docker compose exec f1-eink-cal backup list --verify --json
```

The service holds a shared kernel lock on `<database>.lock` throughout its lifetime. Restore requires an exclusive lock from staging through publication of the database and circuit files. Keep the lock file in place. The kernel releases the lock on process exit, including a crash. `--force` still requires exclusive access.

Restore with the service stopped, in a one-off container from the same pinned release as the
service (for a [source build](#build-from-source), leave out `F1_IMAGE=...`):

```bash
docker compose stop f1-eink-cal
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose run --rm --no-deps f1-eink-cal backup restore latest
docker compose start f1-eink-cal
```

`latest` is the newest S3 backup when S3 is configured, otherwise the newest local backup; pass
a name from `backup list` or `--source local|s3` to choose. The restore:

1. copies the backup to a working file and refuses to continue unless `PRAGMA integrity_check`
   reports `ok`;
2. refuses a backup written by a newer schema version than the running image (`--force`
   overrides this);
3. refuses while the database is in use (`-wal`/`-shm` files present or the database locked);
   `--force` is only for stale files left behind by a crash, or to replace a current database
   that is no longer a readable SQLite file (the restore refuses that without `--force`);
4. runs the normal schema migrations on the working file, so backups from older versions are
   upgraded before anything live changes;
5. saves the current database and circuit history as `f1_pre_restore_<timestamp>.db` in
   `BACKUP_LOCAL_DIR` (a second restore within the same second gets a `_1`, `_2`, ... suffix
   and never overwrites it); restore that name to undo. An unreadable database is kept as a
   raw byte copy instead (with its `-wal`/`-shm`, without the circuit history), which can only
   be copied back by hand;
6. writes the backup through SQLite's backup API and restores `circuits_jolpica.json` when the
   backup contains it. Undo also removes a circuit file that was absent at the time of the safety snapshot. Historical database-only archives preserve the current circuit file.
   Restoring a new pre-restore safety snapshot requires a release that understands its circuit-file presence manifest.

A failure before step 6 leaves the database unchanged. If step 6 fails, the command says so and
names the safety snapshot to restore before starting the service.

Add `--yes` to skip the confirmation prompt. Pregenerated images are rebuilt by the next hourly
run. Keep the safety snapshot until readiness and expected statistics are verified.

## Database maintenance

The bundled reset tool provides explicit scopes:

```bash
docker compose exec f1-eink-cal reset-db info
docker compose exec f1-eink-cal reset-db stats
docker compose exec f1-eink-cal reset-db cache
```

`reset-db cache` also clears the stored race weekend podiums (`weekend_results`); restart the
service to drop the copy held in memory. `reset-db all` deletes the database file, so the application must not be running: the live
process keeps its connection to the unlinked file and cannot recreate the schema until it
restarts. Stop the service and run the reset in a one-off container from the same pinned
release (for a source build, leave out `F1_IMAGE=...`):

```bash
docker compose stop f1-eink-cal
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose run --rm --no-deps f1-eink-cal reset-db all
docker compose start f1-eink-cal
```

The tool refuses `all` while WAL/SHM sidecar files indicate an open connection; pass `--force`
only after a crash left stale sidecars behind. The delete scopes destroy data, so take a backup
first. Schema versioning and additive migrations run automatically when the shared database is
opened.

## Data updates

The running scheduler refreshes completed-race historical results daily and regenerates BMPs
hourly. For `weekend_mode=auto` it also fetches the current weekend's qualifying and race top
three from Jolpica, but only in the 12 hours after each of those sessions, at most once an hour
and until a complete podium is stored (about 2–6 requests per race weekend, none otherwise). Season calendar source files are maintained by the GitHub Actions workflow at
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
uv python install 3.15
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

Run `ops status` (see [Diagnostics](#diagnostics)) for reason codes, then inspect the JSON body
of `/health/ready` and the container logs (see
[Run the released image](#run-the-released-image) for enabling them). The response identifies database,
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
