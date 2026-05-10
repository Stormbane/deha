"""Wyoming TTS server backed by Kokoro.

Speaks the Wyoming protocol over TCP so HA's existing wyoming integration
can dial it as a TTS provider — drop-in for wyoming-piper. Runs in the
same process as brain_server so the conversation and TTS layers can
share state cheaply (and so we have one less docker container).

HA flow (non-streaming, complete text per request):
  Describe          -> Info(TtsProgram[TtsVoice])
  Synthesize(text)  -> AudioStart, AudioChunk*, AudioStop

HA flow (streaming, text deltas as conversation generates):
  Describe                -> Info(TtsProgram(supports_synthesize_streaming=True))
  SynthesizeStart(voice)  -> AudioStart   (audio header sent immediately)
  SynthesizeChunk(text)*  -> AudioChunk*  (synthesized as sentence boundaries arrive)
  SynthesizeStop()        -> AudioStop, SynthesizeStopped

Streaming mode is what HA's assist pipeline uses when the conversation
agent has _attr_supports_streaming = True AND the TTS provider
advertises supports_synthesize_streaming = True. Without both, HA
batches conversation deltas before invoking Synthesize, which adds
the full claude latency to user-perceived first-audio.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
)

from .kokoro_tts import KokoroTTS


_LOG = logging.getLogger("narada.tts.wyoming")

CHUNK_SAMPLES = 1024  # ~43 ms at 24 kHz, 16-bit mono


def _build_info(tts: KokoroTTS) -> Info:
    voice = TtsVoice(
        name=tts.voice,
        description=f"Kokoro voice {tts.voice}",
        attribution=Attribution(name="Kokoro", url="https://github.com/hexgrad/kokoro"),
        installed=True,
        version=None,
        languages=["en"],
    )
    program = TtsProgram(
        name="narada-kokoro",
        description="Narada's voice via Kokoro (in-process)",
        attribution=Attribution(name="deha", url=""),
        installed=True,
        version=None,
        voices=[voice],
        # Critical for end-to-end streaming TTS in HA's assist pipeline:
        # this flag tells HA's wyoming integration that we accept
        # SynthesizeChunk events. Without it, HA batches conversation
        # deltas before sending one Synthesize event, defeating the
        # whole point of streaming /converse.
        supports_synthesize_streaming=True,
    )
    return Info(tts=[program])


def _split_pcm(pcm: bytes, frame_bytes: int) -> list[bytes]:
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]


class KokoroTtsHandler(AsyncEventHandler):
    """Per-connection Wyoming handler.

    AsyncEventHandler subclasses are constructed per inbound connection
    by AsyncServer; the shared KokoroTTS instance is injected via class
    attribute so the onnxruntime session loads once.

    Handles both protocol modes:
    - Synthesize (legacy, full text per request)
    - SynthesizeStart / SynthesizeChunk* / SynthesizeStop (streaming)
    """

    tts: KokoroTTS = None  # set by serve_wyoming_tts before serve loop

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Streaming-mode state for this connection
        self._stream_voice_name: str | None = None
        self._stream_text_q: asyncio.Queue[str | None] | None = None
        self._stream_synth_task: asyncio.Task | None = None
        self._stream_chunk_count: int = 0
        self._stream_first_chunk_logged: bool = False
        # Set True between SynthesizeStart and SynthesizeStop. While
        # active, any incoming Synthesize event is treated as a duplicate
        # and dropped: HA's wyoming integration sends BOTH the streaming
        # event sequence AND a legacy Synthesize on the same connection
        # for the same turn — without dedupe, every sentence plays twice.
        self._streaming_active: bool = False

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(_build_info(self.tts).event())
            return True

        if Synthesize.is_type(event.type):
            if self._streaming_active:
                _LOG.info("ignoring legacy Synthesize during active stream")
                return True
            synth = Synthesize.from_event(event)
            text = (synth.text or "").strip()
            voice_name = (synth.voice.name if synth.voice else None) or self.tts.voice
            _LOG.info("synth voice=%s text=%r", voice_name, text[:80])
            if not text:
                return True

            sr = self.tts.sample_rate
            width = 2  # int16
            channels = 1
            frame_bytes = CHUNK_SAMPLES * width * channels

            await self.write_event(
                AudioStart(rate=sr, width=width, channels=channels).event()
            )
            first_chunk_logged = False
            chunk_count = 0
            try:
                async for pcm in self.tts.stream_text(text):
                    for frame in _split_pcm(pcm, frame_bytes):
                        await self.write_event(
                            AudioChunk(
                                rate=sr,
                                width=width,
                                channels=channels,
                                audio=frame,
                            ).event()
                        )
                        chunk_count += 1
                        if not first_chunk_logged:
                            _LOG.info("first_chunk voice=%s", voice_name)
                            first_chunk_logged = True
            finally:
                await self.write_event(AudioStop().event())
                _LOG.info("synth_done voice=%s chunks=%d", voice_name, chunk_count)
            return True

        # ---- streaming mode ----

        if SynthesizeStart.is_type(event.type):
            ss = SynthesizeStart.from_event(event)
            voice_name = (
                (ss.voice.name if ss.voice and ss.voice.name else None)
                or self.tts.voice
            )
            self._stream_voice_name = voice_name
            self._stream_text_q = asyncio.Queue()
            self._stream_chunk_count = 0
            self._stream_first_chunk_logged = False
            self._streaming_active = True
            _LOG.info("synth_stream_start voice=%s", voice_name)

            sr = self.tts.sample_rate
            width = 2
            channels = 1
            await self.write_event(
                AudioStart(rate=sr, width=width, channels=channels).event()
            )
            self._stream_synth_task = asyncio.create_task(self._stream_synth())
            return True

        if SynthesizeChunk.is_type(event.type):
            chunk = SynthesizeChunk.from_event(event)
            text = chunk.text or ""
            if self._stream_text_q is not None and text:
                await self._stream_text_q.put(text)
            return True

        if SynthesizeStop.is_type(event.type):
            voice_name = self._stream_voice_name or "?"
            if self._stream_text_q is not None:
                await self._stream_text_q.put(None)  # sentinel
            if self._stream_synth_task is not None:
                try:
                    await self._stream_synth_task
                except Exception:
                    _LOG.exception("synth_stream task failed")
                self._stream_synth_task = None
            await self.write_event(AudioStop().event())
            await self.write_event(SynthesizeStopped().event())
            _LOG.info(
                "synth_stream_done voice=%s chunks=%d",
                voice_name, self._stream_chunk_count,
            )
            self._stream_text_q = None
            self._stream_voice_name = None
            self._streaming_active = False
            return True

        return True

    async def _stream_synth(self) -> None:
        """Pulls text from the streaming queue, synthesizes per sentence
        boundary via KokoroTTS.stream_async_text_iter, writes AudioChunks
        out the wire as PCM is produced."""
        assert self._stream_text_q is not None
        sr = self.tts.sample_rate
        width = 2
        channels = 1
        frame_bytes = CHUNK_SAMPLES * width * channels

        async def _text_iter():
            assert self._stream_text_q is not None
            while True:
                item = await self._stream_text_q.get()
                if item is None:
                    return
                yield item

        async for pcm in self.tts.stream_async_text_iter(_text_iter()):
            for frame in _split_pcm(pcm, frame_bytes):
                await self.write_event(
                    AudioChunk(
                        rate=sr,
                        width=width,
                        channels=channels,
                        audio=frame,
                    ).event()
                )
                self._stream_chunk_count += 1
                if not self._stream_first_chunk_logged:
                    _LOG.info(
                        "first_chunk voice=%s [streaming]",
                        self._stream_voice_name or "?",
                    )
                    self._stream_first_chunk_logged = True


async def serve_wyoming_tts(tts: KokoroTTS, host: str, port: int) -> None:
    """Run the Wyoming TTS server forever on (host, port)."""
    from wyoming.server import AsyncServer

    KokoroTtsHandler.tts = tts
    server = AsyncServer.from_uri(f"tcp://{host}:{port}")
    _LOG.info("wyoming TTS listening on tcp://%s:%d", host, port)
    await server.run(KokoroTtsHandler)


__all__ = ["serve_wyoming_tts", "KokoroTtsHandler"]
