# Data collection and upgrades

The default profile keeps aggregate operational statistics:

```dotenv
MINIMAL_DATA_MODE=false
AGGREGATE_STATS_ONLY=true
PERF_METRICS_SAMPLE_RATE=0.1
STATS_RETENTION_DAYS=400
RAW_STATS_RETENTION_DAYS=30
SENTRY_ENABLED=true
SENTRY_TRACES_SAMPLE_RATE=0
UMAMI_ENABLED=false
BACKUP_ENABLED=false
```

Explicit environment values override these defaults. `MINIMAL_DATA_MODE=true`
disables collection entirely, including optional monitoring and S3 backups.

## Stored aggregates

- API requests are combined in memory by UTC hour and validated endpoint, language,
  timezone, season, race, display palette, automatic selection and response status.
  Calendar requests also record catalogue-validated map style, source and requested
  accents. Each group stores count, total bytes and total/minimum/maximum duration
  rounded to 10 ms. There is no row per request or visitor in this profile.
- `api_call_totals` retains these groups for `STATS_RETENTION_DAYS` (default 400 days).
  Counts are requests, not unique visitors; an interval's boundary hour is approximate.
  The memory buffer holds at most 10,000 groups and can discard older pending groups
  during a prolonged database outage.
- One randomly sampled report from 10% of page loads contains numeric LCP, CLS, FCP,
  TTFB and INP values and a known page category. Do Not Track and Global Privacy
  Control disable sampling. Sampling uses no visitor identifier or cookie. The
  pinned Web Vitals library is served locally without contacting Google or a CDN.
- `perf_buckets` stores hourly histograms by page category, rounded to 50 ms or
  0.01 CLS. Percentiles are histogram estimates; a missing measurement is not zero.
  Retention is capped by `RAW_STATS_RETENTION_DAYS` (default 30 days), despite these
  being aggregates rather than raw visitor records.
- `stats_batches` contains server batch acknowledgements for transactional retry
  deduplication, with the same 30-day retention. Batch UUIDs identify server writes;
  they are never assigned to visitors or sent to browsers or Umami.

`POST /api/perf-metrics` accepts only a known page category, numeric metrics and a
version marker. Legacy versions 1 and 2 remain accepted; storage always uses version
3. Unknown fields, including `visit_id`, `report_seq`, `connection_type` and
`device_memory`, are discarded during validation. HTTP 200 does not mean that extra
fields were stored. The current browser collector does not send them.

This profile stores no IP addresses, full User-Agents, referrers, arbitrary URLs or
query strings, visitor identifiers or browser/device attributes. Language selection
uses the independent `preferredLang` localStorage value and one-year functional
cookie. Explicit localized URLs take precedence. Browser timezone detection and
image settings in URLs work independently of telemetry.

## Optional integrations

Umami requires both `UMAMI_ENABLED=true` and `UMAMI_WEBSITE_ID`. It receives closed
hourly server summaries with a fixed server User-Agent and no visitor headers; no
Umami script runs in the browser. Interpret the `usage_summary` event's `count`,
not Umami's visitor count. Failed submissions retry for at most one day in a bounded
buffer. A lost acknowledgement can cause duplicate summaries in Umami; local
statistics have transactional deduplication.

GlitchTip/Sentry requires a configured DSN and enablement. The scrubber retains error
type and code location, removing request data, users, value-bearing messages,
breadcrumbs, local variables and other context. Tracing and profiling are disabled.
Credits indicate whether integrations are configured.

Application/library logs and access logs are disabled; the supplied Compose setup
discards stdout/stderr. Rate limiting uses shared 60-second counters without IP keys,
so one busy client can affect other clients in the same process. S3 backups require
explicit enablement, credentials and retention configuration.

Provider/proxy logs, old backups and snapshots are outside the application's
control. Serving HTTP still involves receiving network addresses and headers.
Reduced collection alone does not establish an exemption from privacy obligations;
each deployment needs its own accurate notices and provider arrangements.

## Upgrading to 1.3.0

Stop the old writing process before upgrading. Startup runs transactional migrations
even when image scheduling is disabled. Old `api_calls` rows become hourly totals;
version 2+ `perf_metrics` become histograms. Original rows and identifiers are removed
using SQLite `secure_delete` and WAL cleanup. Repeating startup does not double-count
migrated requests. This does not erase external copies or guarantee forensic disk
erasure. Sporting data, image caches and weather quota records remain.

Schema 6 labels previously unrecorded calendar artwork choices as `legacy` in both
statistics storage formats, preserving counts and times. New requests record resolved
defaults, including cache hits and HTTP 304s. Accent counts describe the requested
subset before intersection with the display palette. The dashboard and `/api/stats`
use the same categories.

`MINIMAL_DATA_MODE=true` removes aggregates, histograms and batch acknowledgements
as well. Statistics pages and APIs return 410. Changing the flag back cannot recover
deleted statistics. `AGGREGATE_STATS_ONLY=false` retains a legacy compatibility
profile that can store individual records; it is not the recommended public profile.

The shared Open-Meteo quota counts every attempted call, including retries, over one
minute, one hour, one day and rolling 31 days. Timestamps previously expired after
one day, so the monthly counter is partial for the first 31 days after upgrading.
Separate databases or applications using the same provider need a shared budget.

Regenerate old calendar and preview outputs after upgrading. Withdrawn static assets
and obsolete image metadata are rejected. Existing images in proxy/CDN caches,
previous releases and Git history require separate review; deleting current files
does not recall copies already distributed. See [artwork](../TRACK_ARTWORK.md) and
[data licences](../DATA_LICENSES.md) for component-specific source and rights notices.

## Read-only runtime check

Run inside the deployed container:

```bash
python -m app.utils.runtime_audit
```

Or from a checkout:

```bash
uv run --locked python -m app.utils.runtime_audit --database /path/to/f1.db
```

The command reads the configured profile and an existing SQLite database, including
its live WAL. It does not initialize or migrate the database. Output contains row
counts for five known statistics tables and weather attempts over the four quota
windows. Aggregate row counts are neither visitor counts nor total request counts.
No individual rows, identifiers, DSNs, database paths or operator details are printed.

Exit codes are `0` for a matching profile and tables, `1` for unexpected raw records,
missing tables or the legacy profile, and `2` for an unavailable or invalid database.
Minimal mode requires aggregate tables to be empty too. The result describes one
database snapshot, not external logs, backups or the behavior of other processes.
