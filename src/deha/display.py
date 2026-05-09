"""ESP32 display client — connects the mind to the body.

Pushes heartbeat state to the ESPHome device via its native API.
The display shows what the viveka is thinking, doing, and feeling.

Design notes:
- Every call creates a fresh APIClient and a fresh asyncio loop. We do NOT
  cache the client across calls — caching previously caused indefinite hangs
  because cached clients were bound to closed event loops, and the synchronous
  wrapper had no timeout to escape from.
- Every call is wrapped in asyncio.wait_for with a hard timeout. Display
  failure is never allowed to block cognition — the daemon's beat_once must
  always be able to proceed even if the body is unreachable.
"""

from __future__ import annotations

import asyncio
import logging

from aioesphomeapi import APIClient

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053  # ESPHome native API port
DEFAULT_TIMEOUT = 5.0  # seconds — hard cap on any single display call


class DisplayClient:
    """Client for pushing state to the ESP32-S3-BOX-3 display.

    Stateless by design: every call opens a fresh connection, does its work,
    and closes. Slightly more network overhead than a long-lived connection
    but immune to the asyncio loop reuse bug that froze the daemon for 28
    hours on 2026-04-09.
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

    def is_available(self) -> bool:
        """Check if the display device is reachable via HTTP."""
        try:
            import requests
            resp = requests.get(f"http://{self.device_ip}/", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    async def _call_service(self, service_name: str, data: dict) -> bool:
        """Connect, find the service, execute it, disconnect. Atomic."""
        client = APIClient(self.device_ip, self.port, password="")
        try:
            await client.connect(login=True)
            _, services = await client.list_entities_services()
            for service in services:
                if service.name == service_name:
                    await client.execute_service(service, data)
                    logger.debug("Display service called: %s -> %s", service_name, data)
                    return True
            logger.warning("Service not found: %s", service_name)
            return False
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass  # disconnect failures are not fatal

    def _run(self, service_name: str, data: dict) -> bool:
        """Synchronous wrapper around _call_service with a hard timeout.

        A fresh event loop is created for every call (via asyncio.run). This
        is intentional — see module docstring.
        """
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._call_service(service_name, data),
                    timeout=self.timeout,
                )
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Display call timed out after %.1fs: %s", self.timeout, service_name
            )
            return False
        except Exception as e:
            logger.warning("Display call failed: %s: %s", type(e).__name__, e)
            return False

    def set_status(self, message: str) -> bool:
        """Update the heartbeat status line on the display."""
        return self._run("set_status", {"message": message})

    def set_thought(self, message: str) -> bool:
        """Update the current thought on the display."""
        return self._run("set_thought", {"message": message})

    def show_desire(self, action: str, topic: str) -> bool:
        """Show a desire on the display."""
        self.set_status(f"{action.lower()}...")
        return self.set_thought(topic)

    def show_judging(self) -> bool:
        """Show that the viveka is judging a plan."""
        return self.set_status("judging...")

    def show_executing(self, topic: str) -> bool:
        """Show that a plan is being executed."""
        self.set_status("executing...")
        return self.set_thought(topic)

    def show_resting(self) -> bool:
        """Show the resting state with the default mantra."""
        self.set_status("resting")
        return self.set_thought("Om Namo Bhagavate Naradaya")

    def show_result(self, summary: str) -> bool:
        """Show an execution result."""
        self.set_status("complete")
        return self.set_thought(summary[:60])

    def show_error(self, message: str) -> bool:
        """Show an error state."""
        self.set_status("error")
        return self.set_thought(message[:60])

    def disconnect(self) -> None:
        """No-op — kept for backward compatibility. Each call is atomic."""
        pass
