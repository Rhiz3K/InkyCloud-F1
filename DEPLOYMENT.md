# Deployment Guide

This guide covers different deployment options for the F1 E-Ink Calendar service.

## Quick Start

```bash
./quickstart.sh
```

## Coolify Deployment (Recommended for Self-Hosting)

[Coolify](https://coolify.io/) is a self-hosted, open-source Platform as a Service (PaaS) that makes deployment as easy as Heroku or Vercel.

### Why Coolify?

- ✅ **Self-hosted** - Full control over your infrastructure
- ✅ **Auto-deploy** - Push to GitHub, auto-deploy to production
- ✅ **Free SSL** - Automatic Let's Encrypt certificates
- ✅ **Built-in Monitoring** - Logs, metrics, health checks
- ✅ **Easy Scaling** - Horizontal scaling with one click
- ✅ **No Vendor Lock-in** - Run on any VPS
- ✅ **Cost-effective** - €5-10/month VPS handles significant traffic

### Quick Deploy (5 Minutes)

1. **In Coolify Dashboard**:
   - Click **"+ New"** → **"Public Repository"**
   - Repository URL: `https://github.com/Rhiz3K/InkyCloud-F1`
   - Branch: `main`

2. **Build Settings** (auto-detected):
   - Build Type: Dockerfile
   - Port: 8000
   - Health Check: `/health`

3. **Environment Variables** (required):
   ```bash
   APP_HOST=0.0.0.0
   APP_PORT=8000
   DEBUG=false
   
   # Optional - Monitoring
   SENTRY_DSN=your-sentry-dsn
   SENTRY_ENVIRONMENT=production
   
   # Optional - Analytics
   UMAMI_ENABLED=true
   UMAMI_WEBSITE_ID=your-website-id
   UMAMI_API_URL=https://analytics.yourdomain.com/api/send
   ```

4. **Deploy**:
   - Click **"Deploy"**
   - Wait 2-3 minutes
   - Access your app at the provided URL

**Done! 🎉** For detailed guide, troubleshooting, and advanced topics, see **[COOLIFY.md](./COOLIFY.md)**.

### Auto-Deploy from GitHub

Enable automatic deployments on every push:

1. Coolify → App Settings → **"Auto Deploy on Push"**
2. Copy webhook URL
3. Add to GitHub repository webhooks
4. Push to `main` → Auto-deploy! 🚀

### Custom Domain & SSL

1. Add domain in Coolify: `f1.yourdomain.com`
2. Point DNS A record to Coolify server IP
3. Coolify automatically provisions SSL certificate
4. Access via HTTPS with free SSL! 🔒

---

## Docker Deployment (Recommended)

### Using Docker Compose

```bash
# Start service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop service
docker-compose down

# Restart service
docker-compose restart
```

### Using Docker

```bash
# Build image
docker build -t f1-eink-cal .

# Run container
docker run -d \
  -p 8000:8000 \
  -e SENTRY_DSN=your-sentry-dsn \
  -e UMAMI_ENABLED=true \
  --name f1-calendar \
  f1-eink-cal

# View logs
docker logs -f f1-calendar

# Stop container
docker stop f1-calendar && docker rm f1-calendar
```

## Production Deployment

### Environment Variables

Create a `.env` file with production settings:

```bash
# Application
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=false

# Monitoring (GlitchTip/Sentry)
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1

# Analytics (Umami)
UMAMI_WEBSITE_ID=your-website-id
UMAMI_API_URL=https://analytics.yourdomain.com/api/send
UMAMI_ENABLED=true

# API
JOLPICA_API_URL=https://api.jolpi.ca/ergast/f1/current/next.json
REQUEST_TIMEOUT=10

# Language
DEFAULT_LANG=en
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name your-domain.com;  # e.g., f1.inkycloud.click

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Cache calendar.bmp for 1 hour
    location /calendar.bmp {
        proxy_pass http://localhost:8000;
        proxy_cache_valid 200 1h;
        add_header X-Cache-Status $upstream_cache_status;
    }
}
```

### Systemd Service

Create `/etc/systemd/system/f1-eink-cal.service`:

```ini
[Unit]
Description=F1 E-Ink Calendar Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/f1-eink-cal
Environment="PATH=/opt/f1-eink-cal/venv/bin"
EnvironmentFile=/opt/f1-eink-cal/.env
ExecStart=/opt/f1-eink-cal/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable f1-eink-cal
sudo systemctl start f1-eink-cal
sudo systemctl status f1-eink-cal
```

## Cloud Platforms

### Heroku

```bash
# Install Heroku CLI and login
heroku login

# Create app
heroku create f1-eink-cal

# Set environment variables
heroku config:set SENTRY_DSN=your-sentry-dsn
heroku config:set UMAMI_ENABLED=true

# Deploy
git push heroku main

# View logs
heroku logs --tail
```

Create `Procfile`:
```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Railway

1. Connect your GitHub repository
2. Set environment variables in Railway dashboard
3. Deploy automatically on push

### Render

1. Create new Web Service from GitHub repo
2. Build command: `pip install -e .`
3. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Set environment variables

### DigitalOcean App Platform

```yaml
# .do/app.yaml
name: f1-eink-cal
services:
- name: web
  github:
    repo: Rhiz3K/InkyCloud-F1
    branch: main
    deploy_on_push: true
  dockerfile_path: Dockerfile
  http_port: 8000
  instance_count: 1
  instance_size_slug: basic-xxs
  envs:
  - key: SENTRY_DSN
    scope: RUN_TIME
    type: SECRET
  - key: UMAMI_ENABLED
    scope: RUN_TIME
    value: "true"
```

## Monitoring & Logging

### GlitchTip Setup

1. Create account at https://glitchtip.com
2. Create new project
3. Copy DSN to `SENTRY_DSN` environment variable

### Umami Analytics Setup

1. Deploy Umami (https://umami.is)
2. Create website
3. Copy Website ID to `UMAMI_WEBSITE_ID`
4. Set `UMAMI_API_URL` to your Umami instance

## Health Checks

The service provides a health check endpoint:

```bash
curl http://your-domain/health
# Response: {"status":"healthy"}
```

Configure your monitoring tool to check this endpoint regularly.

## Performance Tuning

### Workers

For production, use multiple workers:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Caching

Consider adding Redis for caching:

```python
# Optional: Add Redis caching for race data
# Cache race data for 1 hour since it doesn't change frequently
```

## Security

1. **Use HTTPS** - Always use SSL/TLS in production
2. **Environment Variables** - Never commit secrets to git
3. **Rate Limiting** - Consider adding rate limiting for public deployments
4. **CORS** - Configure CORS if needed for web clients

## Backup & Recovery

No database is used, but consider backing up:
- `.env` file (securely)
- Translation files
- Custom modifications

## Scaling

The service is stateless and can be scaled horizontally:

```bash
# Docker Compose scaling
docker-compose up -d --scale f1-eink-cal=3
```

Add a load balancer (nginx, Traefik, etc.) in front of multiple instances.

## Troubleshooting

### Common Issues

1. **Port already in use**
   ```bash
   # Change port in .env or command line
   APP_PORT=8080 uvicorn app.main:app
   ```

2. **Failed to fetch race data**
   - Check internet connectivity
   - Verify Jolpica API is accessible
   - Check logs for specific error

3. **BMP rendering issues**
   - Verify Pillow is installed correctly
   - Check system has required image libraries

### Debug Mode

Enable debug mode for detailed logs:

```bash
DEBUG=true python -m app.main
```

### Logs

View logs based on deployment method:

```bash
# Docker Compose
docker-compose logs -f

# Docker
docker logs -f f1-calendar

# Systemd
journalctl -u f1-eink-cal -f
```
