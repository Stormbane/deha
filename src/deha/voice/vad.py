"""Silero-based voice activity detection.

Pattern adapted from Home Assistant's assist_pipeline/vad.py
(VoiceCommandSegmenter) — Apache 2.0. Their hangover-buffer logic is
the right shape; we use silero-vad directly rather than pulling HA
as a dependency.

Decision logic:
  - Process audio in 32ms frames (512 samples at 16kHz mono int16).
    silero-vad v5+ requires this exact frame size.
  - silero outputs P(speech) in [0, 1] per frame.
  - is_speech = P > speech_threshold (default 0.5).
  - After speech has been observed, count consecutive non-speech frames.
  - If non-speech duration crosses silence_threshold_s, fire the close
    callback. Reset on any speech frame.
  - Hard cap at max_listen_s regardless of speech state.
"""

from __future__ import annotations

import enum
import time

import numpy as np

from .protocol import VAD_FRAME_BYTES, VAD_FRAME_S, MIC_RATE


class VadVerdict(enum.Enum):
    LISTENING = "listening"
    SILENCE_CLOSED = "silence_closed"
    MAX_LISTEN_HIT = "max_listen_hit"


class SileroVad:
    """Wraps silero-vad with hangover buffer + silence-close decision.

    Construction lazy-imports silero_vad and torch so import cost is
    paid only when voice is actually wanted (not at every test/CLI
    invocation). Tests inject a fake via the pipeline's vad_factory.
    """

    def __init__(
        self,
        speech_threshold: float = 0.5,
        silence_threshold_s: float = 1.0,
        speech_required_s: float = 0.2,
        max_listen_s: float = 8.0,
    ):
        from silero_vad import load_silero_vad
        import torch

        self._model = load_silero_vad()
        self._torch = torch
        self._speech_threshold = speech_threshold
        self._silence_threshold_s = silence_threshold_s
        self._speech_required_s = speech_required_s
        self._max_listen_s = max_listen_s
        self._buf = bytearray()
        self._started_at: float | None = None
        self._speech_seen_s = 0.0
        self._last_speech_at: float | None = None

    def reset(self) -> None:
        self._buf.clear()
        self._started_at = None
        self._speech_seen_s = 0.0
        self._last_speech_at = None
        # silero-vad keeps RNN state across calls; reset for a fresh utterance.
        if hasattr(self._model, "reset_states"):
            self._model.reset_states()

    def process(self, chunk: bytes) -> VadVerdict:
        """Feed PCM bytes (16kHz mono int16). Returns current verdict."""
        if self._started_at is None:
            self._started_at = time.monotonic()
            if hasattr(self._model, "reset_states"):
                self._model.reset_states()
        self._buf.extend(chunk)
        # Slice 32ms frames out of the buffer.
        while len(self._buf) >= VAD_FRAME_BYTES:
            frame_bytes = bytes(self._buf[:VAD_FRAME_BYTES])
            del self._buf[:VAD_FRAME_BYTES]
            arr = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = self._torch.from_numpy(arr)
            prob = float(self._model(tensor, MIC_RATE).item())
            if prob >= self._speech_threshold:
                self._speech_seen_s += VAD_FRAME_S
                self._last_speech_at = time.monotonic()

        now = time.monotonic()
        if (now - self._started_at) > self._max_listen_s:
            return VadVerdict.MAX_LISTEN_HIT
        if (
            self._speech_seen_s >= self._speech_required_s
            and self._last_speech_at is not None
            and (now - self._last_speech_at) > self._silence_threshold_s
        ):
            return VadVerdict.SILENCE_CLOSED
        return VadVerdict.LISTENING


__all__ = ["SileroVad", "VadVerdict"]
