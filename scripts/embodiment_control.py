#!/usr/bin/env python
"""Driver for narada-embodiment.yaml — Phase 2 firmware control.

Talks to the state-machine API. The bridge will eventually drive
these same services; for now this is how we exercise them by hand.

Commands:

    python scripts/embodiment_control.py mood neutral|curious|focused
    python scripts/embodiment_control.py speaking on|off
    python scripts/embodiment_control.py phoneme rest|aa|ee|oh|mbp
    python scripts/embodiment_control.py gaze <gx> <gy>          # -1..+1 each
    python scripts/embodiment_control.py glyph on|off [x] [y]
    python scripts/embodiment_control.py clear                   # all layers
    python scripts/embodiment_control.py demo                    # mood + glyph
    python scripts/embodiment_control.py speak                   # phoneme cycle
    python scripts/embodiment_control.py gaze-loop               # eye tracking demo
    python scripts/embodiment_control.py sandhi [id]             # trigger sandhi (default 0)

    # Low-level (direct compositor poke, debug):
    python scripts/embodiment_control.py layer <id> <frame> <x> <y>

Set HOST=<ip> if mDNS doesn't resolve (we have mdns disabled).
"""
from __future__ import annotations

import asyncio
import math
import os
import sys

import aioesphomeapi

HOST = os.environ.get("HOST", "narada-embodiment.local")
PORT = int(os.environ.get("PORT", "6053"))

MOOD = {"neutral": 0, "curious": 1, "focused": 2}
PHONEME = {"rest": 0, "aa": 1, "ee": 2, "oh": 3, "mbp": 4}


async def get_services(api: aioesphomeapi.APIClient) -> dict:
    _entities, services = await api.list_entities_services()
    return {s.name: s for s in services}


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    print(f"connecting to {HOST}:{PORT} ...", flush=True)
    api = aioesphomeapi.APIClient(HOST, PORT, None)
    try:
        await asyncio.wait_for(api.connect(login=True), timeout=8.0)
    except (asyncio.TimeoutError, Exception) as exc:
        print(f"connect failed: {exc!r}")
        sys.exit(2)
    print("  connected.", flush=True)

    try:
        svc = await get_services(api)

        if cmd == "mood":
            name = sys.argv[2] if len(sys.argv) > 2 else "neutral"
            snap = "snap" in sys.argv[3:]
            n = MOOD.get(name)
            if n is None:
                print(f"unknown mood: {name}; one of {list(MOOD)}")
                sys.exit(1)
            await api.execute_service(
                svc["set_mood"], {"mood": n, "snap": snap}
            )
            print(f"mood -> {name}{' (snap)' if snap else ''}")

        elif cmd == "speaking":
            on = (sys.argv[2] if len(sys.argv) > 2 else "on") == "on"
            await api.execute_service(svc["set_speaking"], {"speaking": on})
            print(f"speaking -> {on}")

        elif cmd == "phoneme":
            name = sys.argv[2] if len(sys.argv) > 2 else "rest"
            n = PHONEME.get(name)
            if n is None:
                print(f"unknown phoneme: {name}; one of {list(PHONEME)}")
                sys.exit(1)
            await api.execute_service(svc["set_phoneme"], {"phoneme": n})
            print(f"phoneme -> {name}")

        elif cmd == "gaze":
            if len(sys.argv) < 4:
                print("usage: gaze <gx> <gy>   (each in -1.0 .. +1.0)")
                sys.exit(1)
            gx, gy = float(sys.argv[2]), float(sys.argv[3])
            await api.execute_service(svc["set_gaze"], {"gx": gx, "gy": gy})
            print(f"gaze -> ({gx:+.2f}, {gy:+.2f})")

        elif cmd == "glyph":
            sub = sys.argv[2] if len(sys.argv) > 2 else "on"
            x = int(sys.argv[3]) if len(sys.argv) > 3 else 280
            y = int(sys.argv[4]) if len(sys.argv) > 4 else 30
            await api.execute_service(
                svc["set_glyph"],
                {"visible": sub == "on", "x": x, "y": y},
            )
            print(f"glyph {sub} at ({x},{y})")

        elif cmd == "clear":
            await api.execute_service(svc["clear_face"], {})
            print("cleared")

        elif cmd == "layer":
            if len(sys.argv) < 6:
                print("usage: layer <id> <frame> <x> <y>")
                sys.exit(1)
            args = {
                "layer": int(sys.argv[2]),
                "frame": int(sys.argv[3]),
                "x": int(sys.argv[4]),
                "y": int(sys.argv[5]),
            }
            await api.execute_service(svc["set_layer"], args)
            print(f"layer -> {args}")

        elif cmd == "demo":
            for m in ("neutral", "curious", "focused", "neutral"):
                await api.execute_service(
                    svc["set_mood"], {"mood": MOOD[m], "snap": False}
                )
                print(f"mood -> {m}")
                # Through-neutral animation is up to ~640 ms; sleep long
                # enough to let it complete before the next mood change.
                await asyncio.sleep(1.5)
            await api.execute_service(
                svc["set_glyph"], {"visible": True, "x": 280, "y": 30}
            )
            print("glyph on")
            await asyncio.sleep(1.5)
            await api.execute_service(
                svc["set_glyph"], {"visible": False, "x": 0, "y": 0}
            )
            print("glyph off")

        elif cmd == "speak":
            # Cycle phonemes at ~12 Hz to simulate speech. Stand-in
            # for the phoneme stream the bridge will eventually push.
            await api.execute_service(svc["set_speaking"], {"speaking": True})
            print("speaking on; cycling phonemes for 4 s")
            ph_cycle = ["aa", "ee", "oh", "mbp", "aa", "rest"]
            t_end = asyncio.get_event_loop().time() + 4.0
            i = 0
            while asyncio.get_event_loop().time() < t_end:
                p = ph_cycle[i % len(ph_cycle)]
                await api.execute_service(
                    svc["set_phoneme"], {"phoneme": PHONEME[p]}
                )
                i += 1
                await asyncio.sleep(0.08)
            await api.execute_service(svc["set_speaking"], {"speaking": False})
            print("speaking off")

        elif cmd == "sandhi":
            sid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
            await api.execute_service(
                svc["trigger_sandhi"], {"id": sid}
            )
            print(f"sandhi -> {sid}")

        elif cmd == "gaze-loop":
            # Trace a gentle ellipse with the gaze for 4 s.
            print("gaze loop for 4 s")
            t_end = asyncio.get_event_loop().time() + 4.0
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() < t_end:
                t = asyncio.get_event_loop().time() - t0
                gx = math.cos(t * 2.0)
                gy = 0.4 * math.sin(t * 2.0)
                await api.execute_service(
                    svc["set_gaze"], {"gx": gx, "gy": gy}
                )
                await asyncio.sleep(0.05)
            await api.execute_service(svc["set_gaze"], {"gx": 0.0, "gy": 0.0})
            print("gaze recentered")

        else:
            print(f"unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)
    finally:
        await api.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
