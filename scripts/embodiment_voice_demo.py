#!/usr/bin/env python
"""Replay a fake voice turn against the embodiment device.

Demonstrates the bridge → embodiment integration end-to-end without
requiring the voice firmware. The script:

  1. Connects to narada-embodiment.yaml at HOST:6053.
  2. Plays a "wake" beat — mood snap to curious, gaze up, glyph on.
  3. Plays a "think" beat — mood eased to focused, gaze down.
  4. Synthesizes one Piper sentence and walks the resulting PCM
     through PhonemeMapper at 60 ms per chunk, pushing set_phoneme
     calls. set_speaking on at start, off at end. The visual mouth
     follows the audio amplitude, simulating what the bridge does
     during streaming TTS.
  5. Plays a "settle" beat — back to neutral, gaze centered, glyph off.

Usage:

    HOST=192.168.86.35 python scripts/embodiment_voice_demo.py
    # or pass --text "your sentence here"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Allow `from deha.voice...` when the script is run by
# path, not as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from deha.voice.embodiment_client import (  # noqa: E402
    EmbodimentClient, Mood, Phoneme,
)
from deha.voice.phoneme_mapper import PhonemeMapper  # noqa: E402


CHUNK_MS = 60
DEFAULT_TEXT = (
    "Hello. I am Narada. This is the first time my voice and my "
    "face are running through the same pipeline."
)


async def play_speech(emb: EmbodimentClient, text: str) -> None:
    """Synthesize text via Piper and stream PCM through the phoneme
    mapper, pushing set_phoneme to the device at chunk cadence."""
    from deha.voice.tts import PiperTTS, resample_pcm_to_16k
    from deha.voice.config import (
        DEFAULT_PIPER_MODEL_DIR, DEFAULT_PIPER_VOICE,
        DEFAULT_PIPER_SPEAKER_ID, DEFAULT_PIPER_VOLUME,
    )

    print("loading piper voice...", flush=True)
    t0 = time.monotonic()
    tts = PiperTTS(
        DEFAULT_PIPER_MODEL_DIR,
        DEFAULT_PIPER_VOICE,
        DEFAULT_PIPER_SPEAKER_ID,
        DEFAULT_PIPER_VOLUME,
    )
    print(f"  loaded in {time.monotonic() - t0:.1f}s", flush=True)

    mapper = PhonemeMapper()
    src_rate = tts.sample_rate
    chunk_samples_16k = int(16000 * CHUNK_MS / 1000)
    chunk_bytes_16k = chunk_samples_16k * 2

    print(f"speaking: {text!r}", flush=True)
    await emb.set_speaking(True)
    async for sentence_pcm in tts.synthesize_streaming(text):
        pcm_16k = await asyncio.to_thread(
            resample_pcm_to_16k, sentence_pcm, src_rate
        )
        for i in range(0, len(pcm_16k), chunk_bytes_16k):
            chunk = pcm_16k[i:i + chunk_bytes_16k]
            ph = mapper.map_chunk(chunk)
            await emb.set_phoneme(ph)
            # Pace at chunk duration so the mouth tracks audio time.
            # No actual audio playback in this demo — phonemes only.
            await asyncio.sleep(CHUNK_MS / 1000)
    await emb.set_speaking(False)
    await emb.set_phoneme(Phoneme.REST)


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=os.environ.get("HOST", "192.168.86.35"))
    p.add_argument("--port", type=int, default=6053)
    p.add_argument("--text", default=DEFAULT_TEXT)
    p.add_argument("--no-speech", action="store_true",
                   help="skip the Piper synthesis beat (just mood arc)")
    args = p.parse_args()

    emb = EmbodimentClient(args.host, args.port)
    if not await emb.connect():
        return 2

    try:
        # 1. WAKE — curious snap, gaze up, glyph on.
        print("\nbeat 1: wake (curious / gaze up / glyph on)", flush=True)
        await emb.set_mood(Mood.CURIOUS, snap=True)
        await emb.set_gaze(0.0, -0.3)
        await emb.set_glyph(True, 280, 30)
        await asyncio.sleep(1.0)

        # 2. THINK — focused, gaze drops.
        print("beat 2: think (focused / gaze down)", flush=True)
        await emb.set_mood(Mood.FOCUSED)  # smooth through neutral
        await emb.set_gaze(0.0, 0.2)
        await asyncio.sleep(1.0)

        # 3. SPEAK — phoneme stream from Piper.
        if not args.no_speech:
            print("beat 3: speak (phoneme stream)", flush=True)
            await play_speech(emb, args.text)
        else:
            print("beat 3: skipped (--no-speech)", flush=True)

        # 4. SETTLE — neutral, gaze centered, glyph off.
        print("beat 4: settle (neutral / centered / glyph off)", flush=True)
        await emb.set_mood(Mood.NEUTRAL)
        await emb.set_gaze(0.0, 0.0)
        await emb.set_glyph(False)
        await asyncio.sleep(1.5)
    finally:
        await emb.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
