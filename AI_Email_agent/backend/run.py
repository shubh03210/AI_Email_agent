"""
Dev runner — starts all services with a single command:
    python run.py

Starts (in order):
  1. Verifies Redis is reachable
  2. Celery Gmail worker   (inbox poll, outreach — logs/worker-gmail.log)
  3. Celery Agent worker   (auto-runs agent on prospect replies — logs/worker-agent.log)
  4. Celery Beat           (schedules inbox poll every GMAIL_POLL_INTERVAL_SECONDS)
  5. FastAPI / Uvicorn     (foreground — Ctrl+C here stops everything)

Do NOT use ``uvicorn`` alone — prospect replies and outreach require Celery.
"""

import subprocess
import sys
import os
import time
import signal
import socket

# Force UTF-8 output on Windows so print() never raises UnicodeEncodeError
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── helpers ───────────────────────────────────────────────────────────────────

def _redis_ok(host: str = "localhost", port: int = 6379, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _banner(msg: str, color: str = "\033[96m") -> None:
    reset = "\033[0m"
    print(f"{color}{msg}{reset}", flush=True)


def _celery_worker(name: str, queues: str, log_file: str, popen_flags: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "celery",
            "-A", "app.workers.celery_app", "worker",
            "--loglevel=info",
            "--pool=solo",
            f"--queues={queues}",
            "--concurrency=1",
            "-n", name,
        ],
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        creationflags=popen_flags,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Change to the backend directory so all imports resolve correctly
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    _banner("=" * 42)
    _banner("   AI Email Agent -- Starting Up...")
    _banner("=" * 42)

    # ── 1. Redis check ────────────────────────────────────────────────────────
    _banner("\n[1/5] Checking Redis...", "\033[93m")
    retries = 0
    while not _redis_ok():
        retries += 1
        if retries == 1:
            print("  Redis not reachable — make sure Docker Desktop is running.")
            print("  Waiting up to 15 seconds...", flush=True)
        if retries > 15:
            _banner("  ERROR: Redis never came up. Start Docker Desktop first.", "\033[91m")
            sys.exit(1)
        time.sleep(1)
    _banner("  Redis OK (localhost:6379)", "\033[92m")

    procs: list[subprocess.Popen] = []

    def _shutdown(sig=None, frame=None):
        _banner("\n\nShutting down all services...", "\033[93m")
        for p in procs:
            try:
                if sys.platform == "win32":
                    # Worker/beat are in their own process group; send CTRL_BREAK
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
            except Exception:
                pass
        # Give them a moment, then force-kill
        time.sleep(3)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        _banner("All stopped. Bye!")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # On Windows we put worker/beat in their own process group so that
    # Ctrl+C / uvicorn hot-reload signals don't cascade into them.
    _popen_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    # Separate workers so a long inbox poll does not block agent runs.
    _banner("\n[2/5] Starting Celery Gmail worker...", "\033[93m")
    gmail_worker = _celery_worker(
        "gmail@%h", "gmail,default", "logs/worker-gmail.log", _popen_flags
    )
    procs.append(gmail_worker)
    _banner("  Gmail worker started   (logs -> backend/logs/worker-gmail.log)", "\033[92m")

    _banner("\n[3/5] Starting Celery Agent worker...", "\033[93m")
    agent_worker = _celery_worker(
        "agent@%h", "agent", "logs/worker-agent.log", _popen_flags
    )
    procs.append(agent_worker)
    _banner("  Agent worker started   (logs -> backend/logs/worker-agent.log)", "\033[92m")

    # ── 4. Celery Beat ────────────────────────────────────────────────────────
    _banner("\n[4/5] Starting Celery Beat...", "\033[93m")
    beat = subprocess.Popen(
        ["celery",
         "-A", "app.workers.celery_app", "beat",
         "--loglevel=info",
         "--scheduler", "celery.beat:PersistentScheduler"],
        stdout=open("logs/beat.log", "a"),
        stderr=subprocess.STDOUT,
        creationflags=_popen_flags,
    )
    procs.append(beat)
    _banner("  Celery Beat started    (logs -> backend/logs/beat.log)", "\033[92m")
    _banner(
        "  Inbox poll runs every 60s — new prospect replies auto-trigger the agent.",
        "\033[90m",
    )

    # Give workers a moment to connect to Redis before API starts accepting requests
    time.sleep(3)

    # ── 5. FastAPI / Uvicorn (foreground) ─────────────────────────────────────
    _banner("\n[5/5] Starting FastAPI...", "\033[93m")
    _banner("  Dashboard API -> http://localhost:8000", "\033[92m")
    _banner("  Press Ctrl+C to stop everything.\n", "\033[90m")

    uvicorn = subprocess.Popen(
        ["uvicorn", "app.main:app",
         "--reload",
         "--host", "0.0.0.0",
         "--port", "8000"],
    )
    procs.append(uvicorn)

    # Wait for uvicorn -- when it exits (or Ctrl+C), shut everything down
    try:
        uvicorn.wait()
    finally:
        _shutdown()


if __name__ == "__main__":
    # Make sure logs/ folder exists
    os.makedirs("logs", exist_ok=True)
    main()
