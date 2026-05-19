# ── Celery Beat Scheduler ────────────────────────────────────────────────────
# Triggers periodic tasks: poll_inbox (every 60s), follow_up (daily 08:00 UTC),
# cleanup_old_logs (daily 02:00 UTC)

Set-Location "$PSScriptRoot\backend"

Write-Host "Starting Celery Beat..." -ForegroundColor Magenta
celery -A app.workers.celery_app beat `
  --loglevel=info `
  --scheduler celery.beat:PersistentScheduler
