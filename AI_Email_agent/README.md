# Email Wakeup Agent

An AI-powered email agent that automatically handles prospect email threads — classifying intent, negotiating meeting times, scheduling, and generating polished replies.

## Architecture

| Layer | Technology |
|---|---|
| Backend API | FastAPI |
| Agent Orchestration | LangGraph |
| LLM | OpenAI GPT-4o |
| Email | Gmail API |
| Calendar | Google Calendar API |
| Database | PostgreSQL + SQLAlchemy (async) |
| Task Queue | Celery + Redis |
| Frontend | Streamlit |
| Containers | Docker Compose |

## Project Structure

```
email-wakeup-agent/
├── backend/          # FastAPI app, LangGraph agent, Celery workers
├── frontend/         # Streamlit dashboard
├── docker-compose.yml
└── README.md
```

## Getting Started

1. Copy and fill in environment variables:
   ```bash
   cp backend/.env.example backend/.env
   ```

2. Start all services:
   ```bash
   docker-compose up --build
   ```

3. Access:
   - API docs: http://localhost:8000/docs
   - Dashboard: http://localhost:8501

## Agent Flow

```
Incoming Email
     │
     ▼
classify_intent
     │
     ├─ negotiate ──► negotiation ──► reply_generation
     ├─ schedule  ──► scheduling  ──► reply_generation
     └─ reschedule──► rescheduling──► reply_generation
```
