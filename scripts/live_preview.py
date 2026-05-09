"""Live frame push to the body — render on desktop, blit to device.

The iteration unlock from cycle 2.22. Reuses the desktop emulator
(scripts/render_preview.render) to produce frames; converts each
frame to RGB565 and ships to the device's TCP framebuffer endpoint
on port 6060. The device firmware blits incoming frames to the
display when remote-frame mode is enabled.

Usage:
    python scripts/live_preview.py                        # current device state, 2 fps
    python scripts/live_preview.py --fps 1
    python scripts/live_preview.py --hour 18 --cloud 80   # synthetic state
    python scripts/live_preview.py --no-toggle            # don't touch remote-mode flag

Toggling the flag requires the device to be reachable via the
ESPHome native API. If --no-toggle is passed, the script assumes
the flag is already on (you set it manually).

Stop with Ctrl+C. The script disables remote-mode on exit so the
native lambda comes back automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_preview import State, render

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_FRAME_PORT = 6060
DEFAULT_API_PORT = 6053
SCREEN_W = 320
SCREEN_H = 240
FRAME_BYTES = SCREEN_W * SCREEN_H * 2


def rgb888_to_rgb565_bytes(img) -> bytes:
    """Convert a PIL RGB image to big-endian RGB565 bytes (153,600 bytes)."""
    arr = np.array(img.convert("RGB"), dtype=np.uint16)
    r = (arr[:, :, 0] >> 3) & 0x1F
    g = (arr[:, :, 1] >> 2) & 0x3F
    b = (arr[:, :, 2] >> 3) & 0x1F
    rgb565 = (r << 11) | (g << 5) | b
    return rgb565.astype(">u2").tobytes()


def send_frame(ip: str, port: int, payload: bytes, timeout: float = 5.0) -> None:
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(payload)


async def set_remote_mode(ip: str, enabled: bool) -> None:
    """Toggle the device's remote_frame_active flag via the API service."""
    from aioesphomeapi import APIClient

    client = APIClient(ip, DEFAULT_API_PORT, password="")
    try:
        await client.connect(login=True)
        _entities, services = await client.list_entities_services()
        for svc in services:
            if svc.name == "set_remote_frame_mode":
                await client.execute_service(svc, {"enabled": enabled})
                return
        raise RuntimeError("set_remote_frame_mode service not found on device")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def build_state(args) -> State:
    s = State(
        mood_valence=args.mood_valence,
        mood_arousal=args.mood_arousal,
        wind_kmh=args.wind_kmh,
        wind_dir=args.wind_dir,
        precip=args.precip,
        cloud_pct=args.cloud,
        weather_code=args.code,
        attention_mode=args.attention,
        lightning=args.lightning,
        tree_species=args.tree_species,
        ufo_mode=args.ufo_mode,
        bhumi=args.bhumi,
        alien_activity=args.alien_activity,
        alien_mood=args.alien_mood,
        alien_speech=args.alien_speech,
    )
    if args.hour is None:
        try:
            import zoneinfo
            now = datetime.now(zoneinfo.ZoneInfo("Australia/Brisbane"))
        except Exception:
            now = datetime.now()
        s.hour = now.hour + now.minute / 60.0
    else:
        s.hour = args.hour
    return s


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--ip", default=DEFAULT_DEVICE_IP)
    p.add_argument("--port", type=int, default=DEFAULT_FRAME_PORT)
    p.add_argument("--fps", type=float, default=2.0)
    p.add_argument("--no-toggle", action="store_true",
                   help="Skip set_remote_frame_mode toggle")
    # State knobs (mirror render_preview.py)
    p.add_argument("--hour", type=float, default=None)
    p.add_argument("--mood-valence", type=float, default=0.3)
    p.add_argument("--mood-arousal", type=float, default=0.4)
    p.add_argument("--wind-kmh", type=float, default=14.0)
    p.add_argument("--wind-dir", type=float, default=180.0)
    p.add_argument("--precip", type=float, default=0.0)
    p.add_argument("--cloud", type=float, default=30.0)
    p.add_argument("--code", type=int, default=1)
    p.add_argument("--attention", default="diffuse",
                   choices=["diffuse", "outward", "inward"])
    p.add_argument("--lightning", action="store_true")
    p.add_argument("--tree-species", default="pine",
                   choices=["pine", "oak", "mixed"])
    p.add_argument("--ufo-mode", default="grey",
                   choices=["grey", "mood", "outline"])
    p.add_argument("--bhumi", default="ufo_interior",
                   choices=["landscape", "ufo_interior"])
    p.add_argument("--alien-activity", default="resting")
    p.add_argument("--alien-mood", default="neutral",
                   choices=["neutral", "happy", "upset", "sad", "playful"])
    p.add_argument("--alien-speech", default="")
    args = p.parse_args()

    if not args.no_toggle:
        print(f"Enabling remote-frame mode on {args.ip}...", flush=True)
        asyncio.run(set_remote_mode(args.ip, True))

    period = 1.0 / max(args.fps, 0.1)
    state = build_state(args)
    started = time.time()
    n_sent = 0
    print(f"Streaming to {args.ip}:{args.port} at ~{args.fps:g} fps. Ctrl+C to stop.",
          flush=True)
    try:
        while True:
            tick_start = time.time()
            state.now_ms = (time.time() * 1000) % 1_000_000
            img = render(state)
            payload = rgb888_to_rgb565_bytes(img)
            assert len(payload) == FRAME_BYTES, \
                f"frame size {len(payload)} != {FRAME_BYTES}"
            try:
                send_frame(args.ip, args.port, payload)
                n_sent += 1
                if n_sent == 1 or n_sent % 10 == 0:
                    elapsed = time.time() - started
                    actual_fps = n_sent / max(elapsed, 0.001)
                    print(f"  frame #{n_sent}  ({actual_fps:.1f} fps actual)",
                          flush=True)
            except Exception as e:
                print(f"  send failed: {type(e).__name__}: {e}", flush=True)
            sleep_for = period - (time.time() - tick_start)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        if not args.no_toggle:
            print(f"Disabling remote-frame mode...", flush=True)
            try:
                asyncio.run(set_remote_mode(args.ip, False))
            except Exception as e:
                print(f"  toggle-off failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
