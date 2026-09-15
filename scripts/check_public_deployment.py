"""Report unresolved public deployment configuration without printing personal values."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import config


def findings(settings=config) -> list[str]:
    """Check declared settings; this cannot verify contracts or provider-side logging."""
    missing = []
    for name in ("OPERATOR_NAME", "PRIVACY_CONTACT_EMAIL", "HOSTING_DETAILS"):
        if not getattr(settings, name).strip():
            missing.append(f"{name}: unresolved")
    if not settings.HETZNER_DPA_CONFIRMED:
        missing.append("HETZNER_DPA_CONFIRMED: account agreement not confirmed")
    if (
        not settings.MINIMAL_DATA_MODE
        and settings.SENTRY_ENABLED
        and settings.SENTRY_DSN
        and not settings.MONITORING_DETAILS.strip()
    ):
        missing.append("MONITORING_DETAILS: provider, country and retention unresolved")
    if (
        not settings.MINIMAL_DATA_MODE
        and settings.BACKUP_ENABLED
        and not settings.BACKUP_DETAILS.strip()
    ):
        missing.append("BACKUP_DETAILS: provider, country and retention unresolved")
    if (
        not settings.MINIMAL_DATA_MODE
        and not settings.AGGREGATE_STATS_ONLY
        and not settings.SCHEDULER_ENABLED
    ):
        missing.append("SCHEDULER_ENABLED: arrange equivalent periodic retention cleanup")
    url = urlsplit(str(settings.SITE_URL))
    host = (url.hostname or "").rstrip(".")
    placeholders = ("localhost", "example.com", "example.net", "example.org")
    if (
        url.scheme != "https"
        or not host
        or host in placeholders
        or host.endswith(tuple(f".{domain}" for domain in placeholders))
    ):
        missing.append("SITE_URL: configure the actual public HTTPS origin")
    return missing


def main() -> int:
    """Print missing configuration keys only, leaving deliberate blanks untouched."""
    missing = findings()
    if missing:
        print("\n".join(missing), file=sys.stderr)
        return 1
    print(
        "Declared settings present; independently verify providers, contracts and public notices."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
