"""BodyClient — query Narada's physical body state.

Reads the ESPHome native API to discover what's currently on the display,
network state, and sensor values. This is the proprioception layer.

Stateless by design: every call opens a fresh connection. See the same
note in deha.display — caching APIClients across asyncio.run
calls causes indefinite hangs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from aioesphomeapi import APIClient

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053
DEFAULT_TIMEOUT = 8.0


@dataclass
class BodyState:
    """A snapshot of the body's current state."""
    reachable: bool
    name: str | None = None
    ip_address: str | None = None
    wifi_ssid: str | None = None
    current_status: str | None = None
    current_thought: str | None = None
    sensors: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def display_lines(self) -> list[str]:
        """Format as readable lines for terminal output."""
        lines = []
        lines.append(f"Reachable:    {self.reachable}")
        if self.error:
            lines.append(f"Error:        {self.error}")
            return lines
        if self.name:
            lines.append(f"Device:       {self.name}")
        if self.ip_address:
            lines.append(f"IP:           {self.ip_address}")
        if self.wifi_ssid:
            lines.append(f"WiFi:         {self.wifi_ssid}")
        lines.append("")
        lines.append("=== What's on the screen ===")
        lines.append(f"Status:       {self.current_status or '(unknown)'}")
        lines.append(f"Thought:      {self.current_thought or '(unknown)'}")
        if self.sensors:
            lines.append("")
            lines.append("=== Other sensors ===")
            for k, v in self.sensors.items():
                lines.append(f"{k:14s}{v}")
        return lines


class BodyClient:
    """Query the ESP32 body via the ESPHome native API."""

    def __init__(
        self,
        device_ip: str = DEFAULT_DEVICE_IP,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.device_ip = device_ip
        self.port = port
        self.timeout = timeout

    def ping(self) -> bool:
        """Quick HTTP reachability check."""
        try:
            import requests
            resp = requests.get(f"http://{self.device_ip}/", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def status(self) -> BodyState:
        """Read full body state via the native API."""
        try:
            return asyncio.run(
                asyncio.wait_for(self._read_state(), timeout=self.timeout)
            )
        except asyncio.TimeoutError:
            return BodyState(reachable=False, error=f"timeout after {self.timeout}s")
        except Exception as e:
            return BodyState(reachable=False, error=f"{type(e).__name__}: {e}")

    async def _read_state(self) -> BodyState:
        """Connect, read all entities, return a snapshot."""
        client = APIClient(self.device_ip, self.port, password="")
        try:
            await client.connect(login=True)
            device_info = await client.device_info()

            entities, _services = await client.list_entities_services()

            # Map text_sensor entity keys to their names so we can route states
            text_sensor_names: dict[int, str] = {}
            for entity in entities:
                if type(entity).__name__ == "TextSensorInfo":
                    text_sensor_names[entity.key] = entity.name

            # Subscribe briefly to capture current state values
            captured: dict[str, str] = {}
            done = asyncio.Event()

            def on_state(state):
                key = getattr(state, "key", None)
                if key in text_sensor_names:
                    val = getattr(state, "state", None)
                    if val is not None:
                        captured[text_sensor_names[key]] = val
                # Signal once we've seen at least the proprioception sensors
                if "Current Status" in captured and "Current Thought" in captured:
                    done.set()

            client.subscribe_states(on_state)

            # Wait briefly for the sensors to publish their initial states.
            try:
                await asyncio.wait_for(done.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass  # We may be on old firmware without proprioception sensors

            state = BodyState(
                reachable=True,
                name=device_info.name,
                ip_address=captured.pop("IP Address", None),
                wifi_ssid=captured.pop("WiFi SSID", None),
                current_status=captured.pop("Current Status", None),
                current_thought=captured.pop("Current Thought", None),
                sensors=captured,
            )
            return state
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
