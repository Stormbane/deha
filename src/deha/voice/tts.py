"""Piper TTS with sentence segmentation for streaming.

Two synthesis modes:

  - `synthesize(text)` — full utterance, returns WAV bytes. Used for
    URL-fetched playback through media_player's announcement_pipeline.
  - `synthesize_streaming(text)` — async generator, yields PCM bytes
    sentence by sentence. Used for low-latency playback via the API
    audio path (send_voice_assistant_audio).

Piper voices ship as `<voice>.onnx` + `<voice>.onnx.json` files. The
laptop downloads one on first run if missing.
"""

from __future__ import annotations

import asyncio
import io
import re
import wave
from pathlib import Path
from typing import AsyncIterator

import numpy as np


# The BOX-3's announcement_pipeline runs at 48 kHz; ESPHome's pipeline
# does NOT auto-resample — feeding a 22050 Hz WAV to a 48 kHz I2S clock
# either plays at 0.46x speed or stalls the buffer-prefill gate. Piper
# medium voices output 22050 Hz, so we upsample server-side before
# writing the WAV. ([speaker_media_player audio_pipeline.cpp,
# resampler PR #8169 — March 2025 — opt-in only])
DEVICE_SAMPLE_RATE = 48000


# Sentence boundary: split on . ! ? — but only when followed by space
# or end-of-string. Avoids mis-splitting "e.g." or "1.5". Keeps the
# trailing punctuation with the sentence (Piper handles the cadence).
_SENTENCE_RE = re.compile(r"(?<=[\.\!\?])\s+")


def split_sentences(text: str) -> list[str]:
    """Greedy sentence split. Conservative — better to under-split (a
    chunk synthesizes fine) than over-split (lose prosody)."""
    text = text.strip()
    if not text:
        return []
    parts = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    return parts or [text]


class PiperTTS:
    def __init__(self, model_dir: Path, voice: str, speaker_id: int | None = None,
                 volume: float = 1.0, length_scale: float = 1.0):
        from piper import PiperVoice
        onnx = model_dir / f"{voice}.onnx"
        config = model_dir / f"{voice}.onnx.json"
        if not onnx.exists():
            raise FileNotFoundError(
                f"piper voice not found: {onnx}. "
                f"Download via: python -m piper.download_voices {voice} "
                f"--data-dir {model_dir}"
            )
        self._voice = PiperVoice.load(str(onnx), str(config) if config.exists() else None)
        self._speaker_id = speaker_id
        self._volume = volume
        self._length_scale = length_scale
        self._syn_config = self._make_syn_config(speaker_id, volume, length_scale)

    @staticmethod
    def _make_syn_config(speaker_id: int | None, volume: float,
                         length_scale: float):
        """Build a SynthesisConfig if any non-default param is set.
        length_scale > 1.0 = slower speech, < 1.0 = faster."""
        if speaker_id is None and volume == 1.0 and length_scale == 1.0:
            return None
        from piper.config import SynthesisConfig
        kwargs = {}
        if speaker_id is not None:
            kwargs["speaker_id"] = speaker_id
        if volume != 1.0:
            kwargs["volume"] = volume
        if length_scale != 1.0:
            kwargs["length_scale"] = length_scale
        return SynthesisConfig(**kwargs)

    @property
    def sample_rate(self) -> int:
        # piper exposes the model's native sample rate via config.
        cfg = getattr(self._voice, "config", None)
        if cfg is not None and hasattr(cfg, "sample_rate"):
            return int(cfg.sample_rate)
        return 22050  # piper medium-voice default

    async def synthesize(self, text: str) -> bytes:
        """Full-utterance WAV synthesis at Piper's native sample rate.
        Earlier we upsampled to 48 kHz to "match the device" — but the
        linear interpolation produced an audible crackle on
        plosives/sibilants and the actual playback stall turned out
        to be the HTTP reader-FINISHED gate, not a rate mismatch.
        Reverting to native rate; trust the device to resample
        cleanly via I2S clock adjust."""
        return await asyncio.to_thread(self._run_wav, text)

    def _run_wav(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            if self._syn_config is not None:
                self._voice.synthesize_wav(text, wav, syn_config=self._syn_config)
            else:
                self._voice.synthesize_wav(text, wav)
        return buf.getvalue()

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """Sentence-by-sentence PCM synthesis. Each yield is raw int16
        PCM bytes at self.sample_rate (no WAV header)."""
        for sentence in split_sentences(text):
            pcm = await asyncio.to_thread(self._run_pcm, sentence)
            if pcm:
                yield pcm

    def _run_pcm(self, text: str) -> bytes:
        chunks: list[bytes] = []
        # piper exposes synthesize() as an iterable of audio chunks; the
        # exact return shape varies by version. Try the modern API first
        # (yields AudioChunk with .audio_int16_bytes), fall back to raw
        # numpy float arrays.
        kwargs = {"syn_config": self._syn_config} if self._syn_config else {}
        for chunk in self._voice.synthesize(text, **kwargs):
            if hasattr(chunk, "audio_int16_bytes"):
                chunks.append(chunk.audio_int16_bytes)
            elif hasattr(chunk, "audio_float_array"):
                arr = np.asarray(chunk.audio_float_array)
                pcm = (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                chunks.append(pcm)
            elif isinstance(chunk, (bytes, bytearray)):
                chunks.append(bytes(chunk))
        return b"".join(chunks)


def resample_pcm(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample int16 mono PCM by linear interpolation. Piper voices
    are already band-limited for their target rate so anti-alias
    filtering is not strictly required for either direction."""
    if src_rate == dst_rate or not pcm_bytes:
        return pcm_bytes
    arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    n_out = int(round(arr.size * dst_rate / src_rate))
    x_in = np.linspace(0.0, 1.0, num=arr.size, endpoint=False)
    x_out = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    resampled = np.interp(x_out, x_in, arr)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def resample_pcm_to_16k(pcm_bytes: bytes, src_rate: int) -> bytes:
    """Backwards-compatible wrapper used by the streaming TTS path."""
    return resample_pcm(pcm_bytes, src_rate, 16000)


def clean_for_voice(text: str) -> str:
    """Strip emoji, markdown asterisks, code fences — anything Piper
    would pronounce literally or stumble on."""
    import unicodedata
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N", "Z") or cat in (
            "Pd", "Po", "Pi", "Pf", "Ps", "Pe", "Pc", "Sc"
        ):
            out.append(ch)
        elif ch in "\n\t":
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


__all__ = [
    "PiperTTS",
    "split_sentences",
    "clean_for_voice",
    "resample_pcm",
    "resample_pcm_to_16k",
    "DEVICE_SAMPLE_RATE",
]
