"""Wyoming TTS server backed by Kokoro.

Speaks the Wyoming protocol over TCP so HA's existing wyoming integration
can dial it as a TTS provider — drop-in for wyoming-piper. Runs in the
same process as brain_server so the conversation and TTS layers can
share state cheaply (and so we have one less docker container).

HA flow:
  Describe          -> Info(TtsProgram[TtsVoice])
  Synthesize(text)  -> AudioStart, AudioChunk*, AudioStop
"""

from __future__ import annotations

import logging
from typing import Any

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize

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
        attribution=Attribution(name="svapna", url=""),
        installed=True,
        version=None,
        voices=[voice],
    )
    return Info(tts=[program])


def _split_pcm(pcm: bytes, frame_bytes: int) -> list[bytes]:
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]


class KokoroTtsHandler(AsyncEventHandler):
    """Per-connection Wyoming handler.

    AsyncEventHandler subclasses are constructed per inbound connection
    by AsyncServer; the shared KokoroTTS instance is injected via class
    attribute so the onnxruntime session loads once.
    """

    tts: KokoroTTS = None  # set by serve_wyoming_tts before serve loop

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(_build_info(self.tts).event())
            return True

        if Synthesize.is_type(event.type):
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
            finally:
                await self.write_event(AudioStop().event())
            return True

        return True


async def serve_wyoming_tts(tts: KokoroTTS, host: str, port: int) -> None:
    """Run the Wyoming TTS server forever on (host, port)."""
    from wyoming.server import AsyncServer

    KokoroTtsHandler.tts = tts
    server = AsyncServer.from_uri(f"tcp://{host}:{port}")
    _LOG.info("wyoming TTS listening on tcp://%s:%d", host, port)
    await server.run(KokoroTtsHandler)


__all__ = ["serve_wyoming_tts", "KokoroTtsHandler"]
