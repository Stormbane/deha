"""Presence fusion for the body.

Reads available sensor signals (radar, camera, microphone, ...) and fuses
them into a single boolean + per-source breakdown for prana's heartbeat
router. The contract is in `docs/contracts/presence.md`.

Current state (2026-05-17): **contract-only stub**. The BOX-3-Sensor variant
ships with mmWave radar (LD2410), camera, IR, and temp/humidity (AHT20),
but `firmware/narada-faces.yaml` does not expose any of them to ESPHome /
HA yet. Until that firmware work lands, every reader honestly reports
`detected=False, confidence=0.0`, the fused `present` is `False`, and
prana's PC-input fallback continues to do the work.

When the firmware exposes sensor entities, replace each `_read_*` reader
with a real implementation — likely an `aioesphomeapi` query routed
through `deha.proprioception` (same channel `display.py` and `esp_client`
use for body state).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class SourceReading:
    detected: bool = False
    last_seen_ts: float = 0.0
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "last_seen_ts": self.last_seen_ts,
            "confidence": self.confidence,
        }


def _read_radar() -> SourceReading:
    # LD2410 not yet enabled in firmware. When it is, read the ESPHome
    # binary_sensor (presence detection) and target distance/energy
    # sensors via aioesphomeapi.
    return SourceReading()


def _read_camera() -> SourceReading:
    # esp32_camera not yet enabled. When it is, run on-device person
    # detection (binary output only — never transmit frames; see the
    # privacy notes in docs/contracts/presence.md).
    return SourceReading()


def _read_microphone() -> SourceReading:
    # Wyoming STT VAD events pass through brain_server already; once
    # we wire a tap, last_voice_ts decays over 60s per the contract.
    return SourceReading()


_READERS: Dict[str, Callable[[], SourceReading]] = {
    "radar": _read_radar,
    "camera": _read_camera,
    "microphone": _read_microphone,
}


# Per-source confidence thresholds for the fused `present` decision.
# Microphone gets the strictest threshold + a recency window because
# HVAC/traffic can false-positive VAD; radar gets the loosest because
# it works in the dark, through soft furnishings, and doesn't false-
# positive on furniture. See docs/contracts/presence.md "Fusion logic".
_THRESHOLDS = {
    "radar":      {"min_confidence": 0.5},
    "camera":     {"min_confidence": 0.6},
    "microphone": {"min_confidence": 0.7, "max_age_s": 30.0},
}


def _fuse(sources: Dict[str, SourceReading], now: float) -> bool:
    for name, reading in sources.items():
        if not reading.detected:
            continue
        rule = _THRESHOLDS.get(name)
        if rule is None:
            # Unknown source: any positive detection counts.
            return True
        if reading.confidence < rule["min_confidence"]:
            continue
        max_age = rule.get("max_age_s")
        if max_age is not None and (now - reading.last_seen_ts) > max_age:
            continue
        return True
    return False


def current() -> dict:
    """Return the current presence snapshot.

    Shape matches `docs/contracts/presence.md`. Failures inside individual
    readers degrade to `{detected: False, last_seen_ts: 0, confidence: 0}`
    for that source rather than 500-ing the whole endpoint.
    """
    now = time.time()
    sources: Dict[str, SourceReading] = {}
    for name, reader in _READERS.items():
        try:
            sources[name] = reader()
        except Exception:
            sources[name] = SourceReading()

    present = _fuse(sources, now)
    last_seen_ts = max((s.last_seen_ts for s in sources.values()), default=0.0)
    idle_seconds = max(0.0, now - last_seen_ts) if last_seen_ts > 0.0 else 0.0

    return {
        "present": present,
        "last_seen_ts": last_seen_ts,
        "idle_seconds": idle_seconds,
        "sources": {name: s.to_dict() for name, s in sources.items()},
    }
