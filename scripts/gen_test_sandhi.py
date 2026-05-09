#!/usr/bin/env python
"""Generate a synthetic test sandhi: 60 frames of color sweep.

Each frame is a solid background color (sweeping through hue) with a
moving white rectangle so we can verify motion plays at the target
fps. Designed to compress well under RLE — long flat regions with
short rectangle runs.

Output: embodiment/sandhis/test_sandhi/frame_000.png ... frame_059.png
Then invokes build_sandhi.py to produce the C header.
"""
from __future__ import annotations

import colorsys
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

NAME = "test_sandhi"
WIDTH = 320
HEIGHT = 240
FRAMES = 60
FPS = 30


def main() -> None:
    out_dir = (
        Path(__file__).resolve().parent.parent
        / "embodiment"
        / "sandhis"
        / NAME
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wipe stale frames so the encoder doesn't pick up old leftovers.
    for old in out_dir.glob("frame_*.png"):
        old.unlink()

    for i in range(FRAMES):
        # Hue sweeps 0 → 1 across the sandhi. Saturation/value held high
        # for a vivid effect.
        hue = i / FRAMES
        r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.85)
        bg = (int(r * 255), int(g * 255), int(b * 255))

        img = Image.new("RGB", (WIDTH, HEIGHT), bg)
        draw = ImageDraw.Draw(img)

        # Moving white rectangle. Sweeps left-to-right then back over
        # the 60 frames, so we see motion in both directions.
        rect_w, rect_h = 60, 40
        progress = i / (FRAMES - 1)
        if progress <= 0.5:
            x = int(progress * 2 * (WIDTH - rect_w))
        else:
            x = int((1.0 - (progress - 0.5) * 2) * (WIDTH - rect_w))
        y = (HEIGHT - rect_h) // 2
        draw.rectangle([x, y, x + rect_w, y + rect_h], fill=(255, 255, 255))

        img.save(out_dir / f"frame_{i:03d}.png")

    print(f"wrote {FRAMES} frames to {out_dir}")

    # Encode via build_sandhi.py.
    here = Path(__file__).resolve().parent
    rc = subprocess.call([
        sys.executable, str(here / "build_sandhi.py"),
        NAME, str(out_dir), "--fps", str(FPS),
    ])
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
