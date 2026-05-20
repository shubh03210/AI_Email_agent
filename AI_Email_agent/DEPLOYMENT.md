# Production Deployment Guide

This document covers hardening the AI Email Agent for production beyond what
the default `docker-compose.yml` provides.

---

## Table of Contents

1. [Pre-Deployment Checklist](#1-pre-deployment-checklist)
2. [Environment Variables](#2-environment-variables)
3. [Building & Pushing Images](#3-building--pushing-images)
4. [Docker Compose — Production Startup](#4-docker-compose--production-startup)
5. [Reverse Proxy + TLS (nginx / Caddy)](#5-reverse-proxy--tls-nginx--caddy)
6. [Health Probes & Monitoring](#6-health-probes--monitoring)
7. [Secrets Management](#7-secrets-management)
8. [Database (Supabase)](#8-database-supabase)
9. [Google OAuth in Production](#9-google-oauth-in-production)
10. [Scaling](#10-scaling)
11. [Upgrading](#11-upgrading)
12. [Rollback](#12-rollback)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Pre-Deployment Checklist

Run through every item before going live.

### Security

- [ ] `SECRET_KEY` is ≥ 32 random bytes (not the default placeholder)
- [ ] `ADMIN_PASSWORD` is a strong unique password (not `change-me`)
- [ ] `GROQ_API_KEY` is a real key (not a placeholder)
- [ ] `backend/.env` is **not** committed to git (verify with `git status`)
- [ ] `credentials/` directory is **not** committed to git
- [ ] `DEBUG=false` in production `.env`

### Infrastructure

- [ ] Docker and Docker Compose v2 are installed on the host
- [ ] Supabase project is created; Transaction-mode connection string is copied
- [ ] Redis 7 is reachable (via Docker Compose redis service or managed service)
- [ ] Ports 3000 and 8000 are either exposed directly or behind a reverse proxy
- [ ] TLS certificate is provisioned if you're exposing the service to the internet

### Google OAuth

- [ ] `gmail_credentials.json` exists in `backend/credentials/`
- [ ] `calendar_credentials.json` exists in `backend/credentials/`
- [ ] You have already run the OAuth flow locally to generate token files
- [ ] `gmail_token.json` and `calendar_token.json` exist in `backend/credentials/`

---

## 2. Environment Variables

```bash
# Copy template
cp backend/.env.example backend/.env

# Generate a secure SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
```

**Critical values to set for production:**

```env
DEBUG=false
SECRET_KEY=<64-char-random-hex>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong-unique-password>
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>.pooler.supabase.com:6543/postgres
GROQ_API_KEY=<your-groq-key>
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

For Docker Compose deployments, Redis URLs must use the **service name** (`redis`),
not `localhost`.

---

## 3. Building & Pushing Images

If you're deploying to a server or container registry:

```bash
# Build images locally
docker compose build

# Tag and push to a registry (replace with your registry)
docker tag email_agent_backend:latest registry.example.com/email-agent-backend:v1.0
docker tag email_agent_frontend:latest registry.example.com/email-agent-frontend:v1.0
docker push registry.example.com/email-agent-backend:v1.0
docker push registry.example.com/email-agent-frontend:v1.0
```

To pull pre-built images in `docker-compose.yml`, replace the `build` block
with an `image` reference:

```yaml
backend:
  image: registry.example.com/email-agent-backend:v1.0
  # (remove the build: block)
```

---

## 4. Docker Compose — Production Startup

```bash
# On the production server, in the project directory:

# 1. Ensure credentials are in place
ls backend/credentials/
# → gmail_credentials.json  gmail_token.json
# → calendar_credentials.json  calendar_token.json

# 2. Start all services in detached mode
docker compose up --build -d

# 3. Follow logs until startup completes
docker compose logs -f backend

# 4. Verify health
curl http://localhost:8000/api/v1/health/ready
# → {"status":"ready","version":"...","db":"connected","redis":"connected"}

# 5. Verify frontend
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# → 200
```

### First-run admin bootstrap

The first time the backend starts, if the `users` table is empty, it creates
the admin account using `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env`.
This only happens once. After that, manage users via the API.

```bash
# Check bootstrap occurred
docker compose logs backend | grep "bootstrapped"
```

### Alembic migrations

Migrations run automatically via `entrypoint.sh` on every container start.
They are idempotent — running `alembic upgrade head` twice is safe.

To inspect the migration state:

```bash
docker compose exec backend alembic current
docker compose exec backend alembic history
```

---

## 5. Reverse Proxy + TLS (nginx / Caddy)

In production you typically run a reverse proxy in front of both services
so you can use a single port (443) with TLS.

### Option A — Caddy (automatic TLS via Let's Encrypt)

`Caddyfile`:

```
yourdomain.com {
    # Frontend SPA
    reverse_proxy localhost:3000

    # API backend
    handle /api/* {
        reverse_proxy localhost:8000
    }
}
```

```bash
caddy run --config Caddyfile
```

### Option B — nginx on the host

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # Frontend
    location / {
        proxy_pass http://127.0.0.1:3000;
    }

    # API
    location /api {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

### Updating CORS for your domain

Once you have a domain, add it to `CORS_ORIGINS` in `backend/.env`:

```env
# Add to backend/.env  (CORS_ORIGINS is a JSON array)
# The Settings class reads it automatically
```

Or update `app/core/config.py` to add your domain to the default `CORS_ORIGINS` list.

---

## 6. Health Probes & Monitoring

### Endpoints

| Endpoint | Probe type | Recommended polling |
|---|---|---|
| `GET /api/v1/health/live` | Liveness | Every 10–15 s |
| `GET /api/v1/health/ready` | Readiness | Every 15–30 s |
| `GET /api/v1/health/worker` | Worker health | Every 60 s (operator dashboards) |

### Uptime monitoring example (uptime-kuma / Prometheus blackbox)

```yaml
# uptime-kuma monitor
url: https://yourdomain.com/api/v1/health/ready
keyword: "ready"   # page must contain this string
interval: 60       # seconds
```

### Log collection

Logs are written to `stdout` (structured JSON via Python's logging module).
Docker writes them to its default log driver. Collect with:

```bash
# Tail live logs
docker compose logs -f

# Export to a file
docker compose logs --no-color > app.log
```

For production log aggregation, configure Docker's `json-file` driver with
rotation or use the `fluentd` / `gelf` drivers to ship logs to an aggregator.

---

## 7. Secrets Management

**Never** store secrets in:
- Git history
- Docker image layers (`.dockerignore` excludes `.env` from the build context)
- Process arguments (visible in `ps aux`)

**Recommended approaches:**

| Approach | Complexity | Notes |
|---|---|---|
| `.env` file on host (current default) | Low | Ensure file permissions: `chmod 600 backend/.env` |
| Docker secrets (`docker secret`) | Medium | Requires Docker Swarm mode |
| Vault (HashiCorp) | High | Best for large teams |
| Cloud secret manager (AWS SSM, GCP Secret Manager) | Medium | Best for cloud deployments |

### Minimum hardening for the `.env` approach

```bash
chmod 600 backend/.env
chown <deploy-user>:docker backend/.env
```

---

## 8. Database (Supabase)

### Connection pooling

Always use the **Transaction mode** pooler URL (port `6543`), not the direct
connection (port `5432`). The app uses SQLAlchemy's `NullPool` which requires
transaction mode.

```
postgresql+asyncpg://<user>:<password>@<project>.pooler.supabase.com:6543/postgres
```

### Backups

Supabase's free tier includes daily automated backups. For production, enable
**Point-in-Time Recovery (PITR)** in your Supabase project settings.

### Running migrations manually

```bash
cd backend
alembic upgrade head
```

---

## 9. Google OAuth in Production

OAuth tokens expire and need to be refreshed. The `google-auth` library handles
refresh automatically as long as the token file exists and the app has write
access to the credentials directory.

In Docker Compose, `backend/credentials/` is mounted as a **read-only** volume.
This means refresh tokens cannot be written back.

**For production:**

1. Pre-authorize locally (run the backend once, complete the OAuth flow)
2. Copy the generated token files to your production server before deploying
3. Keep the credentials directory outside of Docker volumes so the host copy
   is updated when tokens refresh — **remove the `:ro` flag** from the volume:

```yaml
# In docker-compose.yml — remove :ro for production
volumes:
  - ./backend/credentials:/app/credentials   # writeable
```

---

## 10. Scaling

### Celery workers

Add more worker replicas to increase throughput:

```bash
docker compose up --scale celery_worker=3 -d
```

Or in `docker-compose.yml`:

```yaml
deploy:
  replicas: 3
```

### Backend API

The backend runs with 2 uvicorn workers by default (`entrypoint.sh`). Increase
for higher concurrency:

```bash
# entrypoint.sh
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \        # ← increase for more concurrency
    --log-level info
```

> **Note**: With async SQLAlchemy (NullPool) + Supabase, each worker opens its
> own connection pool. Monitor Supabase connection count as you scale.

---

## 11. Upgrading

```bash
# Pull latest code
git pull origin main

# Rebuild and restart (zero-downtime not supported in this compose setup)
docker compose up --build -d

# Migrations run automatically in the backend container on startup
# Verify:
docker compose logs backend | grep -E "alembic|migration"
```

---

## 12. Rollback

```bash
# Downgrade one migration
docker compose exec backend alembic downgrade -1

# Or downgrade to a specific revision
docker compose exec backend alembic downgrade <revision-id>

# Restart with the previous image tag
docker compose up -d --force-recreate
```

---

## 13. Troubleshooting

### Backend fails to start

```bash
docker compose logs backend
```

Common causes:
- `DATABASE_URL` is wrong or unreachable → check Supabase dashboard
- `SECRET_KEY` is too short → must be ≥ 32 chars
- `ADMIN_PASSWORD` is too short → must be ≥ 8 chars
- Alembic migration fails → check migration history and DB connectivity

### Celery worker not processing tasks

```bash
docker compose logs celery_worker
curl http://localhost:8000/api/v1/health/worker
```

Check `queues.default`, `queues.agent`, `queues.gmail` in the response — non-zero
means tasks are queued but not consumed.

### Gmail / Calendar authorization errors

```bash
docker compose logs backend | grep -i "credential\|token\|auth"
```

The OAuth token may have expired and the credentials directory is read-only.
Remove `:ro` from the credentials volume mount and restart the backend.

### Frontend shows "Network Error" for API calls

The nginx proxy forwards `/api/*` to `http://backend:8000`. If the backend
container isn't healthy, the frontend will show network errors.

```bash
docker compose ps          # check backend status
curl http://localhost:8000/api/v1/health/live
```

### Logs full of GROQ errors

The Groq API key may be invalid or rate-limited. Check:
- `GROQ_API_KEY` in `.env` is correct
- You haven't exceeded the free tier rate limit (https://console.groq.com/)
