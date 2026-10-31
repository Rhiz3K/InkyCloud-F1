# Deployment Guide

This page is the routing index for deployment documentation. Configuration values are defined
once in [`.env.example`](./.env.example); copy that file to `.env` and change only values needed
by your environment.

## Choose a deployment path

| Target | Guide | Artifact |
| --- | --- | --- |
| Docker or Docker Compose | [SELF-HOSTING.md](./SELF-HOSTING.md) | GHCR image or local source build |
| Coolify | [COOLIFY.md](./COOLIFY.md) | Dockerfile build from Git `main` |
| Local development | [SELF-HOSTING.md](./SELF-HOSTING.md#local-development) | Python 3.14 + locked `uv` environment |

The production deployment (Coolify) builds the Dockerfile from Git `main`; every merge to `main`
is a production deploy. Every release also publishes `ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z` (plus
`sha-<short>` and `latest`) as an optional artifact for self-hosting. Version tags are immutable
rollback targets for self-hosters; `latest` follows the newest release.

## Production invariants

- Mount persistent storage at `/app/data`.
- Run one replica. SQLite, the scheduler, and in-memory coordination are single-instance by
  design.
- Route the platform health check to `GET /health/ready`; `GET /health` is liveness only.
- Terminate TLS at the platform or reverse proxy and set `SITE_URL` to the public apex URL.
- Keep secrets in deployment configuration, never in the repository.
- Production requires `OPERATOR_NAME` and `PRIVACY_CONTACT_EMAIL`.

Operational backup, restore, upgrade, rollback, and troubleshooting procedures live in the two
target-specific guides above. Asset maintenance is documented in
[`scripts/README.md`](./scripts/README.md) and [`BMP_PROCESSING.md`](./BMP_PROCESSING.md).

## Releases

Follow the [Release PR flow](./CONTRIBUTING.md#pull-request-process): open "Release vX.Y.Z",
move Unreleased notes to a new version heading, and bump `version` in `pyproject.toml`.
After merge, `.github/workflows/release.yml` creates the release and tag; the tag triggers
`publish-container`. See [Coolify releases](./COOLIFY.md#releases) for artifact details.
Do not create tags manually: a tag newer than `CHANGELOG.md` fails
`python -m app.utils.release_validation`.
