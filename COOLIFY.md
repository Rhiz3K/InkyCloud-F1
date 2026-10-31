# Coolify Deployment Guide

Production Coolify builds the repository Dockerfile from Git branch `main`. Every merge to
`main` automatically builds and deploys production; there is no separate promotion step.
Merges therefore need the maintainer's approval. Generic configuration and operations are in
[`SELF-HOSTING.md`](./SELF-HOSTING.md).

## Create the resource

1. In Coolify, create an application from the public Git repository
   `https://github.com/Rhiz3K/InkyCloud-F1`.
2. Select branch `main` (`git_branch: main`) and the Dockerfile build pack
   (`build_pack: dockerfile`), using the repository `Dockerfile`.
3. Expose container port `8000`.
4. Add persistent storage with destination `/app/data`.
5. Import values from [`.env.example`](./.env.example) and adjust them for production. Set
   `APP_ENV=production`, `OPERATOR_NAME`, and `PRIVACY_CONTACT_EMAIL`; set `SITE_URL` to the
   final HTTPS apex URL.
6. Keep one replica, enable automatic deployments from `main`, and deploy.

## Persistent storage

The storage destination must be exactly `/app/data`, even when `DATABASE_PATH` or `IMAGES_PATH`
uses a nested custom directory. The application verifies the common root is a mount and writable.

After the first deployment, open the container terminal and verify:

```bash
mountpoint /app/data
ls -la /app/data
```

The application runs as UID 1000. If a bind mount is used, its host directory must be writable by
that UID.

## Domains and proxy headers

Add the HTTPS apex domain and optionally its `www` alias in Coolify. Point DNS records to the
Coolify host and let its proxy provision TLS. Set:

```dotenv
SITE_URL=https://f1.example.com
FORWARDED_ALLOW_IPS=<Coolify proxy network or address>
```

Do not set `FORWARDED_ALLOW_IPS=*` on an Internet-facing deployment.

## Health check

Configure the resource health path as `/health/ready` on port 8000. The endpoint becomes ready
after SQLite, persistent storage, and a recent core calendar generation all pass. Optional weather
or secondary-variant failures return HTTP 200 with top-level status `degraded`; `/health` is
liveness and must not be used for deployment readiness.

The first deployment also refreshes upstream data before generating every localized variant.
Retain the Dockerfile `HEALTHCHECK` with its five-minute start period and three retries so this
valid cold start remains in the `starting` state; `/health/ready` still exposes its detailed 503
response during that window.

## Configuration

`.env.example` is the complete configuration reference. Keep all values in Coolify's environment
UI and mark S3, Sentry, and admin-token values as secrets. Redeploy after changing
environment values.

Important operational rules:

- one replica only
- `/app/data` persistent storage is mandatory
- `STATS_RETENTION_DAYS=400` retains the full 365-day dashboard
- `BACKUP_ENABLED=true` requires a valid S3 target and cron schedule
- the Dockerfile sets `APP_ENV=production`; the production validator refuses startup unless
  `OPERATOR_NAME` and `PRIVACY_CONTACT_EMAIL` are set (the data controller identity on the privacy
  page); the `.env.example` value `APP_ENV=development` overrides this, so set `APP_ENV=production`
  or drop that line when importing
- `PRIVACY_MAIL_RETENTION_DAYS` (default 365) is the period, stated on the privacy page, for
  which e-mails to the privacy contact are kept after the request is closed

## Releases

Open a PR titled "Release vX.Y.Z" that moves the Unreleased notes to a new version heading in
`CHANGELOG.md` and bumps `version` in `pyproject.toml`; see
[`CONTRIBUTING.md`](./CONTRIBUTING.md#pull-request-process). After merge,
`.github/workflows/release.yml` creates the GitHub release and `vX.Y.Z` tag. The tag then runs
the `publish-container` job, publishing `ghcr.io/rhiz3k/inkycloud-f1` tags `vX.Y.Z`,
`sha-<short-commit>` and `latest` with SBOM and provenance.

The GHCR image is an optional self-hosting artifact and is not used by production Coolify.
The release PR merge deploys production just like every other merge to `main`.

Do not create tags by hand. A manual tag newer than `CHANGELOG.md` makes
`python -m app.utils.release_validation` fail with
"Latest CHANGELOG version must not be older than the latest git tag".

After deployment, confirm `/health/ready`, a calendar BMP, a teams BMP, and the statistics page
before removing any backup.

## Rollback

Take an S3 backup before an upgrade or rollback because migrations are versioned; an older
application version may not be able to use a newer schema.

Revert the offending change on `main` through a PR; merging automatically rebuilds and redeploys
production. Alternatively, use Coolify's redeploy/rollback of an earlier deployment from its
deployment history if that build is still available on the host. Verify `/health/ready` and logs.

## Backup commands

Use the Coolify terminal for the same bundled commands as Docker:

```bash
backup info
backup test
backup now
```

The automatic schedule runs inside the single application replica. Invalid enabled cron syntax
fails startup visibly.

## Troubleshooting

### Build fails

Check the Coolify build log, the repository `Dockerfile`, and `uv.lock` / `requirements.lock`
for the failing build step.

### Readiness remains unhealthy

Open `/health/ready` or inspect it from the container. Its `checks` object distinguishes SQLite,
storage, and generation failures, including `starting`, `stale`, and still-servable `degraded`
generation states. Confirm `/app/data` is a storage mount rather than a directory created on the
ephemeral container layer.

### Data resets after a deployment

Recreate the storage mapping with destination `/app/data`, restore the latest backup, and verify
the mount before redeploying. Environment variables alone do not create persistence.

### Images stop updating

Review scheduler and upstream API errors in logs. Readiness intentionally remains green while a
core calendar generation is within the six-hour serving tolerance. Optional failures are reported
as `degraded`; readiness turns 503 only when no usable core generation remains fresh.

### Source changes are not visible

Check that the change is merged to `main` and the latest Coolify deployment has finished and is
healthy. Every merge triggers a Dockerfile build and production deployment.

## Optional: deploy the GHCR release image

Self-hosters can use Docker Compose per [`SELF-HOSTING.md`](./SELF-HOSTING.md), or create their
own Coolify resource:

1. Create a resource from a Docker image.
2. Set the image to `ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z`, using an exact version tag.
3. Configure port `8000`, persistent storage `/app/data`, one replica, and the production
   environment values described above.
4. Add registry credentials with package read permission only if the package is private.
   Public packages require no registry secret.
5. Deploy and verify `/health/ready` and logs.

To upgrade or roll back, take an S3 backup first, change the image tag to the desired exact
`vX.Y.Z` version, pull, and redeploy. Version tags provide explicit rollback targets; `latest`
follows the newest release.

If the image cannot be pulled, confirm the lowercase image name, exact tag, package visibility,
and registry credentials. A GitHub token used for a private package needs `read:packages`.
