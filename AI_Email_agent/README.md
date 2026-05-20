# AI Email Agent

An AI-powered autonomous email agent that handles B2B/HR prospect outreach
end-to-end — classifying intent, negotiating budgets, scheduling meetings via
Google Calendar, and generating polished replies — with zero manual
intervention.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Quick Start — Docker Compose](#quick-start--docker-compose)
5. [Local Development (no Docker)](#local-development-no-docker)
6. [Google OAuth Setup](#google-oauth-setup)
7. [Database Migrations](#database-migrations)
8. [Configuration Reference](#configuration-reference)
9. [API Reference](#api-reference)
10. [Health Endpoints](#health-endpoints)
11. [Agent Flow](#agent-flow)
12. [Authentication & Roles](#authentication--roles)
13. [Production Deployment](#production-deployment)

---

## Architecture

| Layer | Technology |
|---|---|
| Backend API | FastAPI (async) |
| Agent Orchestration | LangGraph |
| LLM | Groq — Llama 3.3-70b-versatile |
| Email | Gmail API (OAuth 2.0) |
| Calendar | Google Calendar API (OAuth 2.0) |
| Database | PostgreSQL via Supabase (async SQLAlchemy + Alembic) |
| Task Queue | Celery + Redis |
| Frontend | React 19 + Vite + Tailwind CSS v4 |
| Containers | Docker + Docker Compose |

---

## Project Structure

```
AI_Email_agent/
├── backend/
│   ├── app/
│   │   ├── agents/        # LangGraph graph, nodes, state, prompts
│   │   ├── api/v1/        # FastAPI routers
│   │   │   └── endpoints/ # auth, prospects, threads, config, health, metrics, …
│   │   ├── core/          # Settings, logging, security, env validation
│   │   ├── db/            # SQLAlchemy session, init
│   │   ├── models/        # ORM models
│   │   ├── repositories/  # Async DB access layer
│   │   ├── schemas/       # Pydantic v2 request/response schemas
│   │   ├── services/      # Gmail, Calendar, LLM, Memory, Negotiation, Tone
│   │   └── workers/       # Celery app + tasks (outreach, follow-up, polling)
│   ├── alembic/           # Database migrations (run automatically on startup)
│   ├── tests/             # pytest test suite (550+ tests)
│   ├── Dockerfile         # Multi-stage build (builder → runtime)
│   ├── .dockerignore
│   ├── .env.example       # ← Copy to .env and fill in values
│   ├── entrypoint.sh      # Runs migrations then starts uvicorn
│   └── requirements.txt
├── frontend_react/
│   ├── src/
│   │   ├── api/           # Axios client + TypeScript interfaces
│   │   └── pages/         # Dashboard, Prospects, Config, Threads, Logs
│   ├── Dockerfile         # Multi-stage: node build → nginx serve
│   ├── .dockerignore
│   └── nginx.conf         # SPA routing + /api proxy + gzip + security headers
├── docker-compose.yml
└── README.md
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Docker Desktop | ≥ 24 | Includes Docker Compose v2 |
| Node.js | ≥ 20 | Only needed for local frontend dev |
| Python | ≥ 3.11 | Only needed for local backend dev |
| Supabase account | — | Free tier works. Use the **transaction mode** connection string (port 6543) |
| Groq API key | — | Free at https://console.groq.com/ |
| Google Cloud project | — | For Gmail + Calendar OAuth. See [Google OAuth Setup](#google-oauth-setup) |

---

## Quick Start — Docker Compose

### 1. Clone and configure

```bash
git clone <repo-url> && cd AI_Email_agent
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in **every value marked `← REQUIRED`**:

| Variable | Where to get it |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | Any strong password (≥ 8 chars) |
| `DATABASE_URL` | Supabase → Project Settings → Database → Connection string (Transaction mode, port 6543) |
| `GROQ_API_KEY` | https://console.groq.com/ |
| `REDIS_URL` | `redis://redis:6379/0` (when using Docker Compose) |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` |

> **Redis URL for Docker Compose**: use `redis://redis:6379/0` (the Docker service name).
> For local dev (backend outside Docker), use `redis://localhost:6379/0`.

### 2. Add Google OAuth credentials

See [Google OAuth Setup](#google-oauth-setup) — you need two JSON files in `backend/credentials/` before the Gmail + Calendar integrations will work.

### 3. Build and start

```bash
docker compose up --build -d
```

The first startup will:
- Pull/build all images (takes 2–4 minutes first time)
- Start Redis
- Run Alembic migrations (automatically applied via `entrypoint.sh`)
- Create the bootstrap admin user
- Start the Celery worker and beat scheduler
- Build the React SPA and start nginx

### 4. Verify startup

```bash
# Check all containers are running
docker compose ps

# Tail logs from all services
docker compose logs -f

# Backend health
curl http://localhost:8000/api/v1/health/live    # → {"status":"ok"}
curl http://localhost:8000/api/v1/health/ready   # → {"status":"ready"} when DB+Redis up
```

### 5. Access the application

| Service | URL |
|---|---|
| **Dashboard** | http://localhost:3000 |
| **API docs (Swagger)** | http://localhost:8000/api/v1/docs |
| **API docs (Redoc)** | http://localhost:8000/api/v1/redoc |

Login with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` you set in `.env`.

### Stopping

```bash
docker compose down          # Stop containers (data preserved in volumes)
docker compose down -v       # Stop + delete volumes (clean slate)
```

---

## Local Development (no Docker)

Local dev keeps Redis in Docker and runs the backend + frontend natively for
fast iteration (no rebuild needed).

### Redis (keep in Docker)

```bash
docker compose up redis -d
```

### Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # macOS / Linux

pip install -r requirements.txt

# Set env (point Redis URLs to localhost)
cp .env.example .env
# Edit .env — set REDIS_URL=redis://localhost:6379/0 and other required values

# Apply migrations
alembic upgrade head

# Start the API server (auto-reload)
uvicorn app.main:app --reload --port 8000
```

### Celery Worker (separate terminal)

```bash
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app worker --loglevel=info --queues=default,agent,gmail
```

### Celery Beat Scheduler (separate terminal)

```bash
cd backend
.venv\Scripts\activate
celery -A app.workers.celery_app beat --loglevel=info
```

### Frontend

```bash
cd frontend_react
npm install
npm run dev          # http://localhost:5173 with Vite HMR
```

> Vite's dev server proxies `/api` to `http://localhost:8000` automatically
> (configured in `vite.config.ts`). No nginx needed for local dev.

### PowerShell convenience scripts

The repo includes PowerShell scripts for Windows:

```powershell
.\start_all.ps1      # Start Redis + backend + worker + beat + frontend dev server
.\stop_all.ps1       # Stop all processes
.\start_worker.ps1   # Start only the Celery worker
.\start_beat.ps1     # Start only Celery beat
.\start_redis.ps1    # Start only Redis (via Docker)
```

---

## Google OAuth Setup

Both Gmail and Google Calendar integrations use OAuth 2.0 with a Desktop App
credential type. You only need to set this up once per Google account.

### Steps

1. Go to https://console.cloud.google.com/ and create or select a project.

2. Enable APIs:
   - **Gmail API** (APIs & Services → Library → search "Gmail API" → Enable)
   - **Google Calendar API** (same path)

3. Create OAuth credentials:
   - APIs & Services → Credentials → **Create Credentials** → OAuth client ID
   - Application type: **Desktop app**
   - Give it a name (e.g. "Email Agent Local")
   - Click **Download JSON**

4. Place the credential file:
   ```
   backend/credentials/gmail_credentials.json
   ```
   Also copy it (or create a separate credential) as:
   ```
   backend/credentials/calendar_credentials.json
   ```

5. Authorize on first run:
   - Start the backend (`uvicorn app.main:app --reload` or `docker compose up backend`)
   - The first time a Gmail/Calendar API call is made, a browser window opens
   - Log in with the Google account that will send emails
   - This creates `backend/credentials/gmail_token.json` and `backend/credentials/calendar_token.json`

6. For Docker: the `credentials/` directory is mounted as a **read-only volume**
   (`./backend/credentials:/app/credentials:ro`). Pre-authorize locally first,
   then the token files will be available inside the container.

> **Never commit** `gmail_token.json`, `calendar_token.json`, or the credentials
> JSON files to git. They are excluded by `.gitignore`.

---

## Database Migrations

Alembic manages all schema changes. Migrations run automatically when the
backend container starts (`entrypoint.sh` calls `alembic upgrade head`).

### Manual migration commands

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Check current migration state
alembic current

# Generate a new migration from model changes
alembic revision --autogenerate -m "describe_the_change"

# Rollback one migration
alembic downgrade -1
```

### Migration history

| Rev | Description |
|---|---|
| 001 | Initial schema |
| 002 | Add agent_runs table |
| 003 | Gmail idempotency keys |
| 004 | Calendar enhancements |
| 005 | LangGraph hardening fields |
| 006 | Task infrastructure |
| 007 | Company field + indexes |
| 008 | Cascade delete + performance indexes |
| 009 | Product enhancements (tone, recruiter, cadence) |

---

## Configuration Reference

All runtime agent behaviour is controlled via the **Config page** in the UI
(`/api/v1/config/`) — no code changes or restarts needed.

| Field | Description | Default |
|---|---|---|
| `gig_description` | Job/role description given to the LLM | — |
| `tone` | Email tone preset | `formal` |
| `budget_ceiling` | Maximum compensation the agent will accept | 5000 |
| `timezone` | Scheduling timezone | `UTC` |
| `working_hours_start` | Start of recruiter working hours (0–23) | 9 |
| `working_hours_end` | End of recruiter working hours (0–23) | 18 |
| `follow_up_cadence` | JSON array of follow-up days, e.g. `[1, 3, 7]` | `[1, 3, 7]` |
| `max_follow_ups` | Maximum number of follow-up emails | 3 |
| `recruiter_name` | Recruiter name used in email signatures | `Alex` |
| `recruiter_title` | Recruiter title used in email signatures | `HR Recruiter` |
| `recruiter_signature` | Free-text email footer appended to all sent emails | — |
| `meeting_confirmation_template` | Jinja-style template for meeting confirmations | (built-in) |

**Tone presets**: `formal` · `friendly` · `startup` · `executive` · `professional` · `casual`

---

## API Reference

Interactive docs are available at:
- **Swagger UI**: http://localhost:8000/api/v1/docs
- **Redoc**: http://localhost:8000/api/v1/redoc

### Key endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/token` | Obtain JWT token |
| `GET` | `/api/v1/prospects/` | List all prospects |
| `POST` | `/api/v1/prospects/` | Create a prospect |
| `GET` | `/api/v1/threads/` | List email threads |
| `GET` | `/api/v1/config/` | Read agent config |
| `PUT` | `/api/v1/config/` | Update agent config |
| `GET` | `/api/v1/metrics/` | Pipeline KPIs dashboard |
| `GET` | `/api/v1/logs/` | Agent run audit log |
| `GET` | `/api/v1/health/live` | Liveness probe |
| `GET` | `/api/v1/health/ready` | Readiness probe |
| `GET` | `/api/v1/health/worker` | Worker / queue health |

---

## Health Endpoints

All health endpoints are **public** (no auth required) so orchestrators can
poll them freely.

| Endpoint | Purpose | Returns 503 when |
|---|---|---|
| `GET /api/v1/health/live` | Process liveness | Never (if process is running) |
| `GET /api/v1/health/ready` | Dependency readiness | DB or Redis unreachable |
| `GET /api/v1/health/` | Legacy combined check | DB or Redis unreachable |
| `GET /api/v1/health/worker` | Worker + queue status | Always 200 (check `status` field) |
| `GET /api/v1/health/worker/dlq` | Dead-letter queue listing | Always 200 |

---

## Agent Flow

```
Incoming Email
      │
      ▼
classify_intent  ──(confidence < threshold)──► escalate_to_human
      │
      ├─ interested    ──► scheduling   ──► reply_generation ──► send_reply
      ├─ negotiating   ──► negotiation  ──► reply_generation ──► send_reply
      ├─ reschedule    ──► rescheduling ──► reply_generation ──► send_reply
      └─ curious / declined / ambiguous ──► reply_generation ──► send_reply
```

The agent runs as a Celery task (`run_agent_task`) triggered by the Gmail
polling loop (`poll_gmail_task`, runs every 60 seconds by default).

---

## Authentication & Roles

All API routes except `/health/*` require a valid JWT Bearer token.

| Role | Permissions |
|---|---|
| `admin` | Full access — read + write everything, manage users |
| `operator` | Read prospects, threads, config, metrics, logs |

Obtain a token:

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -d "username=admin&password=<your-password>"
```

Use the token:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/prospects/
```

---

## Production Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for a full production hardening guide
covering reverse proxy, TLS, secrets management, and monitoring.
