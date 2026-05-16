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

Voice spec accepts either a single voice name or a blend recipe:
    voice="am_puck"                          # solo
    voice="am_michael:0.5,af_heart:0.5"      # 50/50 blend
    voice=[("am_michael", 0.5), ("af_heart", 0.5)]   # programmatic
    voice="am_michael:1,af_heart:1,hf_alpha:1"  # equal weights, normalize

Blends linearly combine per-voice style vectors (shape (510, 1, 256))
weighted as given. Weights are normalized to sum to 1.0. Pre-computed
once at first load and reused.

The current Narada production voice is the 50/50 blend
am_michael + af_heart (audition winner, 2026-05-10). See
data/voice-blends/2026-05-10/ for the full audition.

Device selection: device="auto" (default) picks CUDA when
onnxruntime-gpu reports it available, else falls back to CPU. Pass
device="cpu" or device="cuda" to force.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import AsyncIterator, Iterable

import numpy as np


_LOG = logging.getLogger("narada.tts.kokoro")


def _register_cuda_dll_dirs() -> list[str]:
    """Make pip-installed NVIDIA libs (cuDNN, cuBLAS, nvrtc) discoverable
    by onnxruntime-gpu on Windows.

    onnxruntime-gpu's CUDA provider needs cudnn64_9.dll and friends on
    the DLL search path. Pip ships them under
    ``site-packages/nvidia/<lib>/bin``, which is NOT on PATH by default.

    Two registrations are required because onnxruntime_providers_cuda.dll
    loads cudnn via its own LoadLibrary call (not via Python's import
    machinery), which bypasses ``os.add_dll_directory``. So we also
    prepend each bin to ``PATH`` — that's what LoadLibrary actually
    consults. Both registrations are safe to apply.

    Returns the list of registered paths (for logging).
    """
    if sys.platform != "win32":
        return []
    registered: list[str] = []
    venv_root = Path(sys.executable).resolve().parent.parent
    nvidia_root = venv_root / "Lib" / "site-packages" / "nvidia"
    if not nvidia_root.is_dir():
        return registered
    bin_dirs: list[str] = []
    for sub in nvidia_root.iterdir():
        bin_dir = sub / "bin"
        if bin_dir.is_dir():
            bin_dirs.append(str(bin_dir))
    for bd in bin_dirs:
        try:
            os.add_dll_directory(bd)
        except (OSError, AttributeError):
            pass
        registered.append(bd)
    if bin_dirs:
        existing = os.environ.get("PATH", "")
        # Only prepend bins that aren't already first-pathed
        new_path = os.pathsep.join(bin_dirs + [existing]) if existing else os.pathsep.join(bin_dirs)
        os.environ["PATH"] = new_path
    return registered


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
# Soft-break clauses inside long sentences so first audio comes out
# faster than waiting for a full period. Comma/semicolon/colon followed
# by whitespace is a good prosody-preserving boundary.
_CLAUSE_SOFT_RE = re.compile(r"(?<=[,;:])\s+")
_MAX_CHUNK_CHARS = 240

# Inline speed control. Narada can prefix a sentence with one of these
# tags; the tag is stripped before synthesis and sets the speed for that
# sentence and all subsequent sentences until another tag (sticky).
#   <slow>     -> 0.85
#   <normal>   -> 1.0
#   <fast>     -> 1.15
#   <speed=N>  -> N, clamped to [_SPEED_MIN, _SPEED_MAX]
# Closing forms (</slow>, </fast>, </normal>, </speed>) also recognized
# and treated as "return to normal" (1.0). Narada emits these out of
# SSML/XML habit even though our scoping is sticky-by-default.
_NAMED_SPEEDS = {"slow": 0.85, "normal": 1.0, "fast": 1.15}
_SPEED_TAG_RE = re.compile(r"<(/?)(slow|normal|fast|speed(?:=\d+(?:\.\d+)?)?)>")
_SPEED_MIN, _SPEED_MAX = 0.5, 2.0


def _consume_speed_tags(text: str, current: float) -> tuple[str, float]:
    """Strip speed tags from `text`, return (cleaned_text, new_speed).

    Multiple tags in one chunk: last-write-wins (later tag overrides
    earlier). `current` is the sticky speed coming in; if no tag is
    present the returned speed equals `current`.

    Closing tags (</slow>, </fast>, etc.) reset to 1.0 regardless of
    which scope they close — our model is "any close = back to normal".
    """
    new_speed = current
    matches = list(_SPEED_TAG_RE.finditer(text))
    if not matches:
        return text, current
    for m in matches:
        is_closing = m.group(1) == "/"
        token = m.group(2)
        if is_closing:
            new_speed = 1.0
        elif token in _NAMED_SPEEDS:
            new_speed = _NAMED_SPEEDS[token]
        elif token.startswith("speed="):
            try:
                new_speed = float(token.split("=", 1)[1])
            except ValueError:
                continue
        else:
            # Bare "speed" (no =N) on open form — ignore silently.
            continue
        new_speed = max(_SPEED_MIN, min(_SPEED_MAX, new_speed))
    cleaned = _SPEED_TAG_RE.sub("", text)
    # Collapse double spaces left behind by stripped tags.
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, new_speed


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


def _parse_voice_spec(
    spec: "str | list[tuple[str, float]]",
) -> list[tuple[str, float]]:
    """Normalize a voice spec to a list of (name, normalized_weight) tuples.

    Accepted forms:
      "am_puck"                         -> [("am_puck", 1.0)]
      "am_michael:0.5,af_heart:0.5"     -> [("am_michael", 0.5), ("af_heart", 0.5)]
      "a:1,b:1,c:1"                     -> three equal-weight (normalized to 1/3 each)
      [("a", 0.5), ("b", 0.5)]          -> passed through, weights normalized

    Weights are normalized so they sum to 1.0.
    """
    components: list[tuple[str, float]]
    if isinstance(spec, str):
        s = spec.strip()
        if not s:
            raise ValueError("empty voice spec")
        if "," not in s and ":" not in s:
            # Plain solo name
            components = [(s, 1.0)]
        else:
            parsed: list[tuple[str, float]] = []
            for part in s.split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    name, weight_str = part.split(":", 1)
                    weight = float(weight_str.strip())
                else:
                    name, weight = part, 1.0
                name = name.strip()
                if not name:
                    raise ValueError(f"voice spec missing name: {part!r}")
                parsed.append((name, weight))
            if not parsed:
                raise ValueError(f"voice spec empty after parse: {spec!r}")
            components = parsed
    else:
        components = [(name, float(weight)) for name, weight in spec]
        if not components:
            raise ValueError("empty voice spec list")
    total = sum(w for _, w in components)
    if total <= 0:
        raise ValueError(
            f"voice spec weights must sum to a positive value: {components!r}"
        )
    return [(name, weight / total) for name, weight in components]


class KokoroTTS:
    """Lazy-loaded Kokoro synth, sentence-chunked streaming.

    Thread-safe at the chunk level (one synth call at a time) — the
    underlying kokoro-onnx Kokoro instance holds an onnxruntime session
    which we don't share across coroutines concurrently.
    """

    def __init__(
        self,
        models_dir: Path | None = None,
        voice: "str | list[tuple[str, float]]" = "am_michael:0.5,af_heart:0.5",
        speed: float = 1.0,
        lang: str = "en-us",
        device: str = "cpu",
    ):
        self._models_dir = models_dir or _default_models_dir()
        self._voice_spec = voice
        self._voice_components = _parse_voice_spec(voice)
        # Display name: solo => the voice id; blend => "name1+name2[+...]"
        if len(self._voice_components) == 1:
            self._voice_display = self._voice_components[0][0]
        else:
            self._voice_display = "+".join(
                n for n, _ in self._voice_components
            )
        self._voice_array: np.ndarray | None = None  # computed at load time
        self._speed = speed
        self._lang = lang
        self._device = device
        self._device_in_use: str | None = None
        self._kokoro = None  # lazy
        self._load_lock = asyncio.Lock()
        self._synth_lock = asyncio.Lock()

    @property
    def voice(self) -> str:
        """Display-only name for Wyoming Info events. For a blend this
        is "name1+name2[+...]"; HA dials Wyoming by program name, not
        voice name, so this label has no functional effect."""
        return self._voice_display

    @property
    def voice_components(self) -> list[tuple[str, float]]:
        """Normalized blend recipe currently in use."""
        return list(self._voice_components)

    @property
    def sample_rate(self) -> int:
        # Kokoro outputs 24 kHz natively. Stable across model versions.
        return 24000

    def _resolve_providers(self) -> list[str]:
        """Pick onnxruntime providers based on self._device.

        auto: CUDA if available, else CPU. cuda/cpu force one or the other.
        """
        import onnxruntime as ort
        available = ort.get_available_providers()
        want = self._device.lower()
        if want == "cpu":
            return ["CPUExecutionProvider"]
        if want == "cuda":
            if "CUDAExecutionProvider" not in available:
                raise RuntimeError(
                    "device='cuda' requested but CUDAExecutionProvider not "
                    f"available. Installed: {available}. Install "
                    "onnxruntime-gpu and ensure CUDA libs are on the path."
                )
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        # auto
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    async def _ensure_loaded(self) -> None:
        if self._kokoro is not None:
            return
        async with self._load_lock:
            if self._kokoro is not None:
                return
            # Register CUDA DLL search paths BEFORE importing onnxruntime,
            # so onnxruntime_providers_cuda.dll can resolve cudnn64_9.dll
            # and friends from the pip-installed nvidia-* packages.
            registered = _register_cuda_dll_dirs()
            if registered:
                _LOG.info("CUDA DLL dirs registered: %s", registered)

            from kokoro_onnx import Kokoro  # local import — heavy deps
            import onnxruntime as ort

            model_path = self._models_dir / "kokoro-v1.0.onnx"
            voices_path = self._models_dir / "voices-v1.0.bin"
            if not model_path.exists() or not voices_path.exists():
                raise FileNotFoundError(
                    f"Kokoro model files not found in {self._models_dir}. "
                    "Download kokoro-v1.0.onnx and voices-v1.0.bin from "
                    "https://github.com/thewh1teagle/kokoro-onnx/releases"
                )

            providers = self._resolve_providers()
            self._device_in_use = (
                "cuda" if providers[0] == "CUDAExecutionProvider" else "cpu"
            )

            def _build() -> "Kokoro":
                session = ort.InferenceSession(
                    str(model_path), providers=providers
                )
                actual = session.get_providers()
                _LOG.info(
                    "kokoro session built: requested=%s actual=%s",
                    providers, actual,
                )
                return Kokoro.from_session(session, str(voices_path))

            self._kokoro = await asyncio.to_thread(_build)

            # Pre-compute the voice vector. Solo voices could pass the
            # name string straight to .create(), but always materializing
            # the vector here gives one code path and lets us log the
            # recipe explicitly.
            voice_arr: np.ndarray | None = None
            for name, weight in self._voice_components:
                v = self._kokoro.get_voice_style(name).astype(np.float32)
                contribution = v * weight
                voice_arr = contribution if voice_arr is None else voice_arr + contribution
            self._voice_array = voice_arr
            recipe = ", ".join(
                f"{n}@{w:.2f}" for n, w in self._voice_components
            )
            _LOG.info("kokoro voice ready: %s", recipe)

    async def synth_chunk(self, text: str, speed: float | None = None) -> bytes:
        """Synthesize a single chunk, return 16-bit PCM bytes.

        speed=None falls back to the instance default (self._speed). Pass
        an explicit value for per-utterance prosody control.

        Speed tags (<slow>/<normal>/<fast>/<speed=N> and closing forms)
        are stripped here so every caller gets consistent behavior — not
        just the streaming iterators. A tag inside `text` overrides the
        explicit `speed` argument (last-write-wins). When called from
        the streaming iterators (which have already consumed tags), the
        strip is a no-op.
        """
        await self._ensure_loaded()
        effective_speed = self._speed if speed is None else speed
        cleaned, effective_speed = _consume_speed_tags(text, effective_speed)
        if not cleaned:
            return b""
        async with self._synth_lock:
            samples, _sr = await asyncio.to_thread(
                self._kokoro.create,
                cleaned,
                voice=self._voice_array,
                speed=effective_speed,
                lang=self._lang,
            )
        # kokoro-onnx returns float32 in [-1, 1].
        pcm16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        return pcm16.tobytes()

    async def keepalive(self) -> bool:
        """Tiny synth to keep CUDA/ORT caches hot.

        On CPU this is a no-op (CPU has no cold-start; the runtime
        guarantees consistent latency). On CUDA, ORT's input-shape-
        dependent kernel selection plus driver-side context eviction
        means an idle GPU pays a 4-10s JIT/autotune penalty on the
        next synth — empirically far worse than CPU's 0.7-1.5s. A
        background task hitting this every ~30s keeps the kernels
        compiled and the workspaces resident.

        Returns True if a synth ran, False if it was a no-op.
        """
        await self._ensure_loaded()
        if self._device_in_use != "cuda":
            return False
        async with self._synth_lock:
            await asyncio.to_thread(
                self._kokoro.create,
                "ok",
                voice=self._voice_array,
                speed=self._speed,
                lang=self._lang,
            )
        return True

    async def stream_text(self, text: str) -> AsyncIterator[bytes]:
        """Synth a fixed string by chunks. Yields PCM bytes per chunk.

        Inline speed tags (<slow>/<normal>/<fast>/<speed=N>) are honored
        and apply sticky from where they appear.
        """
        current_speed = self._speed
        for chunk in _segment(text):
            cleaned, current_speed = _consume_speed_tags(chunk, current_speed)
            if cleaned:
                yield await self.synth_chunk(cleaned, speed=current_speed)

    async def stream_text_iter(
        self, text_chunks: Iterable[str]
    ) -> AsyncIterator[bytes]:
        """Synth a stream of incoming text deltas.

        Buffers incoming deltas until a sentence/clause boundary is
        reached, then synthesizes that segment. Trailing buffer is
        flushed when the iterator ends. Inline speed tags adjust pacing
        sticky from where they appear.
        """
        buf = ""
        current_speed = self._speed
        for delta in text_chunks:
            buf += delta
            # Greedy: emit complete sentences as soon as we see them.
            while True:
                m = _SENTENCE_SPLIT_RE.search(buf)
                if m is None:
                    break
                head, buf = buf[: m.end()].strip(), buf[m.end():]
                cleaned, current_speed = _consume_speed_tags(head, current_speed)
                if cleaned:
                    yield await self.synth_chunk(cleaned, speed=current_speed)
            # Soft-flush long pending clauses so first audio isn't gated
            # on a far-away period.
            if len(buf) > _MAX_CHUNK_CHARS:
                m = list(_CLAUSE_SOFT_RE.finditer(buf))
                if m:
                    cut = m[-1].end()
                    head, buf = buf[:cut].strip(), buf[cut:]
                    cleaned, current_speed = _consume_speed_tags(head, current_speed)
                    if cleaned:
                        yield await self.synth_chunk(cleaned, speed=current_speed)
        tail = buf.strip()
        if tail:
            cleaned, current_speed = _consume_speed_tags(tail, current_speed)
            if cleaned:
                yield await self.synth_chunk(cleaned, speed=current_speed)

    async def stream_async_text_iter(
        self, text_aiter: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """Async-iterator twin of stream_text_iter — for live claude_stream."""
        buf = ""
        current_speed = self._speed
        async for delta in text_aiter:
            buf += delta
            while True:
                m = _SENTENCE_SPLIT_RE.search(buf)
                if m is None:
                    break
                head, buf = buf[: m.end()].strip(), buf[m.end():]
                cleaned, current_speed = _consume_speed_tags(head, current_speed)
                if cleaned:
                    yield await self.synth_chunk(cleaned, speed=current_speed)
            if len(buf) > _MAX_CHUNK_CHARS:
                m = list(_CLAUSE_SOFT_RE.finditer(buf))
                if m:
                    cut = m[-1].end()
                    head, buf = buf[:cut].strip(), buf[cut:]
                    cleaned, current_speed = _consume_speed_tags(head, current_speed)
                    if cleaned:
                        yield await self.synth_chunk(cleaned, speed=current_speed)
        tail = buf.strip()
        if tail:
            cleaned, current_speed = _consume_speed_tags(tail, current_speed)
            if cleaned:
                yield await self.synth_chunk(cleaned, speed=current_speed)


__all__ = ["KokoroTTS"]
