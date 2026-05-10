"""Narada supervisor — single-point lifecycle for the voice arc.

Keeps the embodiment alive across reboots and partial failures. Stays
deliberately separate from brain_server so the conversation/agent layer
is free to evolve (Hermes, OpenClaw, etc.) without lifecycle concerns
mixed in.

Components managed:
  - homeassistant docker container (must already exist; we only start it)
  - brain_server child process (we own its lifecycle)

Components observed but not owned:
  - Docker daemon / Docker Desktop (we surface its absence; we don't try
    to launch Docker Desktop GUI from a service-style process — too
    flaky on Windows and out of scope for "minimal"; the user starts
    Docker, we drive everything else)

Heartbeat: 30s cadence. After 3 consecutive failures for a managed
component, restart it with exponential backoff (5s → 30s → 60s, capped).
Log every check; only restart on real triple-misses.

Status HTTP: GET http://127.0.0.1:9999/status returns JSON with each
component's last-check-at, status, and restart count.

Logging: rotating file at %LOCALAPPDATA%/narada/logs/supervisor.log
plus stdout (so Task Scheduler's transcript captures it too).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp
import psutil
from aiohttp import web


_LOG = logging.getLogger("narada.supervisor")


# ---------- config ----------

HEARTBEAT_INTERVAL_S = 30.0
FAILURES_TO_RESTART = 3
RESTART_BACKOFF_S = [5.0, 30.0, 60.0]  # capped at last value
HEALTH_CHECK_TIMEOUT_S = 3.0
# Grace window after supervisor start. At Windows logon the supervisor
# task runs alongside Docker Desktop's autostart — Docker can take
# 30-45s to be ready. During this window, failed health checks are
# logged at INFO and don't accrue toward FAILURES_TO_RESTART, so we
# don't alarm-storm during the natural boot race.
STARTUP_GRACE_S = 90.0

HA_CONTAINER_NAME = "homeassistant"
HA_HEALTH_URL = "http://127.0.0.1:8123/manifest.json"

BRAIN_HEALTH_URL = "http://127.0.0.1:8765/health"
BRAIN_TTS_HOST = "127.0.0.1"
BRAIN_TTS_PORT = 10210

STATUS_HTTP_PORT = 9999


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "narada" / "logs"
    return Path.home() / ".narada" / "logs"


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _venv_python() -> Path:
    """Resolve the python that should run brain_server.

    Prefers the project's .venv; falls back to the supervisor's own
    sys.executable (which is already correct if we were launched from it).
    """
    root = _project_root()
    candidate = root / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


# ---------- component model ----------

@dataclass
class Component:
    name: str
    check: Callable[[], Awaitable[bool]]
    restart: Callable[[], Awaitable[None]]
    # Per-component post-restart grace: HA needs ~60s to start serving
    # /manifest.json after `docker start`; brain_server is up in seconds.
    # During this window, failed checks are logged as "warming" rather
    # than counting toward another restart, so we don't loop while the
    # component is still booting.
    restart_grace_s: float = 0.0
    last_status: str = "unknown"
    last_check_at: float = 0.0
    consecutive_failures: int = 0
    restart_count: int = 0
    last_restart_at: float = 0.0
    restart_grace_until: float = 0.0
    backoff_index: int = 0

    def to_status(self) -> dict:
        return {
            "name": self.name,
            "last_status": self.last_status,
            "last_check_at": self.last_check_at,
            "consecutive_failures": self.consecutive_failures,
            "restart_count": self.restart_count,
            "last_restart_at": self.last_restart_at,
        }


# ---------- health checks ----------

async def _http_ok(url: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                return r.status == 200
    except Exception:
        return False


async def _tcp_ok(host: str, port: int) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, HEALTH_CHECK_TIMEOUT_S)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _docker_container_running(name: str) -> bool:
    """`docker inspect` is the cheapest reliable check."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), HEALTH_CHECK_TIMEOUT_S)
        return proc.returncode == 0 and stdout.strip() == b"true"
    except Exception:
        return False


# ---------- HA container management ----------

class HAContainer:
    name = "homeassistant_container"

    async def check(self) -> bool:
        if not await _docker_container_running(HA_CONTAINER_NAME):
            return False
        # Container running ≠ HA app ready. Probe the HTTP port too.
        return await _http_ok(HA_HEALTH_URL)

    async def restart(self) -> None:
        _LOG.info("restarting HA container")
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", HA_CONTAINER_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            _LOG.warning("docker start %s returned %s", HA_CONTAINER_NAME, proc.returncode)


# ---------- brain_server child process management ----------

class BrainServerProcess:
    name = "brain_server"

    def __init__(
        self,
        python_path: Path,
        prompt_file: Path | None,
        voice: str,
        model: str,
    ):
        self._python = python_path
        self._prompt_file = prompt_file
        self._voice = voice
        self._model = model
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        async with self._lock:
            if self.alive:
                return
            # Orphan-sweep: if a previous supervisor or manual run left a
            # brain_server alive, kill it before binding the same ports.
            # Targets are matched by the exact module invocation, so we
            # never touch unrelated python processes.
            killed = self._kill_orphans()
            if killed:
                _LOG.info("killed %d orphan brain_server process(es): %s",
                          len(killed), killed)
                # Brief pause so Windows releases the bound port before
                # we try to bind it again.
                await asyncio.sleep(0.5)
            cmd = [
                str(self._python),
                "-m", "deha.voice.brain_server",
                "--voice", self._voice,
                "--model", self._model,
            ]
            if self._prompt_file is not None:
                cmd += ["--prompt-file", str(self._prompt_file)]
            _LOG.info("spawning brain_server: %s", " ".join(cmd))
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(_project_root()),
            )
            asyncio.create_task(self._tail_stdout())

    @staticmethod
    def _kill_orphans() -> list[int]:
        """Kill any other python processes running brain_server.

        Targeted by command-line substring `deha.voice.brain_server`,
        so we never risk killing unrelated python processes. Returns the
        PIDs killed (for logging).
        """
        killed: list[int] = []
        own_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == own_pid:
                    continue
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "deha.voice.brain_server" not in cmdline:
                    continue
                p.kill()
                killed.append(p.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return killed

    async def _tail_stdout(self) -> None:
        """Stream brain_server's combined stdout/stderr into our logger."""
        assert self._proc is not None and self._proc.stdout is not None
        proc = self._proc
        while True:
            line = await proc.stdout.readline()
            if not line:
                rc = await proc.wait()
                _LOG.warning("brain_server exited rc=%s", rc)
                return
            _LOG.info("[brain] %s", line.decode("utf-8", errors="replace").rstrip())

    async def stop(self) -> None:
        async with self._lock:
            if not self.alive:
                return
            assert self._proc is not None
            try:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), 5.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except ProcessLookupError:
                pass

    async def check(self) -> bool:
        if not self.alive:
            return False
        if not await _http_ok(BRAIN_HEALTH_URL):
            return False
        if not await _tcp_ok(BRAIN_TTS_HOST, BRAIN_TTS_PORT):
            return False
        return True

    async def restart(self) -> None:
        _LOG.info("restarting brain_server")
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()


# ---------- supervisor loop ----------

class Supervisor:
    def __init__(self, components: list[Component]):
        self._components = components
        self._stop = asyncio.Event()
        self._started_at = time.time()

    def stop(self) -> None:
        self._stop.set()

    @property
    def in_grace(self) -> bool:
        return (time.time() - self._started_at) < STARTUP_GRACE_S

    async def run(self) -> None:
        # Initial bring-up: ALWAYS try to start every down component
        # immediately, regardless of grace. Grace is only meant to
        # suppress noisy alarms in the heartbeat loop while peer
        # services (Docker Desktop) finish booting — it must not gate
        # the initial start of components we own (brain_server), or
        # they'd never come up until grace expired and the failure
        # counter tripped. A restart attempt that fails (e.g. Docker
        # daemon still starting) is fine; the heartbeat loop will
        # retry on the next tick.
        for c in self._components:
            try:
                ok = await c.check()
                c.last_status = "ok" if ok else "starting"
                c.last_check_at = time.time()
                if not ok:
                    _LOG.info("initial bring-up: %s is down — starting", c.name)
                    await c.restart()
                    c.last_restart_at = time.time()
                    c.restart_grace_until = c.last_restart_at + c.restart_grace_s
                    c.restart_count += 1
            except Exception:
                _LOG.exception("initial bring-up failed for %s", c.name)

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), HEARTBEAT_INTERVAL_S)
                if self._stop.is_set():
                    break
            except asyncio.TimeoutError:
                pass
            await self._tick()

    async def _tick(self) -> None:
        now = time.time()
        in_grace = self.in_grace
        for c in self._components:
            try:
                ok = await c.check()
            except Exception:
                _LOG.exception("check failed for %s", c.name)
                ok = False
            c.last_check_at = now
            if ok:
                if c.last_status != "ok":
                    _LOG.info("%s recovered", c.name)
                c.last_status = "ok"
                c.consecutive_failures = 0
                c.backoff_index = 0
                continue
            if in_grace:
                # Don't accrue failures during boot race. Log soft.
                c.last_status = "warming"
                _LOG.info("%s: still warming (in grace, %.0fs left)",
                          c.name, STARTUP_GRACE_S - (now - self._started_at))
                continue
            if now < c.restart_grace_until:
                # Just restarted this component; let it finish booting
                # before counting failures toward another restart.
                c.last_status = "warming"
                _LOG.info("%s: still warming after restart (%.0fs left)",
                          c.name, c.restart_grace_until - now)
                continue
            c.consecutive_failures += 1
            c.last_status = f"down({c.consecutive_failures})"
            _LOG.warning(
                "%s: heartbeat failed (consecutive=%d)",
                c.name, c.consecutive_failures,
            )
            if c.consecutive_failures < FAILURES_TO_RESTART:
                continue
            # Backoff before restart.
            backoff = RESTART_BACKOFF_S[
                min(c.backoff_index, len(RESTART_BACKOFF_S) - 1)
            ]
            since_last = now - c.last_restart_at
            if since_last < backoff:
                _LOG.info(
                    "%s: in backoff (%.0fs since last restart, need %.0fs)",
                    c.name, since_last, backoff,
                )
                continue
            try:
                await c.restart()
                c.restart_count += 1
                c.last_restart_at = time.time()
                c.restart_grace_until = c.last_restart_at + c.restart_grace_s
                c.consecutive_failures = 0
                c.backoff_index += 1
                _LOG.info("%s restart attempt %d done", c.name, c.restart_count)
            except Exception:
                _LOG.exception("%s restart raised", c.name)


# ---------- /status HTTP endpoint ----------

def make_status_app(supervisor: Supervisor, components: list[Component]) -> web.Application:
    app = web.Application()

    async def status(_: web.Request) -> web.Response:
        return web.json_response({
            "now": time.time(),
            "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
            "in_grace": supervisor.in_grace,
            "components": [c.to_status() for c in components],
        })

    app.router.add_get("/status", status)
    return app


# ---------- entry point ----------

def _setup_logging() -> None:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "supervisor.log"

    fmt = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    _LOG.info("supervisor logging to %s", log_path)


async def _main_async(args) -> None:
    ha = HAContainer()
    brain = BrainServerProcess(
        python_path=Path(args.python) if args.python else _venv_python(),
        prompt_file=Path(args.prompt_file) if args.prompt_file else None,
        voice=args.voice,
        model=args.model,
    )

    components = [
        Component(
            name=ha.name, check=ha.check, restart=ha.restart,
            restart_grace_s=60.0,  # HA serves /manifest.json ~30-60s after docker start
        ),
        Component(
            name=brain.name, check=brain.check, restart=brain.restart,
            restart_grace_s=10.0,  # brain_server prewarms claude + Kokoro at startup
        ),
    ]
    supervisor = Supervisor(components)

    status_app = make_status_app(supervisor, components)
    runner = web.AppRunner(status_app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=STATUS_HTTP_PORT)
    await site.start()
    _LOG.info("status endpoint on http://127.0.0.1:%d/status", STATUS_HTTP_PORT)

    loop = asyncio.get_running_loop()

    def _request_stop():
        _LOG.info("shutdown requested")
        supervisor.stop()

    # SIGINT/SIGTERM trigger graceful stop.
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows asyncio doesn't support add_signal_handler — that's
            # fine, Ctrl+C still works via KeyboardInterrupt at the
            # asyncio.run boundary.
            pass

    try:
        await supervisor.run()
    finally:
        _LOG.info("stopping brain_server before exit")
        await brain.stop()
        await runner.cleanup()


def _kill_supervisor_orphans() -> list[int]:
    """Kill any other supervisor processes before this one starts.

    Without this, a previous supervisor instance left running by an
    earlier launch (e.g. an install-script `-RunNow` that re-registered
    the scheduled task without stopping it) holds port 9999 and the
    new instance crashes on bind. Targeted by exact module path so we
    never touch unrelated python processes.

    Also excludes the current process's parent chain — important on
    Windows where a Task Scheduler launcher process can sit between
    us and the kernel and may carry our cmdline.
    """
    own_pid = os.getpid()
    own_ancestors: set[int] = set()
    try:
        me = psutil.Process(own_pid)
        p = me.parent()
        while p is not None:
            own_ancestors.add(p.pid)
            p = p.parent()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    killed: list[int] = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = p.info["pid"]
            if pid == own_pid or pid in own_ancestors:
                continue
            cmdline = " ".join(p.info.get("cmdline") or [])
            if "deha.voice.supervisor" not in cmdline:
                continue
            _LOG.info("orphan-kill candidate: pid=%d cmdline=%r", pid, cmdline)
            p.kill()
            killed.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Narada voice supervisor")
    parser.add_argument("--python", default="", help="Python interpreter to use for brain_server")
    parser.add_argument("--prompt-file", default="", help="Override system prompt path")
    parser.add_argument("--voice", default="bm_george")
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    _setup_logging()
    killed = _kill_supervisor_orphans()
    if killed:
        _LOG.info("killed %d orphan supervisor process(es): %s", len(killed), killed)
        time.sleep(0.5)  # let Windows release port 9999
    _LOG.info("narada supervisor starting (PID %d)", os.getpid())
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        _LOG.info("interrupted")
    _LOG.info("supervisor exit")


if __name__ == "__main__":
    main()


__all__ = ["Supervisor", "Component", "HAContainer", "BrainServerProcess", "main"]
