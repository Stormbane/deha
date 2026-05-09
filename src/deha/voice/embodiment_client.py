"""Bridge → embodiment device API client.

Wraps an aioesphomeapi APIClient with the high-level state-machine
calls the firmware's narada-embodiment.yaml exposes:

    set_mood, set_speaking, set_phoneme, set_gaze, set_glyph,
    set_layer, clear_face

Failure semantics: this is a presentation channel, not a correctness
channel. A disconnected embodiment device must never break the voice
turn. All methods swallow exceptions and log to stderr.

Phase 2 bridge integration:
docs/plans/embodiment-phase2-design-2026-05-02.md
"""

from __future__ import annotations

import asyncio
import enum
import sys
from typing import Optional

from aioesphomeapi import APIClient


class Mood(enum.IntEnum):
    NEUTRAL = 0
    CURIOUS = 1
    FOCUSED = 2


class Phoneme(enum.IntEnum):
    REST = 0
    AA = 1
    EE = 2
    OH = 3
    MBP = 4


class EmbodimentClient:
    """Optional companion to the voice bridge. None-safe — Pipeline
    can hold a value of either EmbodimentClient or None and the
    methods on EmbodimentClient itself handle a not-yet-connected
    state."""

    def __init__(self, host: str, port: int = 6053) -> None:
        self.host = host
        self.port = port
        self._client: Optional[APIClient] = None
        self._services: dict = {}
        self._connect_lock = asyncio.Lock()
        self._connected = False
        # The phoneme stream pumps at ~12 Hz during speech; rate-limit
        # warnings to one per minute so we don't fill the log on a
        # disconnected device.
        self._last_warn_t = 0.0

    async def connect(self) -> bool:
        async with self._connect_lock:
            if self._connected:
                return True
            try:
                self._client = APIClient(self.host, self.port, password="")
                await asyncio.wait_for(
                    self._client.connect(login=True), timeout=4.0
                )
                _entities, services = await self._client.list_entities_services()
                self._services = {s.name: s for s in services}
                self._connected = True
                print(
                    f"  embodiment: connected to {self.host} "
                    f"({len(self._services)} services)",
                    flush=True,
                )
                return True
            except Exception as exc:
                print(
                    f"  embodiment: connect to {self.host} failed: {exc!r}. "
                    f"Visual presentation disabled this session.",
                    file=sys.stderr,
                )
                self._client = None
                self._services = {}
                self._connected = False
                return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=2.0)
            except Exception:
                pass
        self._client = None
        self._connected = False

    async def _call(self, name: str, args: dict) -> None:
        if not self._connected or self._client is None:
            return
        svc = self._services.get(name)
        if svc is None:
            self._warn_once(f"unknown service: {name}")
            return
        try:
            await self._client.execute_service(svc, args)
        except Exception as exc:
            self._warn_once(f"{name}({args}) failed: {exc!r}")

    def _warn_once(self, msg: str) -> None:
        # One warning per minute is enough to know something's wrong
        # without burying the voice log.
        loop = asyncio.get_event_loop()
        now = loop.time()
        if now - self._last_warn_t > 60.0:
            print(f"  embodiment: {msg}", file=sys.stderr)
            self._last_warn_t = now

    # -- High-level state pushes ------------------------------------

    async def set_mood(self, mood: Mood, snap: bool = False) -> None:
        await self._call("set_mood", {"mood": int(mood), "snap": snap})

    async def set_speaking(self, speaking: bool) -> None:
        await self._call("set_speaking", {"speaking": speaking})

    async def set_phoneme(self, phoneme: Phoneme) -> None:
        await self._call("set_phoneme", {"phoneme": int(phoneme)})

    async def set_gaze(self, gx: float, gy: float) -> None:
        # Clamp to [-1, 1] — firmware also clamps but warn the bridge
        # if it's sending out-of-range values.
        gx = max(-1.0, min(1.0, gx))
        gy = max(-1.0, min(1.0, gy))
        await self._call("set_gaze", {"gx": gx, "gy": gy})

    async def set_glyph(self, visible: bool, x: int = 280, y: int = 30) -> None:
        await self._call(
            "set_glyph", {"visible": visible, "x": x, "y": y}
        )

    async def clear_face(self) -> None:
        await self._call("clear_face", {})


__all__ = ["EmbodimentClient", "Mood", "Phoneme"]
