"""Synthesize a sample line in several Kokoro voices for side-by-side audition.

Outputs WAV files to data/voice_auditions/<voice>.wav. Loads kokoro-onnx
once and reuses the session across voices, so the second-onwards synth
is fast.

Usage:
    python scripts/audition_voices.py
    python scripts/audition_voices.py --line "your custom line"
    python scripts/audition_voices.py --voices af_kore am_puck bf_lily
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
from pathlib import Path

import numpy as np

from deha.voice.kokoro_tts import KokoroTTS


DEFAULT_LINE = (
    "Good morning, traveler. "
    "The dream still remembers what the day forgets."
)

DEFAULT_VOICES = [
    "af_kore",
    "af_jadzia",
    "am_puck",
    "bf_lily",
    "af_aoede",
    # Reference points for comparison:
    "bm_george",   # current default — mature British male
    "af_sky",      # soft American female
]


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Write a mono 16-bit PCM bytes blob as a WAV file."""
    n_samples = len(pcm) // 2
    byte_rate = sample_rate * 2
    block_align = 2
    data_size = len(pcm)
    header = b"RIFF"
    header += struct.pack("<I", 36 + data_size)
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, block_align, 16)
    header += b"data"
    header += struct.pack("<I", data_size)
    path.write_bytes(header + pcm)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--line", default=DEFAULT_LINE)
    parser.add_argument("--voices", nargs="*", default=DEFAULT_VOICES)
    parser.add_argument("--out", type=Path, default=Path("data/voice_auditions"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"line: {args.line!r}")
    print(f"voices: {args.voices}")
    print(f"output: {args.out.resolve()}")
    print()

    # Load Kokoro once. Each KokoroTTS holds its own session; we set
    # the voice per-synth instead of one TTS-per-voice so we don't
    # re-load the model.
    tts = KokoroTTS(voice=args.voices[0])
    await tts._ensure_loaded()
    sr = tts.sample_rate

    for voice in args.voices:
        print(f"  {voice} ...", end=" ", flush=True)
        try:
            samples, _sr = await asyncio.to_thread(
                tts._kokoro.create,
                args.line,
                voice=voice,
                speed=1.0,
                lang="en-us",
            )
            pcm16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16).tobytes()
            out_path = args.out / f"{voice}.wav"
            write_wav(out_path, pcm16, sr)
            print(f"-> {out_path.name} ({len(pcm16) // 2 / sr:.1f}s)")
        except Exception as e:
            print(f"FAILED: {e}")

    print()
    print("Play with: start <voice>.wav   (Windows)")
    print("       or: ffplay <voice>.wav  (cross-platform)")


if __name__ == "__main__":
    asyncio.run(main())
