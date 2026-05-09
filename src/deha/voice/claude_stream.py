"""Persistent claude-cli stream-json sessions.

Each session wraps one long-lived `claude -p --input-format stream-json
--output-format stream-json` subprocess. Multi-turn over stdin/stdout
keeps the prompt cache warm between turns and eliminates the
~3-4s subprocess cold-start that dominated per-turn latency before.

We use sync `subprocess.Popen` + a daemon reader thread that pushes
parsed events onto an asyncio.Queue. asyncio.subprocess + PIPE on
Windows hangs on line-buffered child output (proactor event loop
quirk). The threaded bridge sidesteps that entirely and is the
standard pattern for "long-running child with line-delimited I/O".

Schema (verified 2026-05-08):
  stdin (newline-delimited JSON, one per turn):
    {"type":"user","message":{"role":"user","content":[
      {"type":"text","text":"<user text>"}
    ]}}
  stdout events: system(init), assistant(thinking|text), rate_limit_event,
                 result(subtype:success, result:"<final text>")
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
from typing import Any, AsyncIterator, Optional


_DISALLOWED_TOOLS = (
    "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"
)


class ClaudeStreamSession:
    """One long-lived claude-cli subprocess speaking stream-json."""

    def __init__(self, system_prompt: str, model: str = "sonnet"):
        self._system_prompt = system_prompt
        self._model = model
        self._proc: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()
        self._events: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_buf: list[str] = []

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self) -> None:
        if self.alive:
            return
        cmd = [
            "claude", "-p",
            "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", self._model,
            "--system-prompt", self._system_prompt,
            "--setting-sources", "",
            "--exclude-dynamic-system-prompt-sections",
            "--disallowedTools", _DISALLOWED_TOOLS,
        ]
        loop = asyncio.get_running_loop()
        self._proc = await asyncio.to_thread(
            subprocess.Popen,
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            cwd=tempfile.gettempdir(),
        )
        self._reader_thread = threading.Thread(
            target=self._stdout_loop, args=(loop,), daemon=True,
            name="claude-stream-reader",
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, daemon=True,
            name="claude-stream-stderr",
        )
        self._stderr_thread.start()
        # NB: claude-cli does NOT emit any stdout events until it reads
        # the first stdin message — so we cannot wait for init here.
        # The first send() will see system+assistant+result events flow
        # together; we just consume up to the result.

    def _stdout_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                loop.call_soon_threadsafe(self._events.put_nowait, obj)
        finally:
            # Sentinel = EOF
            loop.call_soon_threadsafe(self._events.put_nowait, None)

    def _stderr_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in self._proc.stderr:
            try:
                self._stderr_buf.append(raw.decode("utf-8", errors="replace"))
            except Exception:
                pass

    async def _wait_for_event(self, event_type: str) -> dict[str, Any]:
        while True:
            obj = await self._events.get()
            if obj is None:
                raise RuntimeError(
                    f"claude-cli stdout closed before {event_type!r} event"
                )
            if obj.get("type") == event_type:
                return obj

    async def send(self, user_text: str) -> str:
        """Send one turn, return the assistant's final text.

        Thin wrapper over stream() — collects chunks and joins. Use
        stream() directly when you want incremental text for TTS.
        """
        chunks: list[str] = []
        async for c in self.stream(user_text):
            chunks.append(c)
        return "".join(chunks)

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """Send one turn, yield text chunks as the assistant produces them.

        The concatenation of all yielded chunks equals the final result
        text. Callers feeding TTS should buffer until a sentence boundary
        so each Kokoro generation has a full clause to phrase.

        Handles both event shapes claude-cli stream-json may emit:
          - delta:    {"type":"assistant","delta":{"type":"text_delta","text":"..."}}
          - snapshot: {"type":"assistant","message":{"content":[{"type":"text","text":"<grow>"}, ...]}}

        For snapshot events we yield the suffix not yet seen, so callers
        never see duplicate text regardless of which shape claude-cli
        chooses on a given version.
        """
        async with self._lock:
            if not self.alive:
                await self.start()
            assert self._proc is not None and self._proc.stdin is not None
            payload = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": user_text}],
                },
            }
            line = (json.dumps(payload) + "\n").encode("utf-8")
            await asyncio.to_thread(self._proc.stdin.write, line)
            await asyncio.to_thread(self._proc.stdin.flush)

            accumulated = ""
            while True:
                obj = await self._events.get()
                if obj is None:
                    raise RuntimeError(
                        "claude-cli stdout closed before result event"
                    )
                t = obj.get("type")
                if t == "assistant":
                    # Delta form first.
                    delta_obj = obj.get("delta") or {}
                    delta_text = ""
                    if delta_obj.get("type") in ("text_delta", "text"):
                        delta_text = delta_obj.get("text", "") or ""
                    if delta_text:
                        accumulated += delta_text
                        yield delta_text
                        continue
                    # Snapshot form: extract concatenated text and diff.
                    msg = obj.get("message") or {}
                    blocks = msg.get("content") or []
                    full = "".join(
                        b.get("text", "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    if len(full) > len(accumulated) and full.startswith(accumulated):
                        suffix = full[len(accumulated):]
                        accumulated = full
                        yield suffix
                    elif full and full != accumulated:
                        # Snapshot disagrees with what we've yielded
                        # (rare — model retracted earlier text). Trust
                        # the snapshot as authoritative going forward.
                        accumulated = full
                elif t == "result":
                    final = obj.get("result") or ""
                    if len(final) > len(accumulated) and final.startswith(accumulated):
                        yield final[len(accumulated):]
                    return

    async def close(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                await asyncio.to_thread(self._proc.wait, timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                await asyncio.to_thread(self._proc.wait)
        self._proc = None


__all__ = ["ClaudeStreamSession"]
