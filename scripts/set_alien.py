"""Drive the alien-Narada inside ufo_interior bhumi via the API.

Usage:
    python scripts/set_alien.py activity resting
    python scripts/set_alien.py activity speaking
    python scripts/set_alien.py mood happy
    python scripts/set_alien.py speech "Greetings traveler."
    python scripts/set_alien.py speech ""              # clear
"""

from __future__ import annotations

import asyncio
import sys

from aioesphomeapi import APIClient

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053

ACTIVITY_VALUES = {
    "resting", "speaking", "listening", "thinking", "working", "dreaming",
    "walking_left", "walking_right", "greeting", "surprised", "singing",
}
MOOD_VALUES = {"neutral", "happy", "upset", "sad", "playful"}

SERVICES = {
    "activity": ("set_alien_activity", "name", ACTIVITY_VALUES),
    "mood":     ("set_alien_mood",     "name", MOOD_VALUES),
    "speech":   ("set_alien_speech",   "phrase", None),  # any text
}


async def call(ip: str, service_name: str, var_name: str, value: str) -> None:
    client = APIClient(ip, DEFAULT_PORT, password="")
    try:
        await client.connect(login=True)
        _entities, services = await client.list_entities_services()
        for svc in services:
            if svc.name == service_name:
                await client.execute_service(svc, {var_name: value})
                print(f"{service_name}({var_name}={value!r}) -> ok")
                return
        raise RuntimeError(f"{service_name} not found on device")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} <activity|mood|speech> <value>",
              file=sys.stderr)
        return 2
    field = sys.argv[1]
    value = sys.argv[2]
    if field not in SERVICES:
        print(f"unknown field {field}; pick from {sorted(SERVICES)}",
              file=sys.stderr)
        return 2
    svc_name, var_name, allowed = SERVICES[field]
    if allowed is not None and value not in allowed:
        print(f"value {value!r} not allowed; pick from {sorted(allowed)}",
              file=sys.stderr)
        return 2
    ip = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_DEVICE_IP
    asyncio.run(call(ip, svc_name, var_name, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
