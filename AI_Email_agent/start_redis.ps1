# ── Start Redis only (via Docker) ────────────────────────────────────────────
# Pulls redis:7-alpine if not cached. Runs detached on port 6379.

Write-Host "Starting Redis container..." -ForegroundColor Yellow

docker run -d `
  --name email_agent_redis `
  --restart unless-stopped `
  -p 6379:6379 `
  redis:7-alpine

if ($LASTEXITCODE -eq 0) {
    Write-Host "Redis started on localhost:6379" -ForegroundColor Green
} else {
    # Container may already exist — try starting it
    docker start email_agent_redis
    Write-Host "Redis container restarted." -ForegroundColor Green
}

# Verify
Start-Sleep -Seconds 2
docker exec email_agent_redis redis-cli ping
