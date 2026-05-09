"""Speech-to-text via faster-whisper.

Whisper's transcribe is a blocking CPU/GPU call. The bridge runs it via
asyncio.to_thread so the event loop can keep servicing aioesphomeapi
keepalive pings (otherwise the device drops the client mid-turn).
"""

from __future__ import annotations

import asyncio

import numpy as np

from .protocol import MIC_RATE


class WhisperSTT:
    def __init__(self, model_name: str = "small.en", device: str = "cuda",
                 compute_type: str = "float16"):
        from faster_whisper import WhisperModel
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    async def transcribe(self, pcm_bytes: bytes) -> str:
        """Transcribe int16 mono PCM at MIC_RATE. Returns decoded text."""
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return await asyncio.to_thread(self._run, audio)

    def _run(self, audio_f32: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio_f32,
            language="en",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()


__all__ = ["WhisperSTT", "MIC_RATE"]
