# ══════════════════════════════════════════════════════════════════════════════
# AI Email Agent — Start Everything
# Run this once and all services launch in separate windows.
# ══════════════════════════════════════════════════════════════════════════════

$Root    = $PSScriptRoot
$Backend = "$Root\backend"

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   AI Email Agent — Starting Up...    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 1. Redis (Docker) ─────────────────────────────────────────────────────────
Write-Host "[1/5] Starting Redis..." -ForegroundColor Yellow
docker run -d --name email_agent_redis --restart unless-stopped -p 6379:6379 redis:7-alpine 2>$null
if ($LASTEXITCODE -ne 0) {
    docker start email_agent_redis 2>$null
}
Start-Sleep -Seconds 2
$ping = docker exec email_agent_redis redis-cli ping 2>&1
if ($ping -match "PONG") {
    Write-Host "  Redis OK (localhost:6379)" -ForegroundColor Green
} else {
    Write-Host "  Redis may not be ready — is Docker Desktop running?" -ForegroundColor Red
    Write-Host "  Start Docker Desktop first, then re-run this script." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ── 2. Celery Worker ──────────────────────────────────────────────────────────
Write-Host "[2/5] Starting Celery Worker..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Backend'; `$host.UI.RawUI.WindowTitle = 'Celery Worker'; Write-Host 'Celery Worker' -ForegroundColor Cyan; celery -A app.workers.celery_app worker --loglevel=info --pool=solo --queues=default,agent,gmail --concurrency=1"
)
Write-Host "  Celery Worker window opened." -ForegroundColor Green

# ── 3. Celery Beat ────────────────────────────────────────────────────────────
Write-Host "[3/5] Starting Celery Beat..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Backend'; `$host.UI.RawUI.WindowTitle = 'Celery Beat'; Write-Host 'Celery Beat' -ForegroundColor Magenta; celery -A app.workers.celery_app beat --loglevel=info --scheduler celery.beat:PersistentScheduler"
)
Write-Host "  Celery Beat window opened." -ForegroundColor Green

# ── 4. FastAPI Backend ────────────────────────────────────────────────────────
Write-Host "[4/5] Starting FastAPI backend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Backend'; `$host.UI.RawUI.WindowTitle = 'FastAPI Backend'; Write-Host 'FastAPI Backend' -ForegroundColor Blue; uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
)
Write-Host "  FastAPI window opened  →  http://localhost:8000" -ForegroundColor Green

# ── 5. React Frontend ─────────────────────────────────────────────────────────
Write-Host "[5/5] Starting React frontend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root\frontend_react'; `$host.UI.RawUI.WindowTitle = 'React Frontend'; Write-Host 'React Frontend' -ForegroundColor Yellow; npm run dev"
)
Write-Host "  React window opened    →  http://localhost:5173" -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║          All services started!       ║" -ForegroundColor Cyan
Write-Host "║                                      ║" -ForegroundColor Cyan
Write-Host "║  Dashboard  →  http://localhost:5173 ║" -ForegroundColor Cyan
Write-Host "║  API Docs   →  http://localhost:8000 ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "This window can be closed. Each service runs in its own window." -ForegroundColor Gray
Write-Host ""
Read-Host "Press Enter to close this launcher"
