# Deployment Guide

This page is the routing index for deployment documentation. Configuration values are defined
once in [`.env.example`](./.env.example); copy that file to `.env` and change only values needed
by your environment.

## Choose a deployment path

| Target | Guide | Artifact |
| --- | --- | --- |
| Docker or Docker Compose | [SELF-HOSTING.md](./SELF-HOSTING.md) | GHCR image or local source build |
| Coolify | [COOLIFY.md](./COOLIFY.md) | Versioned GHCR image |
| Local development | [SELF-HOSTING.md](./SELF-HOSTING.md#local-development) | Python 3.14 + locked `uv` environment |

The production image is published as `ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z` for every release.
Version tags are immutable rollback targets; `latest` follows the newest release.

## Production invariants

- Mount persistent storage at `/app/data`.
- Run one replica. SQLite, the scheduler, and in-memory coordination are single-instance by
  design.
- Route the platform health check to `GET /health/ready`; `GET /health` is liveness only.
- Terminate TLS at the platform or reverse proxy and set `SITE_URL` to the public apex URL.
- Keep secrets in deployment configuration, never in the repository.

Operational backup, restore, upgrade, rollback, and troubleshooting procedures live in the two
target-specific guides above. Asset maintenance is documented in
[`scripts/README.md`](./scripts/README.md) and [`BMP_PROCESSING.md`](./BMP_PROCESSING.md).
