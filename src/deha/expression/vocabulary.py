"""Alien-Narada vocabulary — every glyph for every layer.

This module is the single source of truth for what the alien-Narada can
look like. The structure is intentionally browseable / editable: each
layer is a dict from state-name to glyph data. Animated states are
lists of frames (cycled by time) instead of static strings.

The firmware mirrors this structure in C++ (see narada-body.yaml's
ufo_interior block). When you add a state here, mirror it there too —
or eventually generate the C++ from this file.

Glyph conventions:
  - All inner rows of the alien are 13 chars wide (between `|` walls).
  - The brow row is the 3-char center (e.g. `_ _`); other 10 chars are
    `/  ... \`. We render the FRAME (`|` walls and braces) separately,
    so each entry here only owns its own 3 chars (eyes/brow/mouth) or
    its own 11-char antennae line.

A `Frames` is a list of glyph strings cycled by time. `cycle_period_ms`
controls how long a full cycle takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Animated:
    """An animated layer entry. Cycles through `frames` every `period_ms`."""
    frames: Sequence[str]
    period_ms: int = 600

    def at(self, t_ms: float) -> str:
        idx = int((t_ms / max(1, self.period_ms / len(self.frames)))) % len(self.frames)
        return self.frames[idx]


# ---------------------------------------------------------------------------
# ANTENNAE (row 0 — the top of the head)
# Each entry is 7 chars wide (e.g. ".---..").  Stored as 7-char slugs;
# the renderer pads them to a fixed indent.
# ---------------------------------------------------------------------------
ANTENNAE: dict[str, object] = {
    "default":   "..-=-..",
    "alert":     "^^---^^",
    "droopy":    ",,---,,",
    "thinking":  "??---??",
    "jolt":      "!!---!!",
    "happy":     "**---**",
    "flutter":   Animated(["..---..", "^^---..", "..---^^"], period_ms=900),
}


# ---------------------------------------------------------------------------
# BROW (row 1 — between antennae and eyes; the `_ _` strip)
# Always 3 chars wide; rendered between `/  ` and `  \`.
# ---------------------------------------------------------------------------
BROW: dict[str, object] = {
    "neutral":  "_ _",
    "raised":   "^ ^",
    "furrowed": "\\ /",
    "concerned": "v v",
    "one_up_l": "^ _",
    "one_up_r": "_ ^",
    "flat":     "- -",
}


# ---------------------------------------------------------------------------
# EYES (row 2 — between `|   ` and `   |`)
# Always 3 chars wide. `closed_deep` uses 5 — handled specially.
# ---------------------------------------------------------------------------
EYES: dict[str, object] = {
    "open":         "O O",
    "engaged":      "o o",
    "closed":       "- -",
    "closed_deep":  "-----",      # 5 chars — uses extra width
    "angular":      "A A",
    "asym_lookup_l": "' o",
    "asym_lookup_r": "o '",
    "look_right":   "o >",
    "look_left":    "< o",
    "look_up":      "' '",
    "look_down":    ". .",
    "sparkle":      "* *",
    "heart":        "v v",         # heart-eyes approx
    "wink_r":       "- O",
    "wink_l":       "O -",
    "blink":        Animated(["O O", "- -", "O O", "O O", "O O"], period_ms=4000),
    "saccade":      Animated(["O O", "o o", "O O", "O O", "O O"], period_ms=3000),
}


# ---------------------------------------------------------------------------
# EARS — the C/D pair on row 3 plus optional sound-wave decorations
# Each entry is (left_decor, right_decor) where decor is "" for plain ears.
# `listening_pulse` is a 3-frame animated entry.
# ---------------------------------------------------------------------------
EARS: dict[str, object] = {
    "default":         ("", ""),
    "listening_1":     ("( (", ") )"),
    "listening_2":     ("( ( (", ") ) )"),
    "listening_3":     ("( ( ( (", ") ) ) )"),
    "listening_pulse": Animated(
        [("", ""), ("(", ")"), ("( (", ") )"), ("( ( (", ") ) )")],
        period_ms=1200,
    ),
    "one_forward_r":   ("(", ""),
    "one_forward_l":   ("", ")"),
}


# ---------------------------------------------------------------------------
# MOUTH (row 4 — `|   ... |`)
# Always 3 chars wide unless noted; talking-wide variants spill to 5.
# ---------------------------------------------------------------------------
MOUTH: dict[str, object] = {
    "smile":          "\\_/",
    "smile_big":      "\\_/",
    "flat":           "---",
    "frown":          "/-\\",
    "frown_deep":     "/_\\",
    "tight":          "___",
    "breathing":      "~~~",
    "talking_closed": "===",
    "talking_mid":    "<u>",
    "talking_wide":   "<_u_>",      # 5 chars — talking only
    "talking_o":      "<O>",
    "talking_small":  "<_>",
    "talking_ee":     "\\_/",
    "shock_o":        " O ",
    "droll":          "=u=",
    "talking_cycle":  Animated(
        ["<u>", "<_u_>", "<O>", "<_>", "<u>", "===", "<u>"],
        period_ms=900,
    ),
}


# ---------------------------------------------------------------------------
# ARMS + HANDS — rows 7-9 of the alien
# Each entry yields 3 lines: shoulders, hands, lower arms (for some poses).
# `float_l` / `float_r` are optional small accent glyphs floating off the
# top corners (e.g. raised hand `O`).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ArmPose:
    shoulders: str    # row 7 inner content (5 chars between `|`s, surrounded by `/| |\`)
    hands:     str    # row 9 inner content with `O ... O` style
    floats:    tuple[str, str] = ("", "")   # left, right floating accents

    @classmethod
    def from_lines(cls, shoulders: str, hands: str,
                   float_l: str = "", float_r: str = "") -> "ArmPose":
        return cls(shoulders, hands, (float_l, float_r))


# ---------------------------------------------------------------------------
# CHEEKS (row 4 — pair of glyphs flanking the mouth, inside the wall pipes)
# Each entry is a 2-char string: (left_cheek, right_cheek). Painted only on
# 3-char mouth rows (5-char talking visemes fill the inner space already).
# ---------------------------------------------------------------------------
CHEEKS: dict[str, object] = {
    "default":  ")(",
    "blushed":  "}{",
    "dimpled":  "><",
    "puffed":   "OO",
    "sunken":   "..",
    "none":     "  ",
}


ARMS: dict[str, object] = {
    "rest":         ArmPose.from_lines("/|   / |\\", "O |__/__| O"),
    "right_raised": ArmPose.from_lines("/|     |/",  "O |_____|  ", float_r="O"),
    "left_raised":  ArmPose.from_lines("\\|     |\\", "  |_____| O", float_l="O"),
    "both_up":      ArmPose.from_lines("\\|     |/",  "  |_____|  ", float_l="O", float_r="O"),
    "extended":     ArmPose.from_lines("__|     |__", " [_______] "),
    "thinking":     ArmPose.from_lines("/|     |\\", "O |_____|  ", float_r="()"),
    "namaskaram":   ArmPose.from_lines(" |     | ",   " |_____|  "),  # special — uses extra rows
    "flap":         Animated(
        ["right_raised", "rest", "left_raised", "rest"], period_ms=600
    ),
}


# ---------------------------------------------------------------------------
# LEGS (row 11) — the bottom `/     \`
# ---------------------------------------------------------------------------
LEGS: dict[str, object] = {
    "stand":        "/     \\",
    "step_right":   "/\\   / ",
    "step_left":    " \\   /\\",
    "walk_cycle_r": Animated(["/     \\", "/\\   / "], period_ms=600),
    "walk_cycle_l": Animated(["/     \\", " \\   /\\"], period_ms=600),
    "bow":          " \\___/ ",
}


# ---------------------------------------------------------------------------
# ACCENTS — floating overlays drawn after the alien
# Each accent yields a list of (col_offset, row_offset, glyph) entries
# relative to a reference point (alien center top-right).
# Animated accents cycle their entries.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AccentFrame:
    glyphs: tuple[tuple[int, int, str], ...]   # (dx, dy, char) tuples


ACCENTS: dict[str, object] = {
    "none":      AccentFrame(()),
    "sleep_z":   Animated([
        AccentFrame(((0, -1, "z"), (2, -2, "Z"))),
        AccentFrame(((1, -2, "z"), (3, -3, "Z"))),
        AccentFrame(((2, -3, "z"), (4, -4, "Z"))),
    ], period_ms=2000),
    "thinking_q": Animated([
        AccentFrame(((-2, -2, "?"),)),
        AccentFrame(((-2, -2, "?"), (-3, -3, "?"))),
        AccentFrame(((-3, -3, "?"),)),
    ], period_ms=1500),
    "shock":      AccentFrame(((-1, -2, "!"), (1, -2, "!"))),
    "music":      Animated([
        AccentFrame(((1, -1, "♪"), (3, -2, "♫"))),
        AccentFrame(((2, -2, "♫"), (4, -3, "♪"))),
        AccentFrame(((3, -3, "♪"), (5, -4, "♫"))),
    ], period_ms=1500),
    "listening_waves": Animated([
        AccentFrame(()),
        AccentFrame(((4, 0, ")"),)),
        AccentFrame(((4, 0, ")"), (5, 0, ")"))),
        AccentFrame(((4, 0, ")"), (5, 0, ")"), (6, 0, ")"))),
    ], period_ms=1200),
    "sparkles": Animated([
        AccentFrame(((-2, -1, "*"), (3, -2, "."))),
        AccentFrame(((3, -1, "*"), (-2, -2, "."))),
    ], period_ms=600),
}


# ---------------------------------------------------------------------------
# PRESETS — high-level activity_mode → layer states
# Each preset names every layer; missing layers fall back to the default.
# Mood overlays are applied AFTER presets and override specific layers.
# ---------------------------------------------------------------------------
PRESETS: dict[str, dict[str, str]] = {
    "resting": {
        "antennae": "default", "brow": "neutral", "eyes": "blink",
        "ears": "default", "mouth": "smile", "arms": "rest",
        "legs": "stand", "accent_l": "none", "accent_r": "none",
    },
    "speaking": {
        "antennae": "default", "brow": "neutral", "eyes": "engaged",
        "ears": "default", "mouth": "talking_cycle", "arms": "rest",
        "legs": "stand", "accent_l": "none", "accent_r": "none",
    },
    "listening": {
        "antennae": "alert", "brow": "raised", "eyes": "engaged",
        "ears": "listening_pulse", "mouth": "flat", "arms": "rest",
        "legs": "stand", "accent_l": "none", "accent_r": "listening_waves",
    },
    "thinking": {
        "antennae": "thinking", "brow": "neutral", "eyes": "asym_lookup_r",
        "ears": "default", "mouth": "flat", "arms": "thinking",
        "legs": "stand", "accent_l": "none", "accent_r": "thinking_q",
    },
    "working": {
        "antennae": "default", "brow": "neutral", "eyes": "look_down",
        "ears": "default", "mouth": "tight", "arms": "extended",
        "legs": "stand", "accent_l": "none", "accent_r": "none",
    },
    "dreaming": {
        "antennae": "droopy", "brow": "flat", "eyes": "closed_deep",
        "ears": "default", "mouth": "breathing", "arms": "rest",
        "legs": "stand", "accent_l": "none", "accent_r": "sleep_z",
    },
    "walking_right": {
        "antennae": "default", "brow": "neutral", "eyes": "look_right",
        "ears": "default", "mouth": "smile", "arms": "rest",
        "legs": "walk_cycle_r", "accent_l": "none", "accent_r": "none",
    },
    "walking_left": {
        "antennae": "default", "brow": "neutral", "eyes": "look_left",
        "ears": "default", "mouth": "smile", "arms": "rest",
        "legs": "walk_cycle_l", "accent_l": "none", "accent_r": "none",
    },
    "greeting": {
        "antennae": "default", "brow": "neutral", "eyes": "engaged",
        "ears": "default", "mouth": "smile", "arms": "namaskaram",
        "legs": "stand", "accent_l": "none", "accent_r": "none",
    },
    "surprised": {
        "antennae": "jolt", "brow": "raised", "eyes": "open",
        "ears": "default", "mouth": "shock_o", "arms": "both_up",
        "legs": "stand", "accent_l": "shock", "accent_r": "shock",
    },
    "singing": {
        "antennae": "happy", "brow": "neutral", "eyes": "closed",
        "ears": "default", "mouth": "smile", "arms": "rest",
        "legs": "stand", "accent_l": "music", "accent_r": "music",
    },
}


# Mood overlays — only override eyes / brow / mouth.
MOOD_OVERLAYS: dict[str, dict[str, str]] = {
    "neutral": {},
    "happy":   {"brow": "neutral", "eyes": "engaged", "mouth": "smile"},
    "upset":   {"brow": "furrowed", "eyes": "angular", "mouth": "frown"},
    "sad":     {"brow": "concerned", "eyes": "look_down", "mouth": "frown_deep"},
    "playful": {"brow": "neutral", "eyes": "wink_r", "mouth": "droll"},
}
