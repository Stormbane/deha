"""Utter pipeline — one-shot speech requests routed to the BOX-3.

Distinct from the conversation path (HA → /converse → Wyoming TTS).
Used by anyone who wants Narada to speak something proactively:
heartbeat CHECK_IN, signal-integration messages, manual triggers,
the narada-speak MCP server.

Architecture:

    POST /utter {text, source, priority}
       │
       ▼
    UtteranceQueue (in-process asyncio.Queue, FIFO with priority skip)
       │
       ▼
    voice_mediator background task
       │
       ├── KokoroTTS.synth_chunk(text)        # WAV bytes
       ├── TTSServer.write(wav_bytes)         # → http://host:port/tts/{uuid}.wav
       └── APIClient.media_player_command(
              media_url=url, announcement=True)  # BOX-3 fetches and plays

Serialization: only one utterance plays at a time. Higher-priority
items (priority>5) jump the queue but do not interrupt audio already
in flight on the BOX-3 — interruption is a separate concern handled
on the device side.

Coordination with the conversation path is intentionally absent at v1:
HA's TTS pipeline and our utter pipeline both write to the BOX-3's
media_player; the BOX-3 itself decides what to play (announcement
mode replaces current). If contention becomes a real problem, we add
state.db esp32.speaking coordination then.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aioesphomeapi import APIClient, MediaPlayerCommand

from .http_server import TTSServer, local_ip

if TYPE_CHECKING:
    from .kokoro_tts import KokoroTTS

_LOG = logging.getLogger("narada.utter")


@dataclass(order=True)
class _Utterance:
    # Higher priority sorts FIRST when negated; same priority -> FIFO by enqueued_at
    sort_key: tuple = field(init=False, repr=False)
    text: str = field(compare=False)
    source: str = field(compare=False)
    priority: int = field(compare=False, default=1)
    request_id: str = field(
        compare=False, default_factory=lambda: uuid.uuid4().hex[:12]
    )
    enqueued_at: float = field(
        compare=False, default_factory=time.monotonic
    )

    def __post_init__(self) -> None:
        # Negative priority so PriorityQueue.get() returns the highest first.
        self.sort_key = (-self.priority, self.enqueued_at)


class UtteranceQueue:
    """In-process priority queue of pending utterances.

    Wraps asyncio.PriorityQueue. Higher priority = sooner. Within the
    same priority bucket, items drain in FIFO order via the enqueued_at
    secondary sort key.
    """

    def __init__(self) -> None:
        self._q: asyncio.PriorityQueue[_Utterance] = asyncio.PriorityQueue()
        self._depth = 0

    @property
    def depth(self) -> int:
        return self._depth

    async def put(
        self, text: str, source: str, priority: int = 1
    ) -> _Utterance:
        item = _Utterance(text=text, source=source, priority=priority)
        await self._q.put(item)
        self._depth += 1
        return item

    async def get(self) -> _Utterance:
        item = await self._q.get()
        self._depth -= 1
        return item


# WAV synthesis helpers. KokoroTTS.synth_chunk returns raw 16-bit PCM
# at 24 kHz; the BOX-3's announcement_pipeline plays WAV. We wrap the
# PCM in a minimal RIFF header here.

_SAMPLE_RATE = 24000
_BITS_PER_SAMPLE = 16
_CHANNELS = 1


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Wrap raw 16-bit mono PCM at 24 kHz in a RIFF/WAV container."""
    byte_rate = _SAMPLE_RATE * _CHANNELS * _BITS_PER_SAMPLE // 8
    block_align = _CHANNELS * _BITS_PER_SAMPLE // 8
    data_size = len(pcm_bytes)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ", 16, 1, _CHANNELS, _SAMPLE_RATE,
        byte_rate, block_align, _BITS_PER_SAMPLE,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + pcm_bytes
    riff = b"RIFF" + struct.pack("<I", 4 + len(fmt_chunk) + len(data_chunk)) + b"WAVE"
    return riff + fmt_chunk + data_chunk


# ---------- BOX-3 media player ----------


class BoxSpeaker:
    """Wraps an aioesphomeapi APIClient connected to the BOX-3 and
    discovers its media_player entity key once.

    Stateless reconnect on failure: each say() opens, plays, and lets
    the connection idle. Long-lived single connection would be slightly
    more efficient but caching APIClient across the asyncio loop has
    burned us before (see DisplayClient comments). Keep simple.
    """

    def __init__(
        self,
        device_ip: str,
        port: int = 6053,
        password: str = "",
        connect_timeout: float = 5.0,
    ) -> None:
        self.device_ip = device_ip
        self.port = port
        self.password = password
        self.connect_timeout = connect_timeout
        self._media_player_key: int | None = None
        self._key_lock = asyncio.Lock()

    async def _resolve_media_player_key(self, client: APIClient) -> int:
        """Find the media_player entity key. Cached after first lookup."""
        async with self._key_lock:
            if self._media_player_key is not None:
                return self._media_player_key
            entities, _services = await client.list_entities_services()
            for e in entities:
                tname = type(e).__name__.lower()
                if "mediaplayer" in tname or "media_player" in tname:
                    self._media_player_key = e.key
                    _LOG.info(
                        "BOX-3 media_player: key=%s name=%r",
                        e.key, getattr(e, "name", "?"),
                    )
                    return e.key
            raise RuntimeError("BOX-3 has no media_player entity")

    async def play_url(self, url: str) -> None:
        """Connect, send play_media announcement command, disconnect."""
        client = APIClient(
            address=self.device_ip,
            port=self.port,
            password=self.password,
        )
        await asyncio.wait_for(client.connect(login=True), timeout=self.connect_timeout)
        try:
            key = await self._resolve_media_player_key(client)
            client.media_player_command(
                key, media_url=url, announcement=True,
            )
            # The native API call returns immediately. The BOX-3
            # fetches the WAV asynchronously and starts playback when
            # decoded.
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


# ---------- voice mediator ----------


class VoiceMediator:
    """Background task that drains UtteranceQueue and plays each item.

    Owns the TTSServer and BoxSpeaker. Lifecycle: start() spawns the
    drain task; stop() cancels it and shuts the TTSServer.
    """

    def __init__(
        self,
        tts: "KokoroTTS",
        queue: UtteranceQueue,
        device_ip: str,
        device_password: str = "",
        audio_host: str | None = None,
        audio_port: int = 8767,
    ) -> None:
        self._tts = tts
        self._queue = queue
        self._speaker = BoxSpeaker(device_ip, password=device_password)
        host = audio_host or local_ip(device_ip)
        self._tts_server = TTSServer(host=host, port=audio_port)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._tts_server.start()
        _LOG.info(
            "utter mediator: serving audio at http://%s:%d/tts/",
            self._tts_server.host, self._tts_server.port,
        )
        self._task = asyncio.create_task(self._run(), name="voice-mediator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._tts_server.stop()

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            t0 = time.monotonic()
            try:
                pcm = await self._tts.synth_chunk(item.text)
                wav = _pcm_to_wav(pcm)
                url = self._tts_server.write(wav, ext=".wav")
                synth_ms = int((time.monotonic() - t0) * 1000)
                await self._speaker.play_url(url)
                total_ms = int((time.monotonic() - t0) * 1000)
                _LOG.info(
                    "[utter] id=%s src=%s synth=%dms dispatch=%dms text=%r",
                    item.request_id, item.source, synth_ms, total_ms,
                    item.text[:80],
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception(
                    "[utter] id=%s src=%s failed",
                    item.request_id, item.source,
                )
            # Estimate playback duration to space out next item — the
            # BOX-3 announcement queue gets confused by overlapping
            # requests. 24kHz mono 16-bit = 48000 bytes/sec.
            try:
                est_play_s = max(1.0, len(pcm) / 48000.0)
                # Add a small tail margin so the next item doesn't step
                # on the previous one's decode.
                await asyncio.sleep(est_play_s + 0.4)
            except Exception:
                pass


__all__ = [
    "UtteranceQueue",
    "VoiceMediator",
    "BoxSpeaker",
]
