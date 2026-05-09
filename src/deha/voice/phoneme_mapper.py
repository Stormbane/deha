"""Audio amplitude → phoneme bucket.

Piper exposes phoneme IDs for some voices but not consistently across
versions, and the IPA→Preston-Blair mapping is non-trivial. For the
Phase 2 placeholder we use a simpler heuristic: short-term RMS of the
audio chunk picks one of {REST, MBP, EE, OH, AA}. The result is a
mouth that opens with vowels and closes during stops/silence — close
enough to "lip-sync" for a placeholder face.

When real character art lands and we want true phoneme accuracy, this
module gets replaced by a proper Piper-IPA → 5-shape mapper. The
EmbodimentClient interface stays the same; only this file changes.
"""

from __future__ import annotations

import math

import numpy as np

from .embodiment_client import Phoneme


# RMS thresholds (relative to int16 full scale = 32767). Tuned for
# Piper output at volume=4.0; if we change Piper volume we may need
# to retune. The values below give a roughly equal-time distribution
# across the 5 buckets on a sentence of normal speech.
_T_REST = 0.005
_T_MBP  = 0.025
_T_LOW  = 0.07
_T_MID  = 0.14


# To avoid the same vowel shape for every "loud" chunk (which makes
# the face look mechanical), alternate between two shapes within each
# vowel band. State is per-mapper so each speaking turn restarts.
class PhonemeMapper:
    def __init__(self) -> None:
        self._counter = 0

    def reset(self) -> None:
        self._counter = 0

    def map_chunk(self, pcm_bytes: bytes) -> Phoneme:
        """Return the phoneme bucket for one PCM chunk (int16 mono).

        Empty chunk → REST. Otherwise compute normalized RMS and
        bucket into the 5 mouth shapes.

        Earlier versions alternated within each band for "variety";
        in practice that just made the placeholder mouth flash
        between bright primary-color squares at 16 Hz. Steady mapping
        — same amplitude → same phoneme — reads much calmer. Pipeline
        also only pushes set_phoneme on change, so a sustained
        amplitude doesn't re-blit the mouth at all."""
        if not pcm_bytes:
            return Phoneme.REST
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if arr.size == 0:
            return Phoneme.REST
        rms = float(math.sqrt(np.mean(arr * arr))) / 32767.0

        if rms < _T_REST:
            return Phoneme.REST
        if rms < _T_MBP:
            return Phoneme.MBP
        if rms < _T_LOW:
            return Phoneme.EE
        if rms < _T_MID:
            return Phoneme.OH
        return Phoneme.AA


__all__ = ["PhonemeMapper"]
