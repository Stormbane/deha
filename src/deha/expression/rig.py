"""Alien-Narada rig — composes layered vocabulary entries into a frame.

The Rig dataclass holds the current state-name for each layer. compose()
walks the vocabulary, samples animated entries at time t, and returns
both:
  - 12 rows of the alien silhouette (each row exactly 13 chars wide)
  - a list of (col_offset, row_offset, glyph) extras spilling outside
    the silhouette: ear decorations, hand-floats, sleep z's, music
    notes, listening waves, etc.

Higher-level code paints these onto whatever surface (PIL Image for
desktop emulator; ESPHome display for firmware mirror).

Color is decided by the caller — this module is purely structural.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deha.expression import vocabulary as V
from deha.expression.vocabulary import (
    ACCENTS, ANTENNAE, ARMS, BROW, CHEEKS, EARS, EYES, LEGS, MOOD_OVERLAYS,
    MOUTH, PRESETS, AccentFrame, Animated, ArmPose,
)


# Inner alien silhouette is 13 chars wide, 12 rows tall.
ALIEN_WIDTH = 13
ALIEN_HEIGHT = 12


@dataclass
class Rig:
    """Per-layer state names. Pulled from vocabulary at compose time."""
    antennae: str = "default"
    brow: str = "neutral"
    eyes: str = "open"
    ears: str = "default"
    mouth: str = "smile"
    arms: str = "rest"
    legs: str = "stand"
    cheeks: str = "default"
    accent_l: str = "none"
    accent_r: str = "none"
    x_offset: int = 0
    y_offset: int = 0
    facing: str = "right"

    @classmethod
    def from_preset(cls, name: str, mood: str = "neutral",
                    **overrides: Any) -> "Rig":
        preset = PRESETS.get(name, PRESETS["resting"])
        layer = dict(preset)
        layer.update(MOOD_OVERLAYS.get(mood, {}))
        layer.update(overrides)
        return cls(**{k: v for k, v in layer.items()
                      if k in cls.__dataclass_fields__})


def _resolve(layer_dict: dict, key: str, t_ms: float, default: Any) -> Any:
    entry = layer_dict.get(key, default)
    if isinstance(entry, Animated):
        return entry.at(t_ms)
    return entry


def _resolve_arms(key: str, t_ms: float) -> ArmPose:
    entry = ARMS.get(key, ARMS["rest"])
    if isinstance(entry, Animated):
        sub_key = entry.at(t_ms)
        entry = ARMS.get(sub_key, ARMS["rest"])
    if isinstance(entry, ArmPose):
        return entry
    return ARMS["rest"]


def _resolve_accent(name: str, t_ms: float) -> AccentFrame:
    entry = ACCENTS.get(name, ACCENTS["none"])
    if isinstance(entry, Animated):
        entry = entry.at(t_ms)
    if isinstance(entry, AccentFrame):
        return entry
    return ACCENTS["none"]


def _center3(s: str) -> str:
    """13-char eye/mouth row with a 3-char center glyph (1-pad outside walls)."""
    return f" |   {s}   | "


def _center5(s: str) -> str:
    """13-char eye/mouth row with a 5-char center glyph (1-pad outside walls)."""
    return f" |  {s}  | "


def compose(rig: Rig, t_ms: float) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Compose the alien at time t. Returns (rows, extras).

    rows: 12 strings, each EXACTLY ALIEN_WIDTH chars wide.
    extras: list of (col_offset_from_alien_left, row_index, glyph) tuples
            for decorations that spill outside the silhouette envelope
            (ear waves, hand floats, accents). col_offset can be negative.
    """
    antennae = _resolve(ANTENNAE, rig.antennae, t_ms, "..-=-..")
    brow     = _resolve(BROW,     rig.brow,     t_ms, "_ _")
    eyes     = _resolve(EYES,     rig.eyes,     t_ms, "O O")
    ears     = _resolve(EARS,     rig.ears,     t_ms, ("", ""))
    mouth    = _resolve(MOUTH,    rig.mouth,    t_ms, "\\_/")
    arm_pose = _resolve_arms(rig.arms, t_ms)
    legs     = _resolve(LEGS,     rig.legs,     t_ms, "/     \\")

    # Eyes — handle 5-char (closed_deep) variant.
    if isinstance(eyes, str) and len(eyes) == 5:
        eye_row = _center5(eyes)
    else:
        eye_row = _center3(eyes if isinstance(eyes, str) else "O O")

    brow_row = f"  /  {brow}  \\  "

    # Mouth — handle 5-char talking-wide variants. Cheeks `)` `(` flank
    # 3-char mouths (resolved from the CHEEKS vocabulary so future
    # variants — e.g. blushed `}{`, dimpled `>< ` — slot in cleanly).
    if isinstance(mouth, str) and len(mouth) == 5:
        mouth_row = _center5(mouth)
    else:
        m = mouth if isinstance(mouth, str) else "\\_/"
        cheeks = _resolve(CHEEKS, rig.cheeks, t_ms, ")(")
        cl, cr = (cheeks[0], cheeks[1]) if len(cheeks) >= 2 else (")", "(")
        mouth_row = f" | {cl} {m} {cr} | "   # 13 chars

    # Ears row — `C    u    D` (C/D are the ears, flush with the wall);
    # ear pulse decorations spill outside this envelope.
    ear_row = " C    u    D "  # already 13 chars

    # Antennae — `..---..` is 7 chars; centered with 3 spaces each side.
    ant_row = f"   {antennae}   "

    # Body silhouette with sacred thread (yajnopavita) `/` running from
    # right shoulder down across the torso, and dhoti `\` wrapping the
    # lower body — both identity features that persist across activities.
    chin_row    = "  \\_______/  "       # 2+9+2 = 13
    neck_row    = "   |    /|   "        # `/` is the top of sacred thread
    waist_row   = "   |  \\  |   "       # `\` is the dhoti drape
    lower_row   = "   |___\\_|   "       # `\` continuing dhoti through belt
    legs_row    = f"   {legs}   "         # 3+7+3 = 13

    sh_row = arm_pose.shoulders
    hands_row = arm_pose.hands
    # Pad shoulders / hands to ALIEN_WIDTH if needed.
    if len(sh_row) < ALIEN_WIDTH:
        sh_row = sh_row.center(ALIEN_WIDTH)
    if len(hands_row) < ALIEN_WIDTH:
        hands_row = hands_row.center(ALIEN_WIDTH)

    # Special case: namaskaram greeting replaces chin + neck + shoulders + hands.
    if rig.arms == "namaskaram":
        chin_row  = "  \\___A___/  "
        neck_row  = "   | / \\   "
        sh_row    = "   |/   \\|  "
        hands_row = "   |_____|  "

    rows = [
        ant_row,        # 0
        brow_row,       # 1
        eye_row,        # 2
        ear_row,        # 3
        mouth_row,      # 4
        chin_row,       # 5
        neck_row,       # 6
        sh_row,         # 7
        hands_row,      # 8
        waist_row,      # 9
        lower_row,      # 10
        legs_row,       # 11
    ]
    # Force every row to exactly ALIEN_WIDTH for caller-side alignment.
    rows = [r.ljust(ALIEN_WIDTH)[:ALIEN_WIDTH] for r in rows]

    extras: list[tuple[int, int, str]] = []

    # Ear decorations spill OUTSIDE the silhouette. ears is (left, right).
    if isinstance(ears, tuple):
        ear_l, ear_r = ears
        if ear_l:
            # Place to the left of the C, char-by-char rightmost first.
            for i, ch in enumerate(reversed(ear_l)):
                extras.append((-1 - i, 3, ch))
        if ear_r:
            for i, ch in enumerate(ear_r):
                extras.append((ALIEN_WIDTH + i, 3, ch))

    # Floating hands from raised arms (e.g. talking-3 hand `O` top-right).
    fl, fr = arm_pose.floats
    if fl:
        # Float drifts off the upper-left of the alien torso.
        extras.append((1, 6, fl))
    if fr:
        extras.append((ALIEN_WIDTH - 2, 6, fr))

    # Layered floating accents (Z's, ?'s, music, etc).
    for side, name, side_sign in (("l", rig.accent_l, -1),
                                   ("r", rig.accent_r, +1)):
        if name == "none":
            continue
        af = _resolve_accent(name, t_ms)
        for dx, dy, ch in af.glyphs:
            if not ch.strip():
                continue
            if side_sign < 0:
                col = -1 - abs(dx)
            else:
                col = ALIEN_WIDTH + abs(dx)
            row = max(0, 1 + dy)
            extras.append((col, row, ch))

    # Drop space-only ear extras for cleanliness.
    extras = [(c, r, g) for (c, r, g) in extras if g.strip()]
    return rows, extras


# ---------------------------------------------------------------------------
# Mapping from activity_mode (existing global set by daemon) to alien preset.
# Used as a default; can be overridden by setting alien_activity directly.
# ---------------------------------------------------------------------------
ACTIVITY_TO_ALIEN: dict[str, str] = {
    "resting":   "resting",
    "speaking":  "speaking",
    "listening": "listening",
    "thinking":  "thinking",
    "working":   "working",
    "dreaming":  "dreaming",
    "seeing":    "resting",
}


def alien_for_activity(activity_mode: str) -> str:
    return ACTIVITY_TO_ALIEN.get(activity_mode, "resting")
