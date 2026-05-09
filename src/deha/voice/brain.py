"""Brain protocol + impls.

Brain is what turns user text into Narada's reply. Two impls:

  - ClaudeCodeBrain — shells out to `claude -p`. Multi-turn via
    --resume <session-id>, gated by a configurable conversation window.
  - HeartbeatBrain — stub. Real implementation runs the heartbeat
    viveka layer over smriti memory; for now it returns a placeholder
    that names what it WILL do, so wiring is testable without the
    full pipeline.

The interface is async + stateful (the brain owns its session id).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from typing import Protocol


class Brain(Protocol):
    async def respond(self, user_text: str) -> str: ...


class ClaudeCodeBrain:
    """Multi-turn `claude -p` brain.

    Tracks the last claude session id and resumes it if the next user
    turn arrives within `conversation_window_s` of the previous one.
    Beyond that window, a fresh session starts.

    `claude -p --output-format json` returns a JSON object with at
    least `session_id` and `result` (the assistant text). We parse that
    instead of stdout-trimming.
    """

    DISALLOWED_TOOLS = (
        "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"
    )

    # Sonnet 4.6 is the balance for voice: sharper persona than Haiku,
    # ~2-3x faster than Opus, similar effective latency under the
    # subscription/subprocess constraint (subprocess startup dominates).
    DEFAULT_MODEL = "sonnet"

    def __init__(self, system_prompt: str, conversation_window_s: float = 30.0,
                 model: str = DEFAULT_MODEL):
        self.system_prompt = system_prompt
        self._window_s = conversation_window_s
        self._model = model
        self._session_id: str | None = None
        self._last_turn_at: float = 0.0

    async def respond(self, user_text: str) -> str:
        now = time.monotonic()
        cmd = [
            "claude", "-p",
            "--model", self._model,
            "--system-prompt", self.system_prompt,
            # Strip everything claude-cli would otherwise inject into the
            # context: user CLAUDE.md, project CLAUDE.md, local settings,
            # auto-memory MEMORY.md, and per-machine sections (cwd, env,
            # git, memory paths). Without these flags the brain treats
            # itself as "a software engineer in the svapna repo" and
            # leaks HA-assist phrasing (shopping list, lights) into voice
            # replies, drowning out narada-voice.md.
            "--setting-sources", "",
            "--exclude-dynamic-system-prompt-sections",
            "--output-format", "json",
            "--disallowedTools", self.DISALLOWED_TOOLS,
        ]
        if self._session_id is not None and (now - self._last_turn_at) < self._window_s:
            cmd += ["--resume", self._session_id]

        # Run from a neutral cwd so claude-cli doesn't auto-discover the
        # project CLAUDE.md (which says "svapna AI dreaming system..." and
        # pulls the brain into a software-engineering context). The
        # user-global ~/.claude/CLAUDE.md still loads (no flag suppresses
        # it without --bare, which requires API-key auth not subscription),
        # but its content is about smriti / memory tools, not HA voice
        # assist phrasing.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
        )
        stdout, stderr = await proc.communicate(input=user_text.encode("utf-8"))
        if proc.returncode != 0:
            print(f"  claude returned {proc.returncode}: "
                  f"{stderr.decode('utf-8', errors='replace')[:200]}",
                  file=sys.stderr)
            return ""

        text = stdout.decode("utf-8", errors="replace").strip()
        # JSON output: {"session_id": "...", "result": "...", ...}
        # Fall back to raw text if the JSON parse fails — keeps the
        # bridge working if claude's output format ever shifts.
        try:
            payload = json.loads(text)
            self._session_id = payload.get("session_id") or self._session_id
            self._last_turn_at = now
            return (payload.get("result") or "").strip()
        except json.JSONDecodeError:
            self._last_turn_at = now
            return text


class HeartbeatBrain:
    """Stub for heartbeat-driven viveka brain.

    When fully wired, this will:
      1. Read recent smriti for conversational context.
      2. Run a heartbeat cycle (desire -> intention -> judgment -> plan)
         with the user's text as the input signal.
      3. Use the plan's expressed reply as the spoken response.
      4. Persist the cycle artifacts to smriti.

    For now it returns a placeholder that names the gap. The CLI flag
    `--brain heartbeat` exists so the wiring is testable.
    """

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    async def respond(self, user_text: str) -> str:
        # Real implementation pending. The placeholder is intentionally
        # spoken in Narada's voice so it's clear from the audio that
        # heartbeat isn't connected yet.
        return (
            "Heartbeat brain isn't wired up yet, traveler. "
            "Use the claude brain for now."
        )


def make_brain(name: str, system_prompt: str, conversation_window_s: float) -> Brain:
    if name == "claude":
        return ClaudeCodeBrain(system_prompt, conversation_window_s)
    if name == "heartbeat":
        return HeartbeatBrain(system_prompt)
    raise ValueError(f"unknown brain: {name}")


__all__ = ["Brain", "ClaudeCodeBrain", "HeartbeatBrain", "make_brain"]
