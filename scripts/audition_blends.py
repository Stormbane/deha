"""Generate Kokoro voice-blend WAV samples for Narada voice audition.

Six 50/50 male/female blends plus two solo reference voices, all
synthesizing the same test utterance so blends can be compared head to
head against each other and against the current production voice.

Mechanism: Kokoro.create() accepts the voice arg as either a name string
or an np.ndarray style vector. get_voice_style(name) exposes per-voice
style vectors of shape (510, 1, 256). Linear-blending two style vectors
(0.5 * vA + 0.5 * vB) produces a hybrid voice. Per-position weights are
possible too but linear is the standard mix.

Output: data/voice-blends/<date>/<slug>.wav plus a README.md index.

Run from project root:
    python scripts/audition_blends.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

# Test phrase: ~28 words, statement + question, Narada-flavored. Mixes
# "honestly" / "actually" cadence with an em-dash pause and ends in a
# real question — meant to expose monotone (it'll flatten the question)
# vs. expressive (it'll lift the question).
TEST_UTTERANCE = (
    "Honestly, whales are out here being more mysterious than most "
    "humans I know. So — what's actually taking up the most space in "
    "your head right now?"
)


# Each blend: (slug, [(voice_name, weight), ...], description)
# Solo references = single-voice with weight 1.0.
BLENDS: list[tuple[str, list[tuple[str, float]], str]] = [
    # Solo references
    (
        "00_solo_am_puck",
        [("am_puck", 1.0)],
        "Current production voice (American male, playful). Baseline for A/B.",
    ),
    (
        "01_solo_af_heart",
        [("af_heart", 1.0)],
        "American female calm narrator. The female reference.",
    ),
    # Male/female 50/50 blends
    (
        "10_blend_am_puck__af_heart",
        [("am_puck", 0.5), ("af_heart", 0.5)],
        "Current voice + calm narrator. Mellower playful.",
    ),
    (
        "11_blend_am_puck__bf_emma",
        [("am_puck", 0.5), ("bf_emma", 0.5)],
        "Current voice + British female. Cross-Atlantic playful.",
    ),
    (
        "12_blend_am_michael__af_sky",
        [("am_michael", 0.5), ("af_sky", 0.5)],
        "Warm American male + soft American female. Smooth conversational.",
    ),
    (
        "13_blend_am_michael__af_heart",
        [("am_michael", 0.5), ("af_heart", 0.5)],
        "Warm American male + calm narrator. Sage register.",
    ),
    (
        "14_blend_bm_george__af_heart",
        [("bm_george", 0.5), ("af_heart", 0.5)],
        "British weighty + calm narrator. Considered, slower.",
    ),
    (
        "15_blend_bm_lewis__bf_alice",
        [("bm_lewis", 0.5), ("bf_alice", 0.5)],
        "British male + British female. All-British register.",
    ),
    # Indian / Hindi voices — solo and blended. Kokoro's hf_/hm_ voices
    # are Hindi-language but their style vectors can be applied to
    # English text; output is typically Indian-accented English with
    # variable phoneme accuracy.
    (
        "20_solo_hf_alpha",
        [("hf_alpha", 1.0)],
        "Hindi female, alpha. Pure Indian female on English text.",
    ),
    (
        "21_solo_hf_beta",
        [("hf_beta", 1.0)],
        "Hindi female, beta. Pure Indian female, alternate timbre.",
    ),
    (
        "22_solo_hm_omega",
        [("hm_omega", 1.0)],
        "Hindi male, omega. Pure Indian male on English text.",
    ),
    (
        "23_solo_hm_psi",
        [("hm_psi", 1.0)],
        "Hindi male, psi. Pure Indian male, alternate timbre.",
    ),
    # Indian blends — keep the #13 recipe shape, swap or add Indian voices
    (
        "30_blend_am_michael__hf_alpha",
        [("am_michael", 0.5), ("hf_alpha", 0.5)],
        "Like #13 but with Indian female instead of American narrator.",
    ),
    (
        "31_blend_af_heart__hm_omega",
        [("af_heart", 0.5), ("hm_omega", 0.5)],
        "Calm American narrator + Indian male.",
    ),
    (
        "32_blend_am_michael__hf_alpha__af_heart",
        [("am_michael", 1/3), ("hf_alpha", 1/3), ("af_heart", 1/3)],
        "Three-way: warm AmM + Indian female + calm narrator. "
        "Keeps #13's character, adds Indian color at 1/3.",
    ),
    (
        "33_blend_hf_alpha__hm_omega",
        [("hf_alpha", 0.5), ("hm_omega", 0.5)],
        "All-Indian: female + male blend.",
    ),
    (
        "34_blend_bm_george__hf_alpha",
        [("bm_george", 0.5), ("hf_alpha", 0.5)],
        "British weighty + Indian female. Indian English with "
        "British colonial inflection (often natural-sounding).",
    ),
    (
        "35_blend_am_michael__hm_omega",
        [("am_michael", 0.5), ("hm_omega", 0.5)],
        "Warm AmM + Indian male.",
    ),
]


def _models_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent / "models" / "kokoro"
    raise SystemExit("can't locate project root from script location")


def _build_voice_vector(
    kokoro, components: list[tuple[str, float]]
) -> np.ndarray:
    """Linear-combine per-voice style vectors weighted as given.

    Weights don't have to sum to 1.0 but typically should for a
    well-behaved blend. Solo voice = single component with weight 1.0.
    """
    if not components:
        raise ValueError("empty blend")
    total_weight = sum(w for _, w in components)
    if total_weight <= 0:
        raise ValueError("blend weights must sum to a positive value")
    accum = None
    for name, weight in components:
        v = kokoro.get_voice_style(name).astype(np.float32)
        contribution = v * (weight / total_weight)
        accum = contribution if accum is None else accum + contribution
    return accum


def main() -> int:
    from kokoro_onnx import Kokoro

    models_dir = _models_dir()
    model_path = models_dir / "kokoro-v1.0.onnx"
    voices_path = models_dir / "voices-v1.0.bin"
    if not model_path.exists() or not voices_path.exists():
        print(f"models not found in {models_dir}", file=sys.stderr)
        return 1

    print(f"loading kokoro from {models_dir}...")
    kokoro = Kokoro(str(model_path), str(voices_path))

    today = dt.date.today().isoformat()
    out_dir = Path("data/voice-blends") / today
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"writing to {out_dir}")
    print(f"utterance: {TEST_UTTERANCE!r}")
    print()

    index_lines: list[str] = [
        f"# Voice blend audition — {today}",
        "",
        f"Test utterance:",
        "",
        f"> {TEST_UTTERANCE}",
        "",
        "| file | recipe | notes |",
        "|---|---|---|",
    ]

    for slug, components, desc in BLENDS:
        voice_arr = _build_voice_vector(kokoro, components)
        # Use en-us for American voices, en-gb when any British voice
        # is in the mix (matches Kokoro's tokenizer expectations).
        has_british = any(name.startswith(("b", "i")) for name, _ in components)
        lang = "en-gb" if has_british else "en-us"
        samples, sr = kokoro.create(
            TEST_UTTERANCE, voice=voice_arr, speed=1.0, lang=lang,
        )
        out_path = out_dir / f"{slug}.wav"
        sf.write(str(out_path), samples, sr)
        recipe = " + ".join(
            f"{name}@{weight:.2f}" if weight != 1.0 else name
            for name, weight in components
        )
        index_lines.append(
            f"| `{slug}.wav` | {recipe} | {desc} |"
        )
        print(f"  wrote {out_path.name}  ({recipe})")

    index_lines.extend(["", "## How to listen"])
    index_lines.append(
        "Play the solo references first (00, 01) to calibrate your ear, "
        "then the blends (10-15). Look for: which one keeps gravity on "
        "the statement *and* lifts naturally on the question."
    )
    index_path = out_dir / "README.md"
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print()
    print(f"index at {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
