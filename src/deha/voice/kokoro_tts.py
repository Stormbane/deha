"""Kokoro TTS — native ONNX, sentence-streamed.

Wraps kokoro-onnx so brain_server can synthesize Narada's voice in-process
instead of running wyoming-piper in a separate container. Generation runs
on a background thread to keep the asyncio loop free for HA Wyoming I/O.

Usage:
    tts = KokoroTTS()  # lazy-loads the model on first call
    async for pcm in tts.stream(text_chunks):
        # pcm: bytes, 16-bit mono little-endian, tts.sample_rate Hz
        ...

Model files (download once into models/kokoro/):
    kokoro-v1.0.onnx           — main model (~310 MB)
    voices-v1.0.bin            — voice pack
    https://github.com/thewh1teagle/kokoro-onnx/releases

Voice ids worth auditioning for Narada (sage-register fit):
    bm_george, bm_lewis        — British male, weighty
    am_michael                 — American male, warm
    af_sky                     — American female, soft
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import AsyncIterator, Iterable

import numpy as np


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
# Soft-break clauses inside long sentences so first audio comes out
# faster than waiting for a full period. Comma/semicolon/colon followed
# by whitespace is a good prosody-preserving boundary.
_CLAUSE_SOFT_RE = re.compile(r"(?<=[,;:])\s+")
_MAX_CHUNK_CHARS = 240


def _segment(text: str) -> list[str]:
    """Split text into TTS-friendly chunks.

    Hard-splits on sentence terminators. If a single sentence is longer
    than _MAX_CHUNK_CHARS, soft-splits on clause boundaries so first-audio
    latency stays bounded.
    """
    out: list[str] = []
    for sent in _SENTENCE_SPLIT_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= _MAX_CHUNK_CHARS:
            out.append(sent)
            continue
        parts = _CLAUSE_SOFT_RE.split(sent)
        buf = ""
        for p in parts:
            if not buf:
                buf = p
            elif len(buf) + 1 + len(p) <= _MAX_CHUNK_CHARS:
                buf = f"{buf} {p}"
            else:
                out.append(buf)
                buf = p
        if buf:
            out.append(buf)
    return out


def _default_models_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "models" / "kokoro"
        if (parent / "pyproject.toml").exists():
            return candidate
    return Path("models/kokoro")


class KokoroTTS:
    """Lazy-loaded Kokoro synth, sentence-chunked streaming.

    Thread-safe at the chunk level (one synth call at a time) — the
    underlying kokoro-onnx Kokoro instance holds an onnxruntime session
    which we don't share across coroutines concurrently.
    """

    def __init__(
        self,
        models_dir: Path | None = None,
        voice: str = "bm_george",
        speed: float = 1.0,
        lang: str = "en-gb",
    ):
        self._models_dir = models_dir or _default_models_dir()
        self._voice = voice
        self._speed = speed
        self._lang = lang
        self._kokoro = None  # lazy
        self._load_lock = asyncio.Lock()
        self._synth_lock = asyncio.Lock()

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def sample_rate(self) -> int:
        # Kokoro outputs 24 kHz natively. Stable across model versions.
        return 24000

    async def _ensure_loaded(self) -> None:
        if self._kokoro is not None:
            return
        async with self._load_lock:
            if self._kokoro is not None:
                return
            from kokoro_onnx import Kokoro  # local import — heavy deps

            model_path = self._models_dir / "kokoro-v1.0.onnx"
            voices_path = self._models_dir / "voices-v1.0.bin"
            if not model_path.exists() or not voices_path.exists():
                raise FileNotFoundError(
                    f"Kokoro model files not found in {self._models_dir}. "
                    "Download kokoro-v1.0.onnx and voices-v1.0.bin from "
                    "https://github.com/thewh1teagle/kokoro-onnx/releases"
                )
            self._kokoro = await asyncio.to_thread(
                Kokoro, str(model_path), str(voices_path)
            )

    async def synth_chunk(self, text: str) -> bytes:
        """Synthesize a single chunk, return 16-bit PCM bytes."""
        await self._ensure_loaded()
        async with self._synth_lock:
            samples, _sr = await asyncio.to_thread(
                self._kokoro.create,
                text,
                voice=self._voice,
                speed=self._speed,
                lang=self._lang,
            )
        # kokoro-onnx returns float32 in [-1, 1].
        pcm16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        return pcm16.tobytes()

    async def stream_text(self, text: str) -> AsyncIterator[bytes]:
        """Synth a fixed string by chunks. Yields PCM bytes per chunk."""
        for chunk in _segment(text):
            yield await self.synth_chunk(chunk)

    async def stream_text_iter(
        self, text_chunks: Iterable[str]
    ) -> AsyncIterator[bytes]:
        """Synth a stream of incoming text deltas.

        Buffers incoming deltas until a sentence/clause boundary is
        reached, then synthesizes that segment. Trailing buffer is
        flushed when the iterator ends.
        """
        buf = ""
        for delta in text_chunks:
            buf += delta
            # Greedy: emit complete sentences as soon as we see them.
            while True:
                m = _SENTENCE_SPLIT_RE.search(buf)
                if m is None:
                    break
                head, buf = buf[: m.end()].strip(), buf[m.end():]
                if head:
                    yield await self.synth_chunk(head)
            # Soft-flush long pending clauses so first audio isn't gated
            # on a far-away period.
            if len(buf) > _MAX_CHUNK_CHARS:
                m = list(_CLAUSE_SOFT_RE.finditer(buf))
                if m:
                    cut = m[-1].end()
                    head, buf = buf[:cut].strip(), buf[cut:]
                    if head:
                        yield await self.synth_chunk(head)
        tail = buf.strip()
        if tail:
            yield await self.synth_chunk(tail)

    async def stream_async_text_iter(
        self, text_aiter: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """Async-iterator twin of stream_text_iter — for live claude_stream."""
        buf = ""
        async for delta in text_aiter:
            buf += delta
            while True:
                m = _SENTENCE_SPLIT_RE.search(buf)
                if m is None:
                    break
                head, buf = buf[: m.end()].strip(), buf[m.end():]
                if head:
                    yield await self.synth_chunk(head)
            if len(buf) > _MAX_CHUNK_CHARS:
                m = list(_CLAUSE_SOFT_RE.finditer(buf))
                if m:
                    cut = m[-1].end()
                    head, buf = buf[:cut].strip(), buf[cut:]
                    if head:
                        yield await self.synth_chunk(head)
        tail = buf.strip()
        if tail:
            yield await self.synth_chunk(tail)


__all__ = ["KokoroTTS"]
