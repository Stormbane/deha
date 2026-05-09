"""Switch the body's current bhumi via the ESPHome API.

Usage:
    python scripts/set_bhumi.py landscape
    python scripts/set_bhumi.py ufo_interior
"""

from __future__ import annotations

import asyncio
import sys

from aioesphomeapi import APIClient

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053
ALLOWED = {"landscape", "ufo_interior"}


async def set_bhumi(ip: str, name: str) -> None:
    client = APIClient(ip, DEFAULT_PORT, password="")
    try:
        await client.connect(login=True)
        _entities, services = await client.list_entities_services()
        for svc in services:
            if svc.name == "set_bhumi":
                await client.execute_service(svc, {"name": name})
                print(f"set_bhumi({name!r}) -> ok")
                return
        raise RuntimeError("set_bhumi service not found on device")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED:
        print(f"usage: {sys.argv[0]} {'|'.join(sorted(ALLOWED))}",
              file=sys.stderr)
        return 2
    ip = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DEVICE_IP
    asyncio.run(set_bhumi(ip, sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
