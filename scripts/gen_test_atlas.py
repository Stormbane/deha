#!/usr/bin/env python
"""Generate the placeholder test atlas in the Phase 2 atlas format.

Replaces gen_test_sprites.py for Phase 2 onward — instead of emitting
a flat C array directly, emits PNGs + a JSON manifest under
embodiment/sprites/test_atlas/, then invokes build_atlas.py to produce
the real header.

Frames generated (variable size — exercises the new format):
  - 4 face items at 32×32: eye_left, eye_right, mouth_neutral,
    body_idle. Color-coded with frame digit.
  - 3 mood overlays at 48×48 (larger to test variable size).
  - 1 marginalia glyph at 16×16 with magenta-key transparency, to
    test the alpha path.

Run:
    python scripts/gen_test_atlas.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ATLAS_NAME = "test_atlas"
OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "embodiment"
    / "sprites"
    / ATLAS_NAME
)

# 5x7 digit glyphs from gen_test_sprites.py.
DIGITS = {
    "0": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["01110", "10001", "00001", "00110", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
}


def make_solid(
    name: str,
    size: tuple[int, int],
    bg: tuple[int, int, int],
    label: str,
    border: bool = True,
) -> Image.Image:
    img = Image.new("RGBA", size, (*bg, 255))
    draw = ImageDraw.Draw(img)
    w, h = size
    if border:
        for x in range(w):
            img.putpixel((x, 0), (255, 255, 255, 255))
            img.putpixel((x, h - 1), (255, 255, 255, 255))
        for y in range(h):
            img.putpixel((0, y), (255, 255, 255, 255))
            img.putpixel((w - 1, y), (255, 255, 255, 255))
    # Draw label (one digit) at center, scaled 2x → 10×14 px.
    if label and label in DIGITS:
        glyph = DIGITS[label]
        x0 = (w - 10) // 2
        y0 = (h - 14) // 2
        for gy, row in enumerate(glyph):
            for gx, ch in enumerate(row):
                if ch == "1":
                    for dy in range(2):
                        for dx in range(2):
                            img.putpixel(
                                (x0 + gx * 2 + dx, y0 + gy * 2 + dy),
                                (255, 255, 255, 255),
                            )
    return img


def make_alpha_glyph(name: str) -> Image.Image:
    """16×16 with magenta-key transparency in the corners — tests the
    alpha path in the blit."""
    img = Image.new("RGBA", (16, 16), (248, 0, 248, 255))  # magenta = key
    # Solid colored disc in the middle.
    cx, cy = 8, 8
    for y in range(16):
        for x in range(16):
            if (x - cx) ** 2 + (y - cy) ** 2 <= 36:
                img.putpixel((x, y), (255, 200, 50, 255))
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[dict] = []

    # Face items at 32×32, color-coded.
    # Mouth has 5 placeholder shapes (Preston-Blair-style minus the
    # rare ones) so we can wire phonemes end-to-end before real art:
    #   mouth_rest = relaxed default, mouth_aa = wide open,
    #   mouth_ee = smile, mouth_oh = rounded, mouth_mbp = closed/pursed.
    face_specs = [
        ("eye_left",       (60, 100, 220), "0"),
        ("eye_right",      (60, 100, 220), "1"),
        ("mouth_rest",     (200, 60, 100), "2"),
        ("body_idle",      (60, 180, 100), "3"),
        ("mouth_aa",       (220, 90, 110), "4"),
        ("mouth_ee",       (200, 100, 90), "5"),
        ("mouth_oh",       (180, 70, 110), "6"),
        ("mouth_mbp",      (160, 50, 100), "7"),
    ]
    for name, color, label in face_specs:
        img = make_solid(name, (32, 32), color, label)
        img.save(OUT_DIR / f"{name}.png")
        # ox/oy: centered anchor — top-left blit position is anchor - (w/2, h/2).
        frames.append({
            "name": name,
            "file": f"{name}.png",
            "ox": -16,
            "oy": -16,
        })

    # Mood overlays at 48×48 (larger to test variable size).
    # Each non-neutral mood gets 3 transition frames (t1, t2, t3) that
    # blend from neutral toward the mood color in 25% steps. The state
    # machine plays neutral→t1→t2→t3→mood entering, reverse leaving.
    NEUTRAL_RGB = (120, 120, 140)
    mood_endpoints = [
        ("mood_neutral",   NEUTRAL_RGB,     "4"),
        ("mood_curious",   (200, 200, 60),  "5"),
        ("mood_focused",   (60, 200, 200),  "6"),
    ]
    for name, color, label in mood_endpoints:
        img = make_solid(name, (48, 48), color, label)
        img.save(OUT_DIR / f"{name}.png")
        frames.append({
            "name": name,
            "file": f"{name}.png",
            "ox": -24,
            "oy": -24,
        })

    def lerp_rgb(a, b, t):
        return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

    transition_specs = [
        ("mood_curious", (200, 200, 60), "5"),
        ("mood_focused", (60, 200, 200), "6"),
    ]
    for base_name, end_color, label in transition_specs:
        for step, t in enumerate((0.25, 0.5, 0.75), start=1):
            color = lerp_rgb(NEUTRAL_RGB, end_color, t)
            tname = f"{base_name}_t{step}"
            img = make_solid(tname, (48, 48), color, label)
            img.save(OUT_DIR / f"{tname}.png")
            frames.append({
                "name": tname,
                "file": f"{tname}.png",
                "ox": -24,
                "oy": -24,
            })

    # Marginalia glyph 16×16 with magenta corners (transparent test).
    glyph = make_alpha_glyph("glyph_dot")
    glyph.save(OUT_DIR / "glyph_dot.png")
    frames.append({
        "name": "glyph_dot",
        "file": "glyph_dot.png",
        "ox": -8,
        "oy": -8,
    })

    manifest = {
        "name": ATLAS_NAME,
        "version": 1,
        "anchor": "center",
        "enum_prefix": "TA",
        "frames": frames,
    }
    (OUT_DIR / f"{ATLAS_NAME}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(frames)} frames + manifest to {OUT_DIR}")

    # Build the atlas header by invoking build_atlas.
    here = Path(__file__).resolve().parent
    rc = subprocess.call([sys.executable, str(here / "build_atlas.py"), str(OUT_DIR)])
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
