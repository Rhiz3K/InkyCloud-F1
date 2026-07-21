# Coolify Deployment Guide

Coolify should deploy the immutable GHCR release image, not rebuild the repository. Generic
configuration and operations are documented in [`SELF-HOSTING.md`](./SELF-HOSTING.md); this file
contains only Coolify-specific steps.

## Create the resource

1. In Coolify, create a resource from a Docker image.
2. Set the image to `ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z`.
3. Expose container port `8000`.
4. Add persistent storage with destination `/app/data`.
5. Import the values from [`.env.example`](./.env.example), adjust them for production, and set
   `SITE_URL` to the final HTTPS apex URL.
6. Keep the replica count at one and deploy.

Use an exact `vX.Y.Z` tag in production. `latest` is useful for a preview environment but removes
the deployment's explicit rollback identity.

If the GHCR package is private, add registry credentials with package read permission in
Coolify. Public packages require no registry secret.

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
only after SQLite, persistent storage, and a recent complete BMP generation all pass. `/health`
is liveness and must not be used for deployment readiness.

The first deployment also refreshes upstream data before generating every localized variant.
Retain the image's five-minute start period and three retries so this valid cold start remains in
the `starting` state; `/health/ready` still exposes its detailed 503 response during that window.

## Configuration

`.env.example` is the complete configuration reference. Keep all values in Coolify's environment
UI and mark S3, Sentry, analytics, and admin-token values as secrets. Redeploy after changing
environment values.

Important operational rules:

- one replica only
- `/app/data` persistent storage is mandatory
- `STATS_RETENTION_DAYS=400` retains the full 365-day dashboard
- `BACKUP_ENABLED=true` requires a valid S3 target and cron schedule
- `SITE_URL` and `ANALYTICS_HOSTNAME` serve different purposes

## Release deployment

Every `vX.Y.Z` Git tag publishes these GHCR tags:

- `vX.Y.Z`
- `sha-<short-commit>`
- `latest`

The release image includes an SBOM and provenance attestation. To upgrade, change the image tag
in Coolify from the current version to the new version, pull, and redeploy. Confirm
`/health/ready`, a calendar BMP, a teams BMP, and the statistics page before removing any backup.

## Rollback

Rollback does not require a rebuild:

1. Change the image tag back to the previous `vX.Y.Z` value.
2. Redeploy the resource.
3. Verify `/health/ready` and logs.

For a Docker host outside Coolify, the equivalent one-command rollback is:

```bash
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vPREVIOUS docker compose up -d --no-build
```

Database migrations are versioned, but take an S3 backup before every upgrade so data can be
restored if an older application version cannot use a future schema.

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

### Image cannot be pulled

Confirm the lowercase image name, exact tag, package visibility, and registry credentials. A
GitHub token used for a private package needs `read:packages`.

### Readiness remains unhealthy

Open `/health/ready` or inspect it from the container. Its `checks` object distinguishes SQLite,
storage, and generation failures. Confirm `/app/data` is a storage mount rather than a directory
created on the ephemeral container layer.

### Data resets after a deployment

Recreate the storage mapping with destination `/app/data`, restore the latest backup, and verify
the mount before redeploying. Environment variables alone do not create persistence.

### Images stop updating

Review scheduler and upstream API errors in logs. Readiness intentionally remains green while a
previous successful generation is still within the six-hour serving tolerance, then turns 503.

### Source changes are not visible

Coolify is pulling a release artifact. Source commits appear only after a new release tag builds
and publishes a new image; changing the tag is the deployment action.
