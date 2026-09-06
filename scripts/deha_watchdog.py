"""Polls deha /health and starts NaradaSupervisor if down.

Designed to be run by Windows Task Scheduler every minute. Uses ``pythonw.exe``
so no console window is allocated when fired by the scheduler. Logs to
``%LOCALAPPDATA%/narada/logs/deha-watchdog.log`` — append-only, lightweight.

Restart policy (v1):
  - Healthy (HTTP 2xx on /health): silent, exit 0.
  - Unhealthy AND no ``deha.voice.supervisor`` process running:
    fire the ``NaradaSupervisor`` scheduled task.
  - Unhealthy AND process IS running: do nothing (could be starting up
    or briefly stuck; killing+restarting could mask a real problem).
    Logged once per occurrence so a hang is visible in the log.

Future v2: detect "stuck for >N minutes" via a stamped marker and
hard-restart, since v1 won't recover from a wedged supervisor.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HEALTH_URL = "http://127.0.0.1:8765/health"
HEALTH_TIMEOUT_S = 3.0
TASK_NAME = "NaradaSupervisor"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "narada" / "logs" / "deha-watchdog.log"

# We're invoked as pythonw (windowless). Any console-app subprocess we
# spawn without CREATE_NO_WINDOW gets a fresh conhost — the exact flash
# we just spent 2026-05-17 hunting down across prana + deha. Don't
# regress it here.
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT_S) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _supervisor_process_running() -> bool:
    """True iff any ``pythonw.exe`` is running deha.voice.supervisor."""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process | "
             "Where-Object { $_.CommandLine -like '*deha.voice.supervisor*' } | "
             "Measure-Object).Count"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WIN,
        )
        out = (proc.stdout or "").strip()
        return out.isdigit() and int(out) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _start_supervisor_task() -> bool:
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WIN,
        )
        if proc.returncode == 0:
            log(f"started {TASK_NAME}")
            return True
        log(f"ERROR starting {TASK_NAME}: rc={proc.returncode} "
            f"stderr={(proc.stderr or '').strip()[:200]}")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log(f"ERROR starting {TASK_NAME}: {exc}")
        return False


def main() -> int:
    if _health_ok():
        return 0  # happy path — stay silent

    if _supervisor_process_running():
        log("supervisor process exists but /health unresponsive — leaving alone (may be starting or wedged)")
        return 0

    log(f"/health failed and no supervisor process — starting {TASK_NAME}")
    return 0 if _start_supervisor_task() else 1


if __name__ == "__main__":
    sys.exit(main())
