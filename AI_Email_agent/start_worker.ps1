# ── Celery Worker (Windows-compatible) ───────────────────────────────────────
# Must be run from the project root after Redis is up.
# Uses --pool=solo because Windows doesn't support fork-based multiprocessing.

Set-Location "$PSScriptRoot\backend"

Write-Host "Starting Celery Worker..." -ForegroundColor Cyan
celery -A app.workers.celery_app worker `
  --loglevel=info `
  --pool=solo `
  --queues=default,agent,gmail `
  --concurrency=1
