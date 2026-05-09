"""Layer state dataclasses for the v1.0 expression contract.

See `embodiment/design/v1.0-architecture.md` for the full schema.
See `embodiment/design/v1.0-vocabulary.md` for the visual realization.

Cognition constructs these and hands them to ExpressionClient. The firmware
renders. No cognition code touches a color, glyph, or animation timing.

This module is also the contract that scales beyond the screen: a future
humanoid body (Unitree G1 or similar) adds new layers (pose, gait, gaze,
proximity) without restructuring the ones below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AttentionMode = Literal["inward", "outward", "diffuse"]
ActivityMode = Literal[
    "resting", "thinking", "listening", "speaking",
    "seeing", "working", "dreaming",
]
SignalKind = Literal[
    "none", "oh", "q", "dots", "check", "bang", "ask", "noticed", "love",
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Mood:
    """Continuous (valence, arousal). Mood drives accent color, not geometry."""
    valence: float = 0.3        # [-1, +1]
    arousal: float = 0.4        # [0, 1]

    def clamped(self) -> "Mood":
        return Mood(
            valence=_clamp(self.valence, -1.0, 1.0),
            arousal=_clamp(self.arousal, 0.0, 1.0),
        )


@dataclass
class Vitality:
    """Slow-changing body condition. Drives breath rate and substrate density."""
    sleep_pressure: float = 0.3   # [0, 1]
    restlessness: float = 0.2     # [0, 1]
    warmth: float = 0.6           # [0, 1]

    def clamped(self) -> "Vitality":
        return Vitality(
            sleep_pressure=_clamp(self.sleep_pressure, 0.0, 1.0),
            restlessness=_clamp(self.restlessness, 0.0, 1.0),
            warmth=_clamp(self.warmth, 0.0, 1.0),
        )


@dataclass
class Attention:
    """Where the body is oriented. Drives presence-dot visibility, eye direction."""
    mode: AttentionMode = "diffuse"
    target: str = ""               # conventional: "" | "suti" | "self" | "task" | "environment"
    intensity: float = 0.4         # [0, 1]

    def clamped(self) -> "Attention":
        return Attention(
            mode=self.mode,
            target=self.target,
            intensity=_clamp(self.intensity, 0.0, 1.0),
        )


@dataclass
class Activity:
    """What the body is currently doing. Drives the foreground motif."""
    mode: ActivityMode = "resting"


@dataclass
class Utterance:
    """A spoken sentence with karaoke-style word-index advance.

    text empty → no utterance is active.
    """
    text: str = ""
    total_words: int = 0
    word_index: int = 0

    def is_active(self) -> bool:
        return bool(self.text)


@dataclass
class Signal:
    """Discrete event overlay. duration_ms == 0 means persistent until cleared."""
    kind: SignalKind = "none"
    duration_ms: int = 2000

    def is_active(self) -> bool:
        return self.kind != "none"


TreeSpecies = Literal["pine", "oak", "mixed"]
UFOMode     = Literal["grey", "mood", "outline"]


@dataclass
class Visual:
    """The body's visual preferences — its 'wardrobe.'

    Pure cosmetic state. Cognition picks; firmware decides geometry.
    Adding a new species or palette is a firmware change + one new
    enum value here.
    """
    tree_species: TreeSpecies = "pine"
    ufo_mode: UFOMode = "grey"


@dataclass
class Weather:
    """Environmental conditions at the body's physical location (Kallangur, QLD).

    Pushed by cognition from Open-Meteo. Drives cloud cover, tree sway,
    rain visibility in the horizon engine.
    """
    wind_speed_kmh: float = 0.0          # [0, 200]
    wind_direction_deg: float = 0.0      # [0, 360) — 0=N, 90=E, 180=S, 270=W
    precipitation_mm_hr: float = 0.0     # [0, 100]
    cloud_cover_pct: float = 0.0         # [0, 100]
    weather_code: int = 0                # WMO weather code (Open-Meteo)
    temperature_c: float = 22.0

    def clamped(self) -> "Weather":
        wd = self.wind_direction_deg % 360.0
        if wd < 0.0:
            wd += 360.0
        return Weather(
            wind_speed_kmh=_clamp(self.wind_speed_kmh, 0.0, 200.0),
            wind_direction_deg=wd,
            precipitation_mm_hr=_clamp(self.precipitation_mm_hr, 0.0, 100.0),
            cloud_cover_pct=_clamp(self.cloud_cover_pct, 0.0, 100.0),
            weather_code=int(self.weather_code),
            temperature_c=self.temperature_c,
        )


@dataclass
class BodyExpressionState:
    """The full v1.0 expression state — seven layers.

    Used for batched updates and for read-back via proprioception.
    """
    mood: Mood = field(default_factory=Mood)
    vitality: Vitality = field(default_factory=Vitality)
    attention: Attention = field(default_factory=Attention)
    activity: Activity = field(default_factory=Activity)
    utterance: Utterance = field(default_factory=Utterance)
    signal: Signal = field(default_factory=Signal)
    weather: Weather = field(default_factory=Weather)
