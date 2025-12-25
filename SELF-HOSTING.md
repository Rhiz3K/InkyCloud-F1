# Self-Hosting Guide

This guide covers everything you need to know about self-hosting the F1 E-Ink Calendar service. If you just want to use the service, visit the public instance at **[f1.inkycloud.click](https://f1.inkycloud.click)**.

## Table of Contents

- [Quick Start](#quick-start)
- [Deployment Options](#deployment-options)
- [Project Structure](#project-structure)
- [Data Files & Updates](#data-files--updates)
- [Yearly Maintenance](#yearly-maintenance)
- [Configuration Reference](#configuration-reference)
- [Database Management](#database-management)
- [Development Setup](#development-setup)
- [Performance & Caching](#performance--caching)
- [Track Images](#track-images)

---

## Quick Start

### Using Coolify (Recommended) 🚀

Deploy in 5 minutes with one-click deployment:

1. **Connect Repository** in Coolify Dashboard
   - Repository: `https://github.com/Rhiz3K/InkyCloud-F1`
   - Branch: `main`

2. **Set Environment Variables**
   ```bash
   APP_HOST=0.0.0.0
   APP_PORT=8000
   DEBUG=false
   ```

3. **Click Deploy** → Done! 🎉

For detailed guide with custom domains, SSL, scaling, and monitoring, see **[COOLIFY.md](./COOLIFY.md)**.

### Using Docker

```bash
# Clone the repository
git clone https://github.com/Rhiz3K/InkyCloud-F1.git
cd InkyCloud-F1

# Copy environment file
cp .env.example .env

# Build and run with Docker
docker build -t f1-eink-cal .
docker run -p 8000:8000 --env-file .env f1-eink-cal
```

### Using Docker Compose

```bash
# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### Local Development

```bash
# Install dependencies
pip install -e .

# Set up local environment
cp .env.local.example .env
# Edit .env if needed - it uses relative paths for local development

# Run the server
python -m app.main

# Or with uvicorn
uvicorn app.main:app --reload
```

**Note**: Default paths in `config.py` are optimized for Docker containers (`/app/data/*`). The `.env.local.example` file provides relative paths for local development. Copy it to `.env` and modify as needed.

For more deployment options (Heroku, Railway, Render, DigitalOcean, systemd), see **[DEPLOYMENT.md](./DEPLOYMENT.md)**.

---

## Deployment Options

| Option | Complexity | Cost | Best For |
|--------|------------|------|----------|
| **[Coolify](./COOLIFY.md)** | ⭐ Easy | €5-10/mo | Self-hosters wanting Heroku-like experience |
| **Docker** | ⭐⭐ Medium | Varies | Existing Docker infrastructure |
| **[Cloud Platforms](./DEPLOYMENT.md)** | ⭐ Easy | $7-25/mo | Quick deployment without infrastructure |
| **Manual** | ⭐⭐⭐ Advanced | €3-5/mo | Full control, minimal cost |

---

## Project Structure

```
InkyCloud-F1/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── config.py            # Configuration management
│   ├── models.py            # Pydantic models
│   ├── assets/
│   │   ├── fonts/           # Custom fonts (TitilliumWeb)
│   │   ├── images/          # Static images (F1 logo)
│   │   ├── tracks/          # Circuit track images
│   │   ├── seasons/         # Static season calendars (2025.json, 2026.json)
│   │   └── circuits_data.json  # Circuit info + historical results
│   └── services/
│       ├── __init__.py
│       ├── f1_service.py    # F1 data service (static + API fallback)
│       ├── renderer.py      # BMP image rendering
│       ├── scheduler.py     # Background jobs
│       ├── database.py      # SQLite cache
│       ├── analytics.py     # Umami analytics
│       └── i18n.py          # Translation service
├── scripts/
│   ├── update_seasons.py    # Download season calendars from API
│   └── update_historical.py # Update historical race results
├── translations/
│   ├── en.json              # English translations
│   └── cs.json              # Czech translations
├── .github/workflows/
│   └── update-f1-data.yml   # Weekly auto-update action
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `app/main.py` | FastAPI endpoints with async/await pattern |
| `app/services/f1_service.py` | F1 data fetching with timezone conversion |
| `app/services/renderer.py` | Pixel-perfect 1-bit BMP rendering engine |
| `app/services/i18n.py` | Translation loader with caching |
| `app/services/analytics.py` | Fire-and-forget Umami tracking |

---

## Data Files & Updates

The application uses **static JSON files** for F1 data instead of making API calls at runtime. This eliminates rate limiting issues and enables offline operation.

### Data Files

| File | Description | Update Frequency |
|------|-------------|------------------|
| `app/assets/seasons/2025.json` | 2025 race calendar | Once per year (or when FIA changes) |
| `app/assets/seasons/2026.json` | 2026 race calendar | Once per year |
| `app/assets/circuits_data.json` | Circuit info + historical results | After each GP |

### Automatic Updates (GitHub Action)

A GitHub Action runs every **Monday at 06:00 UTC** (after Sunday GP) to automatically update historical race results. Changes are committed directly to the repository.

You can also trigger updates manually from the GitHub Actions tab:
- **historical** - Update race results after each GP
- **seasons** - Update season calendars (use when FIA announces changes)
- **all** - Update both

### Manual Updates

```bash
# Update historical results (after each Grand Prix)
python scripts/update_historical.py

# Update specific circuit only
python scripts/update_historical.py --circuit albert_park

# Update season calendars (when FIA changes schedule)
python scripts/update_seasons.py

# Update specific years
python scripts/update_seasons.py --years 2025,2026
```

---

## Yearly Maintenance

### Before Each Season (January/February)

1. **Update season calendar** when FIA announces the official schedule:
   ```bash
   python scripts/update_seasons.py --years 2026
   ```

2. **Add new circuits** if any are introduced:
   - Add circuit data to `app/assets/circuits_data.json`
   - Add track image to `app/assets/tracks/{circuitId}.png`

3. **Update dependencies** for security:
   ```bash
   pip install -U -e ".[dev]"
   ```

4. **Test rendering** with the new season data:
   ```bash
   pytest tests/
   curl "http://localhost:8000/calendar.bmp?year=2026&round=1" -o test.bmp
   ```

### After Each Grand Prix (Automatic)

The GitHub Action automatically updates historical results every Monday. If you need to trigger manually:

```bash
# From GitHub Actions tab
# Or locally:
python scripts/update_historical.py
```

### Mid-Season Tasks

- **If FIA changes schedule**: Update season calendar
- **If circuits are added/removed**: Update circuits data
- **Monitor error logs**: Check GlitchTip/Sentry for issues
- **Review analytics**: Check Umami for usage patterns

### End of Season (December)

1. **Create next year's calendar file** (placeholder until FIA announces):
   ```bash
   python scripts/update_seasons.py --years 2027
   ```

2. **Review and update dependencies**

3. **Archive old data** if needed (optional - data is useful for historical display)

---

## Configuration Reference

Create a `.env` file based on `.env.example`:

```bash
# Application Configuration
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=false

# Sentry/GlitchTip Configuration
SENTRY_DSN=your-sentry-dsn-here
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1

# Umami Analytics Configuration
UMAMI_WEBSITE_ID=your-website-id
UMAMI_API_URL=https://analytics.example.com/api/send
UMAMI_ENABLED=true

# API Configuration
JOLPICA_API_URL=https://api.jolpi.ca/ergast/f1/current/next.json
REQUEST_TIMEOUT=10

# Default Language
DEFAULT_LANG=en

# Default Timezone (IANA format)
DEFAULT_TIMEZONE=Europe/Prague

# Database and Storage Configuration
# Use absolute paths for containers (default: /app/data)
# For local development, you can use relative paths (e.g., ./data/f1.db)
DATABASE_PATH=/app/data/f1.db
IMAGES_PATH=/app/data/images

# Scheduler Configuration
SCHEDULER_ENABLED=true
```

### Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` / `APP_PORT` / `PORT` | `0.0.0.0:8000` | Bind address and port |
| `DEBUG` | `false` | Enable verbose logging |
| `PYTHONUNBUFFERED` / `PYTHONDONTWRITEBYTECODE` | - | Container-friendly Python flags |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` | - | GlitchTip/Sentry monitoring |
| `UMAMI_WEBSITE_ID`, `UMAMI_API_URL`, `UMAMI_ENABLED` | - | Umami analytics tracking |
| `JOLPICA_API_URL`, `REQUEST_TIMEOUT` | - | Upstream F1 data endpoint |
| `DEFAULT_LANG` | `en` | Default calendar language |
| `DEFAULT_TIMEZONE` | `Europe/Prague` | IANA timezone for schedule |
| `DATABASE_PATH` | `/app/data/f1.db` | SQLite database location (absolute path for containers) |
| `IMAGES_PATH` | `/app/data/images` | Generated preview images (absolute path for containers) |
| `SCHEDULER_ENABLED` | `true` | Background data refresh |
| `BACKUP_ENABLED` | `false` | Enable S3 database backup |
| `BACKUP_CRON` | `0 3 * * *` | Backup schedule (cron expression) |
| `BACKUP_RETENTION_DAYS` | `30` | Days to keep old backups (0 = keep all) |
| `S3_ENDPOINT_URL` | - | S3-compatible endpoint URL |
| `S3_ACCESS_KEY_ID` | - | S3 access key |
| `S3_SECRET_ACCESS_KEY` | - | S3 secret key |
| `S3_BUCKET_NAME` | - | S3 bucket for backups |
| `S3_REGION` | `auto` | S3 region (use "auto" for Cloudflare R2) |

---

## S3 Database Backup

The application supports automatic backups of the SQLite database to any S3-compatible storage provider (Cloudflare R2, AWS S3, MinIO, Backblaze B2, etc.).

### Setting Up Cloudflare R2 (Recommended)

Cloudflare R2 offers generous free tier (10GB storage, 1M requests/month) and no egress fees.

1. **Create an R2 bucket** in your Cloudflare dashboard:
   - Go to R2 → Create bucket
   - Name it (e.g., `f1-eink-backups`)
   - Note your account ID from the bucket URL

2. **Create R2 API token**:
   - Go to R2 → Manage R2 API Tokens → Create API token
   - Select "Object Read & Write" permission
   - Scope to your backup bucket
   - Save the Access Key ID and Secret Access Key

3. **Configure environment variables**:
   ```bash
   BACKUP_ENABLED=true
   BACKUP_CRON=0 3 * * *
   BACKUP_RETENTION_DAYS=30
   
   S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   S3_ACCESS_KEY_ID=<your-access-key-id>
   S3_SECRET_ACCESS_KEY=<your-secret-access-key>
   S3_BUCKET_NAME=f1-eink-backups
   S3_REGION=auto
   ```

### Setting Up AWS S3

```bash
BACKUP_ENABLED=true
BACKUP_CRON=0 3 * * *
BACKUP_RETENTION_DAYS=30

S3_ENDPOINT_URL=https://s3.us-east-1.amazonaws.com
S3_ACCESS_KEY_ID=<your-access-key-id>
S3_SECRET_ACCESS_KEY=<your-secret-access-key>
S3_BUCKET_NAME=f1-eink-backups
S3_REGION=us-east-1
```

### Setting Up MinIO (Self-Hosted)

```bash
BACKUP_ENABLED=true
BACKUP_CRON=0 3 * * *
BACKUP_RETENTION_DAYS=30

S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET_NAME=f1-backups
S3_REGION=us-east-1
```

### Backup Schedule

The `BACKUP_CRON` variable uses standard cron syntax:

| Expression | Description |
|------------|-------------|
| `0 3 * * *` | Daily at 3:00 AM UTC (default) |
| `0 */6 * * *` | Every 6 hours |
| `0 3 * * 0` | Weekly on Sundays at 3:00 AM |
| `0 3 1 * *` | Monthly on the 1st at 3:00 AM |

### Backup Files

Backups are stored with the naming pattern: `f1_backup_YYYY-MM-DD_HH-MM-SS.db`

Example: `f1_backup_2025-03-15_03-00-00.db`

### Restoring from Backup

1. Download the backup file from your S3 bucket
2. Stop the container
3. Replace the database file (default: `/app/data/f1.db`)
4. Start the container

```bash
# Example with Docker
docker cp f1_backup_2025-03-15.db container_name:/app/data/f1.db
docker restart container_name
```

### Backup CLI Commands

The container includes a `backup` CLI tool for testing and manual operations:

```bash
# Show backup configuration (without sensitive data)
docker exec <container> backup info

# Test S3 connection and permissions
docker exec <container> backup test

# Perform backup immediately
docker exec <container> backup now
```

#### Docker Compose Usage

```bash
# Show configuration
docker compose exec f1-eink-cal backup info

# Test connection
docker compose exec f1-eink-cal backup test

# Manual backup
docker compose exec f1-eink-cal backup now
```

#### Command Details

| Command | Description |
|---------|-------------|
| `backup info` | Shows endpoint, bucket, region, schedule, retention (no secrets) |
| `backup test` | Tests credentials, bucket access, write permissions, shows latency and existing backups |
| `backup now` | Performs immediate backup + retention cleanup, shows upload progress |

#### Example Output

**`backup info`:**
```
S3 Backup Configuration
========================================
  Enabled:      True
  Endpoint:     https://xxx.r2.cloudflarestorage.com
  Bucket:       f1-eink-backups
  Region:       auto
  Schedule:     0 3 * * *
  Retention:    30 days
  Credentials:  configured
```

**`backup test`:**
```
Testing S3 connection...

  [OK] Credentials valid
  [OK] Bucket accessible
  [OK] Write permission confirmed

Connection latency: 45.2 ms

Bucket statistics:
  Existing backups: 12
  Total size:       1.2 MB
  Oldest backup:    2025-11-25_03-00-00
  Newest backup:    2025-12-24_03-00-00

Connection test PASSED
```

**`backup now`:**
```
Starting manual backup...

  [OK] Database copied: 156.0 KB
  [OK] Uploaded: f1_backup_2025-12-25_14-30-00.db
  [OK] Cleanup: 2 old backup(s) deleted

Backup completed successfully.
```

---

## Database Management

The application includes a `reset-db` command for managing the SQLite database in Docker containers.

### Available Commands

```bash
# Show database info and record counts (no changes)
docker exec <container> reset-db info

# Reset statistics only (api_calls, request_stats)
docker exec <container> reset-db stats

# Reset cache only (cache_meta, generated_images, BMP files)
docker exec <container> reset-db cache

# Delete entire database (will be recreated on next request)
docker exec <container> reset-db all
```

### Docker Compose Usage

```bash
# Show database info
docker compose exec f1-eink-cal reset-db info

# Reset statistics
docker compose exec f1-eink-cal reset-db stats
```

### What Each Command Does

| Command | Tables Affected | Also Deletes |
|---------|-----------------|--------------|
| `info` | None (read-only) | Nothing |
| `stats` | `api_calls`, `request_stats` | Nothing |
| `cache` | `cache_meta`, `generated_images` | BMP files in IMAGES_PATH |
| `all` | Entire database file | BMP files in IMAGES_PATH |

### Notes

- All destructive commands require confirmation (`[y/N]` prompt)
- The database is automatically recreated on the next request after deletion
- Use `stats` to clear analytics data while preserving cached images
- Use `cache` to force regeneration of all BMP images

---

## Development Setup

### Install Development Dependencies

```bash
pip install -e ".[dev]"
```

### Code Formatting

```bash
ruff check .
ruff format .
```

### Run Tests

```bash
pytest
```

### Preprocess Flag Assets

The flag preprocessing script requires optional dependencies:

```bash
pip install -e .[dev]
python scripts/preprocess_flags.py
```

---

## Performance & Caching

The application uses a multi-tier caching strategy optimized for E-Ink displays that typically refresh every few hours.

### Benchmark Results

Run the benchmark script to measure performance on your hardware:

```bash
python scripts/benchmark_renderer.py
```

**Typical results on a 4-core VPS:**

| Method | Avg Time | Throughput | Use Case |
|--------|----------|------------|----------|
| In-memory cache | ~0.0003 ms | ~3,000,000 req/s | Repeated requests within same process |
| Pre-generated file | ~0.04 ms | ~25,000 req/s | Popular language/timezone combinations |
| On-the-fly render | ~50 ms | ~20 req/s | Specific race or uncommon timezone |
| HTTP endpoint | ~55 ms | ~18 req/s | Full request cycle including overhead |

**Memory usage:** Each rendered BMP is ~47 KB (800×480 1-bit).

### Caching Architecture

```
Request → In-Memory Cache → Pre-generated File → On-the-fly Render
              ↓                    ↓                     ↓
         (instant)            (~0.04ms)              (~50ms)
```

1. **In-memory LRU cache** - Stores recently served BMPs in memory
2. **Pre-generated files** - Popular variants saved to disk by scheduler
3. **On-the-fly rendering** - Fallback for specific races or rare timezones

### Dynamic Pre-generation

The scheduler runs hourly and intelligently pre-generates BMP files based on actual usage patterns:

**Always generated (defaults):**
- `calendar_en.bmp` - English, next race, default timezone
- `calendar_cs.bmp` - Czech, next race, default timezone

**Dynamically generated (based on popularity):**
- Up to 20 additional variants based on the most popular `(language, timezone)` combinations from the last 24 hours
- Example: `calendar_en_America_New_York.bmp`, `calendar_cs_Europe_London.bmp`

**Selection criteria:**
- Queries `api_calls` table for combinations with >10 requests in last 24h
- Excludes default timezone (already covered by base files)
- Limits to 20 variants to control disk usage

### File Naming Convention

Pre-generated files follow this pattern:

```
calendar_{lang}.bmp                    # Default timezone
calendar_{lang}_{tz_safe}.bmp          # Specific timezone
```

Where `{tz_safe}` replaces `/` with `_` in timezone names:
- `America/New_York` → `America_New_York`
- `Europe/London` → `Europe_London`

### Endpoint Behavior

The `/calendar.bmp` endpoint checks for pre-generated files before rendering:

1. **Next race requests** (no `year`/`round` params):
   - First checks for `calendar_{lang}_{tz_safe}.bmp`
   - Falls back to `calendar_{lang}.bmp` if using default timezone
   - Renders on-the-fly if no pre-generated file exists

2. **Specific race requests** (`year` and `round` params):
   - Always renders on-the-fly (historical data not pre-generated)

### Benchmark CLI Options

```bash
# Basic benchmark (excludes HTTP test)
python scripts/benchmark_renderer.py

# Include HTTP endpoint test
python scripts/benchmark_renderer.py --http

# Custom number of iterations
python scripts/benchmark_renderer.py --runs 200

# Export results to JSON
python scripts/benchmark_renderer.py --json

# Verbose output with individual run times
python scripts/benchmark_renderer.py -v
```

---

## Track Images

The renderer automatically loads circuit track images from `app/assets/tracks/`. Images are matched by `circuitId` from the Jolpica API.

### Naming Convention

Name your track images using the `circuitId`:

```
{circuitId}.png
```

### All Circuit IDs (2000-2026)

| circuitId | Circuit | Location |
|-----------|---------|----------|
| `albert_park` | Albert Park Grand Prix Circuit | Melbourne, Australia |
| `americas` | Circuit of the Americas | Austin, USA |
| `bahrain` | Bahrain International Circuit | Sakhir, Bahrain |
| `baku` | Baku City Circuit | Baku, Azerbaijan |
| `buddh` | Buddh International Circuit | Uttar Pradesh, India |
| `catalunya` | Circuit de Barcelona-Catalunya | Barcelona, Spain |
| `fuji` | Fuji Speedway | Oyama, Japan |
| `hockenheimring` | Hockenheimring | Hockenheim, Germany |
| `hungaroring` | Hungaroring | Budapest, Hungary |
| `imola` | Autodromo Enzo e Dino Ferrari | Imola, Italy |
| `indianapolis` | Indianapolis Motor Speedway | Indianapolis, USA |
| `interlagos` | Autódromo José Carlos Pace | São Paulo, Brazil |
| `istanbul` | Istanbul Park | Istanbul, Turkey |
| `jeddah` | Jeddah Corniche Circuit | Jeddah, Saudi Arabia |
| `losail` | Losail International Circuit | Lusail, Qatar |
| `madring` | Madring | Madrid, Spain |
| `magny_cours` | Circuit de Nevers Magny-Cours | Magny Cours, France |
| `marina_bay` | Marina Bay Street Circuit | Marina Bay, Singapore |
| `miami` | Miami International Autodrome | Miami, USA |
| `monaco` | Circuit de Monaco | Monte Carlo, Monaco |
| `monza` | Autodromo Nazionale di Monza | Monza, Italy |
| `mugello` | Autodromo Internazionale del Mugello | Mugello, Italy |
| `nurburgring` | Nürburgring | Nürburg, Germany |
| `portimao` | Autódromo Internacional do Algarve | Portimão, Portugal |
| `red_bull_ring` | Red Bull Ring | Spielberg, Austria |
| `ricard` | Circuit Paul Ricard | Le Castellet, France |
| `rodriguez` | Autódromo Hermanos Rodríguez | Mexico City, Mexico |
| `sepang` | Sepang International Circuit | Kuala Lumpur, Malaysia |
| `shanghai` | Shanghai International Circuit | Shanghai, China |
| `silverstone` | Silverstone Circuit | Silverstone, UK |
| `sochi` | Sochi Autodrom | Sochi, Russia |
| `spa` | Circuit de Spa-Francorchamps | Spa, Belgium |
| `suzuka` | Suzuka Circuit | Suzuka, Japan |
| `valencia` | Valencia Street Circuit | Valencia, Spain |
| `vegas` | Las Vegas Strip Street Circuit | Las Vegas, USA |
| `villeneuve` | Circuit Gilles Villeneuve | Montreal, Canada |
| `yas_marina` | Yas Marina Circuit | Abu Dhabi, UAE |
| `yeongam` | Korean International Circuit | Yeongam County, Korea |
| `zandvoort` | Circuit Park Zandvoort | Zandvoort, Netherlands |

**Note:** If no matching track image is found, the renderer uses a stylized placeholder.

---

## Tech Stack

- **Python 3.11**: Modern Python with type hints
- **FastAPI**: High-performance web framework
- **Pillow**: Image generation and manipulation
- **HTTPX**: Async HTTP client for API calls
- **Sentry-SDK**: Error tracking and monitoring
- **pytz**: Timezone handling

---

## Getting Help

- **Issues**: [GitHub Issues](https://github.com/Rhiz3K/InkyCloud-F1/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Rhiz3K/InkyCloud-F1/discussions)
- **Coolify Guide**: [COOLIFY.md](./COOLIFY.md)
- **Deployment Guide**: [DEPLOYMENT.md](./DEPLOYMENT.md)
- **Contributing**: [CONTRIBUTING.md](./CONTRIBUTING.md)
