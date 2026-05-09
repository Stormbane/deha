"""Push a partial STT transcript to the device for visual testing.

Usage:
    python scripts/set_stt.py "when I said hello world"
    python scripts/set_stt.py ""              # clear
    python scripts/set_stt.py --clear         # clears listening flag too
"""

from __future__ import annotations

import argparse
import asyncio

from aioesphomeapi import APIClient

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_PORT = 6053


async def call(ip: str, service_name: str, args: dict) -> None:
    client = APIClient(ip, DEFAULT_PORT, password="")
    try:
        await client.connect(login=True)
        _entities, services = await client.list_entities_services()
        for svc in services:
            if svc.name == service_name:
                await client.execute_service(svc, args)
                print(f"{service_name}({args}) -> ok")
                return
        raise RuntimeError(f"{service_name} not found on device")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--clear", action="store_true",
                   help="clear listening_active too (end the capture window)")
    p.add_argument("--ip", default=DEFAULT_DEVICE_IP)
    args = p.parse_args()
    if args.clear:
        asyncio.run(call(args.ip, "clear_listening", {}))
    else:
        asyncio.run(call(args.ip, "set_stt_transcript", {"phrase": args.text}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
