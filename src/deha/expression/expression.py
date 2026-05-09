"""ExpressionClient — write the v1.0 expression contract to the body.

See `embodiment/design/v1.0-architecture.md` for the layer model.
See `deha.expression.layers` for the data classes.

Stateless by design: every call opens a fresh APIClient and a fresh asyncio
loop, mirroring `deha.display.DisplayClient`. The asyncio
loop-reuse hang that froze the daemon for 28 hours on 2026-04-09 stays fixed
by *never* caching clients across calls.

Every public method is fail-soft: log and return False on any failure. Body
failure must never block cognition.
"""

from __future__ import annotations

import asyncio
import logging

from aioesphomeapi import APIClient

from deha.expression.layers import (
    Activity,
    ActivityMode,
    Attention,
    Mood,
    Signal,
    SignalKind,
    TreeSpecies,
    UFOMode,
    Utterance,
    Visual,
    Vitality,
    Weather,
)

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053
DEFAULT_TIMEOUT = 5.0


class ExpressionClient:
    """Client for writing v1.0 expression layer state to the ESP32 body.

    One method per ESPHome API service. Each call is an atomic open-execute-
    close; transient connection failures fall back to a logged warning.
    """

    def __init__(
        self,
        device_ip: str = DEFAULT_DEVICE_IP,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.device_ip = device_ip
        self.port = port
        self.timeout = timeout

    # ------------------------------------------------------------------ internals

    async def _call_service(self, service_name: str, data: dict) -> bool:
        client = APIClient(self.device_ip, self.port, password="")
        try:
            await client.connect(login=True)
            _, services = await client.list_entities_services()
            for service in services:
                if service.name == service_name:
                    await client.execute_service(service, data)
                    logger.debug("Expression service called: %s -> %s", service_name, data)
                    return True
            logger.warning("Expression service not found: %s", service_name)
            return False
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _run(self, service_name: str, data: dict) -> bool:
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._call_service(service_name, data),
                    timeout=self.timeout,
                )
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Expression call timed out after %.1fs: %s", self.timeout, service_name
            )
            return False
        except Exception as e:
            logger.warning("Expression call failed: %s: %s", type(e).__name__, e)
            return False

    # ----------------------------------------------------------------- public API

    def set_mood(self, mood: Mood) -> bool:
        m = mood.clamped()
        return self._run("set_mood", {"valence": m.valence, "arousal": m.arousal})

    def set_vitality(self, vitality: Vitality) -> bool:
        v = vitality.clamped()
        return self._run(
            "set_vitality",
            {
                "sleep_pressure": v.sleep_pressure,
                "restlessness": v.restlessness,
                "warmth": v.warmth,
            },
        )

    def set_attention(self, attention: Attention) -> bool:
        a = attention.clamped()
        return self._run(
            "set_attention",
            {"mode": a.mode, "target": a.target, "intensity": a.intensity},
        )

    def set_activity(self, mode: ActivityMode) -> bool:
        return self._run("set_activity", {"mode": mode})

    def set_utterance(self, text: str, total_words: int) -> bool:
        # ESPHome reserves `text` — service variable is `phrase` on the wire.
        return self._run(
            "set_utterance", {"phrase": text, "total_words": total_words}
        )

    def set_word_index(self, i: int) -> bool:
        return self._run("set_word_index", {"word_index": i})

    def clear_utterance(self) -> bool:
        return self._run("clear_utterance", {})

    def emit_signal(self, kind: SignalKind, duration_ms: int = 2000) -> bool:
        """Emit a transient signal. duration_ms=0 means persistent until cleared."""
        return self._run(
            "emit_signal", {"kind": kind, "duration_ms": duration_ms}
        )

    def clear_signal(self) -> bool:
        return self._run("clear_signal", {})

    def set_tree_species(self, species: TreeSpecies) -> bool:
        """Switch the body's tree species (pine / oak / mixed)."""
        return self._run("set_tree_species", {"species": species})

    def set_ufo_mode(self, mode: UFOMode) -> bool:
        """Switch the UFO render mode (grey-filled / mood-tinted / outline)."""
        return self._run("set_ufo_mode", {"mode": mode})

    def set_visual(self, visual: Visual) -> bool:
        """Push both visual preferences in one call."""
        ok = self.set_tree_species(visual.tree_species)
        return self.set_ufo_mode(visual.ufo_mode) and ok

    def set_weather(self, weather: Weather) -> bool:
        """Push environmental conditions to the body. Drives cloud / wind /
        rain rendering in the horizon engine."""
        w = weather.clamped()
        return self._run(
            "set_weather",
            {
                "wind_kmh":  w.wind_speed_kmh,
                "wind_dir":  w.wind_direction_deg,
                "precip":    w.precipitation_mm_hr,
                "cloud_pct": w.cloud_cover_pct,
                "wcode":     w.weather_code,
                "temp_c":    w.temperature_c,
            },
        )

    # --------------------------------------------------------- batched convenience

    def write_state(self, mood: Mood | None = None,
                    vitality: Vitality | None = None,
                    attention: Attention | None = None,
                    activity: Activity | None = None,
                    utterance: Utterance | None = None,
                    signal: Signal | None = None,
                    weather: Weather | None = None) -> bool:
        """Write any subset of layers in sequence. Returns True iff every present
        layer wrote successfully. Each layer call is independent — partial
        success is possible. Use for end-of-cycle state pushes."""
        ok = True
        if mood is not None:
            ok = self.set_mood(mood) and ok
        if vitality is not None:
            ok = self.set_vitality(vitality) and ok
        if attention is not None:
            ok = self.set_attention(attention) and ok
        if activity is not None:
            ok = self.set_activity(activity.mode) and ok
        if utterance is not None:
            if utterance.is_active():
                ok = self.set_utterance(utterance.text, utterance.total_words) and ok
                if utterance.word_index > 0:
                    ok = self.set_word_index(utterance.word_index) and ok
            else:
                ok = self.clear_utterance() and ok
        if signal is not None:
            if signal.is_active():
                ok = self.emit_signal(signal.kind, signal.duration_ms) and ok
            else:
                ok = self.clear_signal() and ok
        if weather is not None:
            ok = self.set_weather(weather) and ok
        return ok
