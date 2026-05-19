# ══════════════════════════════════════════════════════════════════════════════
# AI Email Agent — Stop Everything
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "Stopping AI Email Agent services..." -ForegroundColor Red
Write-Host ""

# Stop Celery processes
Write-Host "Stopping Celery Worker and Beat..." -ForegroundColor Yellow
Get-Process -Name "celery" -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "  Done." -ForegroundColor Green

# Stop FastAPI (uvicorn)
Write-Host "Stopping FastAPI (uvicorn)..." -ForegroundColor Yellow
Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force
# Also stop any python processes running uvicorn
Get-Process -Name "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match "uvicorn|FastAPI" } |
    Stop-Process -Force
Write-Host "  Done." -ForegroundColor Green

# Stop React (node/vite)
Write-Host "Stopping React frontend (node)..." -ForegroundColor Yellow
Get-Process -Name "node" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match "React|vite|frontend" } |
    Stop-Process -Force
Write-Host "  Done." -ForegroundColor Green

# Stop Redis container
Write-Host "Stopping Redis container..." -ForegroundColor Yellow
docker stop email_agent_redis 2>$null
Write-Host "  Done." -ForegroundColor Green

Write-Host ""
Write-Host "All services stopped." -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to close"
