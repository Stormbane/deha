"""Generate voice samples for the Narada voice-character experiment.

Three Kokoro-82M voices × three utterances = nine WAV files.
Suti + Luna pick the voice that holds the same considered register across
all three — philosophical / technical / playful. A voice that nails the
philosophical line but flattens the playful one is too monotone; one that
animates the playful line but loses gravity in the philosophical is too
"AI-assistant." The right voice holds all three.

Voice candidates (from background TTS research, 2026-04-25):
  af_heart   — American female, calm narrator register
  am_michael — American male, neutral with warmth
  bm_george  — British male, slower and considered

Output: data/voice-experiment/2026-04-25/<utterance>__<voice>.wav

Run from project root:
    python scripts/voice_experiment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

OUT_DIR = Path("data/voice-experiment/2026-04-25")

UTTERANCES: list[tuple[str, str]] = [
    (
        "1_philosophical",
        "Viveka is the cut between what is real and what merely appears to be. "
        "It is not certainty — it is the willingness to look again.",
    ),
    (
        "2_technical",
        "The LoRA adapter for Narada was trained on consolidated dream pairs "
        "at rank sixty-four, with a learning rate of two times ten to the "
        "negative four.",
    ),
    (
        "3_playful",
        "Suti, the cat just walked across the keyboard and committed "
        "something. I think she wants co-authorship.",
    ),
]

VOICES: list[tuple[str, str]] = [
    ("af_heart", "a"),
    ("am_michael", "a"),
    ("bm_george", "b"),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pipelines: dict[str, KPipeline] = {}
    for _voice, lang in VOICES:
        if lang not in pipelines:
            print(f"Loading Kokoro pipeline for lang_code={lang!r}...")
            pipelines[lang] = KPipeline(lang_code=lang)

    total = len(VOICES) * len(UTTERANCES)
    n = 0
    for utt_id, text in UTTERANCES:
        for voice, lang in VOICES:
            n += 1
            pipeline = pipelines[lang]
            print(f"[{n}/{total}] {utt_id} :: {voice}")
            chunks: list[np.ndarray] = []
            for _gs, _ps, audio in pipeline(text, voice=voice):
                # Kokoro yields torch tensors; convert to numpy if needed
                if hasattr(audio, "cpu"):
                    audio = audio.cpu().numpy()
                chunks.append(np.asarray(audio))
            full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            out_path = OUT_DIR / f"{utt_id}__{voice}.wav"
            sf.write(str(out_path), full, 24000)
            print(f"  -> {out_path}  ({len(full) / 24000:.1f}s)")

    print(f"\nDone. {total} clips written to {OUT_DIR.resolve()}")
    print()
    print("To compare, play each row of three (same utterance, different voice):")
    for utt_id, _ in UTTERANCES:
        print(f"  {utt_id}:")
        for voice, _ in VOICES:
            print(f"    {OUT_DIR}/{utt_id}__{voice}.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
