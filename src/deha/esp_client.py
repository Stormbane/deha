"""HTTP REST client for the ESP32-S3-BOX-3 embodiment device.

Thin wrapper around the device's REST endpoints. Stateless — each call
is a fresh HTTP request. Use this for heartbeat integration and direct
device control from the desktop side.

Endpoints (defined in embodiment/firmware/narada-heartbeat.yaml):
  POST /heartbeat  — push cycle state (status, topic, action)
  POST /display    — push arbitrary text/icon update
  GET  /status     — read device health and last-seen timestamps

Design note: every public method swallows exceptions and returns a
sentinel (False or DeviceStatus(reachable=False)). Display/body failure
must never block cognition — the same principle as heartbeat/display.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_TIMEOUT = 5.0


@dataclass
class HeartbeatPayload:
    status: str
    topic: str
    action: str


@dataclass
class DisplayPayload:
    text: str
    icon: Optional[str] = None


@dataclass
class DeviceStatus:
    reachable: bool
    uptime: Optional[int] = None
    last_heartbeat: Optional[str] = None


class EspClient:
    """Synchronous HTTP REST client for the embodiment device.

    Stateless — every call is a fresh HTTP request. Failures are logged
    and return False / DeviceStatus(reachable=False); they never raise.
    """

    def __init__(
        self,
        device_ip: str = DEFAULT_DEVICE_IP,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = f"http://{device_ip}"
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.ConnectionError:
            logger.warning("Device unreachable: %s%s", self.base_url, path)
            return False
        except requests.exceptions.Timeout:
            logger.warning("Device timed out: %s%s", self.base_url, path)
            return False
        except requests.exceptions.HTTPError as e:
            logger.warning("Device HTTP error: %s", e)
            return False
        except Exception as e:
            logger.warning("Device call failed: %s: %s", type(e).__name__, e)
            return False

    def post_heartbeat(self, payload: HeartbeatPayload) -> bool:
        """Push cycle state to the device."""
        return self._post(
            "/heartbeat",
            {"status": payload.status, "topic": payload.topic, "action": payload.action},
        )

    def post_display(self, payload: DisplayPayload) -> bool:
        """Push a text/icon update to the device display."""
        data: dict = {"text": payload.text}
        if payload.icon is not None:
            data["icon"] = payload.icon
        return self._post("/display", data)

    def get_status(self) -> DeviceStatus:
        """Read device health. Returns DeviceStatus(reachable=False) on any failure."""
        try:
            resp = requests.get(
                f"{self.base_url}/status",
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return DeviceStatus(
                reachable=True,
                uptime=data.get("uptime"),
                last_heartbeat=data.get("last_heartbeat"),
            )
        except Exception as e:
            logger.debug("Device status check failed: %s: %s", type(e).__name__, e)
            return DeviceStatus(reachable=False)

    def is_reachable(self) -> bool:
        """Quick reachability check via GET /status."""
        return self.get_status().reachable
