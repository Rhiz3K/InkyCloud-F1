# Coolify Deployment Guide

Complete guide for deploying F1 E-Ink Calendar on [Coolify](https://coolify.io/) - a self-hosted, open-source Platform as a Service (PaaS).

## Table of Contents

- [Why Coolify?](#why-coolify)
- [Prerequisites](#prerequisites)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
- [Detailed Setup](#detailed-setup)
- [Environment Variables](#environment-variables)
- [Custom Domain & SSL](#custom-domain--ssl)
- [Scaling & Performance](#scaling--performance)
- [Monitoring & Logs](#monitoring--logs)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)

---

## Why Coolify?

Coolify is perfect for self-hosting F1 E-Ink Calendar:

- ✅ **Self-hosted** - Full control over your infrastructure
- ✅ **Auto-deploy** - Push to GitHub, auto-deploy to production
- ✅ **Free SSL** - Automatic Let's Encrypt certificates
- ✅ **Built-in Monitoring** - Logs, metrics, health checks
- ✅ **Easy Scaling** - Horizontal scaling with one click
- ✅ **No Vendor Lock-in** - Run on any VPS (DigitalOcean, Hetzner, AWS, etc.)
- ✅ **Cost-effective** - €5-10/month VPS can handle significant traffic

---

## Prerequisites

### 1. Coolify Instance
You need a running Coolify instance. Choose one:

**Option A: Self-hosted Coolify**
- VPS with 2GB+ RAM (recommended: 4GB)
- Ubuntu 22.04 or Debian 11+
- Docker installed
- Follow: https://coolify.io/docs/installation

**Option B: Coolify Cloud** (Coming Soon)
- Managed Coolify hosting
- Visit: https://app.coolify.io

### 2. GitHub Repository Access
- Fork or have access to `Rhiz3K/InkyCloud-F1`
- GitHub account connected to Coolify

### 3. (Optional) Custom Domain
- Domain name for custom URL
- DNS access for configuration

---

## Quick Start (5 Minutes)

### Step 1: Create New Resource

1. Log into your Coolify dashboard
2. Click **"+ New"** → **"Public Repository"**
3. Enter repository details:
   ```
   Repository URL: https://github.com/Rhiz3K/InkyCloud-F1
   Branch: main
   ```
4. Click **"Continue"**

### Step 2: Configure Build

Coolify auto-detects the Dockerfile. Verify settings:

```
Build Type: Dockerfile
Dockerfile Location: ./Dockerfile
Port: 8000
Health Check Path: /health
```

### Step 3: Set Environment Variables

Click **"Environment Variables"** and add:

**Required (Minimal Setup)**:
```bash
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=false
```

**Optional (Recommended for Production)**:
```bash
SENTRY_DSN=your-sentry-dsn-here
SENTRY_ENVIRONMENT=production
UMAMI_ENABLED=true
UMAMI_WEBSITE_ID=your-website-id
UMAMI_API_URL=https://your-analytics-domain.com/api/send
```

### Step 4: Deploy

1. Click **"Deploy"**
2. Wait 2-3 minutes for build to complete
3. Access your app at the provided URL (e.g., `https://f1-eink-cal.your-coolify-domain.com`)

**Done! 🎉** Your F1 calendar is now live.

---

## Detailed Setup

### Build Configuration

#### Dockerfile Settings
Coolify automatically detects `Dockerfile` in the repository root. Our multi-stage build:

- **Stage 1 (Builder)**: Compiles dependencies
- **Stage 2 (Runtime)**: Minimal production image (~250MB)

Key features:
- Python 3.12-slim base image
- Non-root user (security)
- Built-in health check
- Optimized layer caching

#### Build Process
```
1. Clone repository
2. Build Stage 1: Install dependencies
3. Build Stage 2: Copy artifacts, add app code
4. Tag image
5. Start container
6. Health check (wait max 40s)
7. Mark as healthy ✅
```

Average build time: **2-3 minutes**

---

## Environment Variables

### Required Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` | `0.0.0.0` | Bind address (always 0.0.0.0 in containers) |
| `APP_PORT` | `8000` | Application port |
| `DEBUG` | `false` | Debug mode (use `false` in production) |

### Optional - Monitoring

#### Sentry/GlitchTip (Error Tracking)

| Variable | Example | Description |
|----------|---------|-------------|
| `SENTRY_DSN` | `https://...@sentry.io/123` | Sentry/GlitchTip DSN |
| `SENTRY_ENVIRONMENT` | `production` | Environment name |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | 10% of requests traced |

**Setup GlitchTip** (Self-hosted Sentry alternative):
1. Deploy GlitchTip on Coolify (one-click template available)
2. Create project
3. Copy DSN to `SENTRY_DSN`

#### Umami Analytics

| Variable | Example | Description |
|----------|---------|-------------|
| `UMAMI_ENABLED` | `true` | Enable/disable analytics |
| `UMAMI_WEBSITE_ID` | `abc123...` | Your website ID from Umami |
| `UMAMI_API_URL` | `https://analytics.yourdomain.com/api/send` | Umami API endpoint |

**Setup Umami**:
1. Deploy Umami on Coolify (template available)
2. Create website
3. Copy Website ID and API URL

### Optional - API Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JOLPICA_API_URL` | `https://api.jolpi.ca/ergast/f1/current/next.json` | F1 data API |
| `REQUEST_TIMEOUT` | `10` | API timeout (seconds) |
| `DEFAULT_LANG` | `en` | Default language (en/cs) |

### Container-Specific Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYTHONUNBUFFERED` | `1` | Immediate log output |
| `PYTHONDONTWRITEBYTECODE` | `1` | No .pyc files |
| `PORT` | `8000` | Port (Coolify may override) |

---

## Custom Domain & SSL

### Add Custom Domain

1. In Coolify, go to your deployment
2. Navigate to **"Domains"** tab
3. Click **"+ Add Domain"**
4. Enter your domain: `f1.yourdomain.com`

### DNS Configuration

Point your domain to Coolify server:

```dns
Type: A
Name: f1 (or @ for root domain)
Value: [Your Coolify server IP]
TTL: 300
```

Example for Cloudflare:
```
f1.yourdomain.com  A  1.2.3.4
```

### SSL Certificate

Coolify automatically provisions Let's Encrypt SSL:

1. After DNS propagates (5-10 minutes)
2. Coolify detects domain
3. Requests SSL certificate
4. Configures HTTPS redirect
5. **Done!** Access via `https://f1.yourdomain.com` 🔒

**SSL Renewal**: Automatic (every 60 days)

---

## Scaling & Performance

### Horizontal Scaling

Scale to multiple instances for high availability:

1. Go to deployment settings
2. **"Resources"** → **"Replicas"**
3. Set desired count (e.g., 3)
4. Click **"Update"**

Coolify automatically:
- Starts multiple containers
- Load balances traffic
- Health checks all instances
- Removes unhealthy containers

**Recommended setup**:
- **Low traffic** (<1000 req/day): 1 replica
- **Medium traffic** (1k-10k req/day): 2 replicas
- **High traffic** (>10k req/day): 3+ replicas

### Resource Limits

Set CPU and memory limits:

```yaml
Resources per replica:
  CPU: 0.5 cores (default)
  Memory: 512MB (recommended: 1GB)
```

Our application is lightweight:
- Idle: ~100MB RAM
- Under load: ~200-300MB RAM
- CPU: Mostly idle, spikes during image generation

### Caching

Add Redis for caching (optional):

1. Deploy Redis via Coolify (one-click)
2. Add environment variable:
   ```
   REDIS_URL=redis://redis:6379
   ```
3. Modify app to cache race data (1 hour TTL)

**Benefits**: Reduces API calls, faster response times

---

## Monitoring & Logs

### Real-time Logs

View logs in Coolify dashboard:

1. Go to your deployment
2. Click **"Logs"** tab
3. Toggle **"Follow"** for real-time

**Log levels**:
- `INFO`: Normal operations (startup, requests)
- `WARNING`: Non-critical issues
- `ERROR`: Failures (API errors, render errors)

### Health Checks

Coolify monitors `/health` endpoint:

```bash
# Health check runs every 30s
curl https://your-app.com/health
# Expected: {"status":"healthy"}
```

**Health check settings** (configured in Dockerfile):
- Interval: 30 seconds
- Timeout: 10 seconds
- Retries: 3
- Start period: 40 seconds (warmup time)

**Unhealthy container actions**:
1. Coolify detects failure after 3 retries
2. Restarts container automatically
3. Sends notification (if configured)

### Metrics

Monitor key metrics in Coolify:

- **Response time**: /calendar.bmp generation time
- **Error rate**: Failed requests percentage
- **Uptime**: Service availability
- **Resource usage**: CPU, Memory, Network

### Alerts

Configure alerts in Coolify settings:

```yaml
Alert on:
  - Health check failure
  - High error rate (>5%)
  - High memory usage (>80%)
  - Container restart

Notification channels:
  - Email
  - Discord webhook
  - Slack webhook
  - Telegram
```

---

## Troubleshooting

### Common Issues

#### 1. Build Fails

**Error**: `Python 3.14 not found`
- **Cause**: Old Dockerfile (before this PR)
- **Fix**: Ensure you're on `main` branch with latest changes

**Error**: `pip install failed`
- **Cause**: Network issue or dependency conflict
- **Fix**: Check Coolify logs, retry build

**Error**: `COPY failed: file not found`
- **Cause**: Missing `setup.py` or `MANIFEST.in`
- **Fix**: Ensure latest code from repository

#### 2. Health Check Fails

**Symptom**: Container starts but marked unhealthy

**Debug steps**:
```bash
# 1. Check if app is running
curl http://your-app:8000/

# 2. Test health endpoint
curl http://your-app:8000/health

# 3. Check logs for errors
# (in Coolify logs tab)
```

**Common causes**:
- Port mismatch (ensure `PORT=8000`)
- App crashed during startup
- Dependencies missing

#### 3. Environment Variables Not Working

**Symptom**: App uses default values instead of your settings

**Fix**:
1. Go to **Environment Variables** in Coolify
2. Verify variables are set correctly
3. Check for typos in variable names
4. **Redeploy** after changing env vars (variables only load on start)

#### 4. Cannot Access Application

**Symptom**: 502 Bad Gateway or Connection Refused

**Checks**:
- [ ] Container is running (check Coolify status)
- [ ] Health check is passing
- [ ] Port 8000 is exposed
- [ ] Firewall allows traffic (usually automatic in Coolify)

#### 5. Slow Image Generation

**Symptom**: `/calendar.bmp` takes >5 seconds

**Solutions**:
- Increase container resources (CPU/Memory)
- Add Redis caching for race data
- Check `JOLPICA_API_URL` response time
- Reduce `REQUEST_TIMEOUT` if API is slow

#### 6. SSL Certificate Issues

**Symptom**: "Not Secure" warning in browser

**Checks**:
- DNS is correctly configured (A record points to server)
- Domain has propagated (check with `dig yourdomain.com`)
- Coolify SSL provisioning completed (check domain tab)

**Force SSL renewal**:
1. Domain settings → SSL
2. Click "Request Certificate"

#### 7. Statistics Lost on Deployment

**Symptom**: API call statistics and database are reset after each deployment

**Cause**: Persistent volume not configured correctly or mounted to wrong path

**Fix**:
1. **Verify volume mount path**:
   - Go to Coolify → Your App → **"Storages"**
   - Ensure "Destination" is `/app/data` (NOT `/usr/src/app/data`)
   
2. **Check if volume exists**:
   ```bash
   # SSH into container or use Coolify console
   ls -la /app/data/
   # Should show: f1.db, f1.db-wal, f1.db-shm
   ```

3. **Verify environment variables**:
   - `DATABASE_PATH` should be `/app/data/f1.db` (or not set - it's the default)
   - Avoid relative paths like `./data/f1.db` in containers

4. **If volume is missing**, add it:
   - Storages → Add Volume
   - Source: `f1-data` (named volume)
   - Destination: `/app/data`
   - Redeploy

5. **Check permissions**:
   ```bash
   ls -ln /app/data/
   # Files should be owned by UID 1000 (appuser)
   ```

See **[Persistent Storage](#persistent-storage-sqlite--images)** section for detailed setup.

---

## Advanced Topics

### Auto-Deploy from GitHub

Enable automatic deployments on every push:

1. Coolify → Your App → **"Settings"**
2. **"Git"** section
3. Enable **"Auto Deploy on Push"**
4. Copy the webhook URL
5. Go to GitHub → Repository Settings → Webhooks
6. Add webhook:
   ```
   Payload URL: [Coolify webhook URL]
   Content type: application/json
   Events: Just the push event
   ```

Now every push to `main` triggers deployment! 🚀

### Multi-Environment Setup

Run staging and production separately:

**Production**:
```
Branch: main
Domain: f1.yourdomain.com
Env: SENTRY_ENVIRONMENT=production
```

**Staging**:
```
Branch: develop
Domain: staging-f1.yourdomain.com
Env: SENTRY_ENVIRONMENT=staging
```

### Persistent Storage (SQLite + Images)

This app uses SQLite for caching and stores pre-generated images. **IMPORTANT: You must configure persistent storage in Coolify, or your statistics and cache will be lost on every deployment!**

**Configure persistent storage in Coolify:**

1. Go to **"Storages"** in your deployment
2. Add volume:
   ```
   Source (Host Path): f1-data (or use a named volume)
   Destination (Mount Path): /app/data
   ```
   ⚠️ **CRITICAL**: The mount path MUST be `/app/data` (matching the Dockerfile WORKDIR).
   Common mistake: Using `/usr/src/app/data` will NOT work!

**Environment variables:**
```bash
DATABASE_PATH=/app/data/f1.db
IMAGES_PATH=/app/data/images
```
These are now the default values, so you don't need to set them explicitly unless you want a different path.

**Files stored:**
- `f1.db` - SQLite database (API call statistics, cache metadata)
- `f1.db-wal`, `f1.db-shm` - SQLite WAL files (Write-Ahead Logging)
- `images/calendar_*.bmp` - Pre-generated calendar images

**Backup:** 
- Optional - data is cache only, regenerated hourly
- For backup, copy `/app/data/` volume content

**Troubleshooting Persistent Storage:**

If your statistics are being lost on deployment:

1. **Check mount path**: Must be `/app/data`, NOT `/usr/src/app/data`
   - Go to Coolify → Your App → **"Storages"**
   - Verify "Destination" is exactly `/app/data`

2. **Verify volume is mounted**: 
   ```bash
   # In Coolify console or SSH into container
   ls -la /app/data/
   # Should show f1.db and f1.db-wal files
   ```

3. **Check permissions**:
   ```bash
   # Files should be owned by appuser (UID 1000)
   ls -ln /app/data/
   # Should show: -rw-r--r-- 1 1000 1000 ...
   ```

4. **Test database access**:
   ```bash
   # From container console
   python -c "from app.services.database import Database; import asyncio; asyncio.run(Database().get_api_calls_stats_24h())"
   ```

5. **Verify environment variables**:
   - Check that DATABASE_PATH=/app/data/f1.db (or not set, as this is the default)
   - Relative paths like `./data/f1.db` should be avoided in containers

### Custom Dockerfile (Advanced)

If you need to modify the Dockerfile:

1. Fork the repository
2. Edit `Dockerfile`
3. Point Coolify to your fork
4. Rebuild

**Example modifications**:
- Add custom fonts
- Include additional Python packages
- Change base image

### Resource Optimization

**Reduce image size further**:
```dockerfile
# Use alpine instead of slim
FROM python:3.12-alpine

# Results in ~150MB image (vs current ~250MB)
```

**Trade-off**: Alpine may have compatibility issues with some packages

### ARM64 Support

Current Dockerfile supports ARM64 (e.g., Raspberry Pi, AWS Graviton):

```bash
# Build on ARM64
docker buildx build --platform linux/arm64 -t f1-eink-cal .
```

Coolify auto-detects architecture.

### Reverse Proxy (Traefik)

Coolify uses Traefik internally. Custom configuration:

```yaml
# In Coolify settings
labels:
  traefik.http.middlewares.ratelimit.ratelimit.average: 100
  traefik.http.middlewares.ratelimit.ratelimit.burst: 50
```

**Adds rate limiting**: 100 req/s average, 50 burst

---

## Performance Benchmarks

Tested on basic VPS (2 vCPU, 2GB RAM):

| Metric | Value |
|--------|-------|
| Cold start time | 10-15s |
| Health check response | <50ms |
| `/calendar.bmp` generation | 200-500ms |
| Memory usage (idle) | ~100MB |
| Memory usage (load) | ~200MB |
| Max throughput | ~50 req/s |
| Image size | ~250MB |

**Recommended VPS specs**:
- **Minimum**: 1 vCPU, 1GB RAM (€5/mo)
- **Recommended**: 2 vCPU, 2GB RAM (€10/mo)
- **High traffic**: 4 vCPU, 4GB RAM (€20/mo)

---

## Cost Estimate

**Self-hosted Coolify** (one-time setup):

| Component | Cost | Notes |
|-----------|------|-------|
| VPS (Coolify) | €10-20/mo | Hetzner, DigitalOcean, etc. |
| Domain | €10/year | Optional (can use Coolify subdomain) |
| SSL | Free | Let's Encrypt |
| **Total** | **€10-20/mo** | Can host multiple apps on same VPS |

**vs. Alternatives**:
- Heroku: $25/mo minimum
- Render: $7/mo (but limited features)
- Railway: $10/mo
- **Coolify**: $10/mo (unlimited apps!)

---

## Getting Help

### Resources

- **Coolify Docs**: https://coolify.io/docs
- **Coolify Discord**: https://discord.gg/coolify
- **InkyCloud-F1 Issues**: https://github.com/Rhiz3K/InkyCloud-F1/issues

### Support

If you encounter issues:

1. **Check logs** in Coolify dashboard
2. **Search GitHub issues** for similar problems
3. **Open new issue** with:
   - Coolify version
   - Error logs
   - Environment details
   - Steps to reproduce

---

## Success Stories

After deploying on Coolify:

- **f1.inkycloud.click**: Running production on Coolify, <5 EUR/month
- **99.9% uptime**: Auto-restart on failures
- **Auto-deploy**: Push to GitHub → Live in 3 minutes
- **SSL included**: Free, automatic renewal

---

## Next Steps

After successful deployment:

1. ✅ **Monitor logs** for first 24 hours
2. ✅ **Set up alerts** for health check failures
3. ✅ **Configure custom domain** (if needed)
4. ✅ **Enable auto-deploy** from GitHub
5. ✅ **Add to ESP32** E-Ink display code:
   ```cpp
   // Update ESP32 to fetch from your Coolify URL
   http.begin("https://f1.yourdomain.com/calendar.bmp?lang=cs");
   ```

**Enjoy your self-hosted F1 calendar! 🏎️**

---

*Last updated: December 2024*  
*Coolify version: 4.x+*  
*InkyCloud-F1 version: 0.2.0+*
