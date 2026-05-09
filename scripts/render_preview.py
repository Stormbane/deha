"""Renders the body's display lambda to a PNG for preview.

Mirrors the C++ lambda in `embodiment/firmware/narada-body.yaml`.
This is a re-implementation — drift risk: keep in sync when the
lambda changes. Useful for iterating on visuals without flashing
the device.

Usage:
    python scripts/render_preview.py
    python scripts/render_preview.py --hour 14 --out daytime.png
    python scripts/render_preview.py --hour 18 --cloud 80 --rain 1.5
    python scripts/render_preview.py --live           # query device state
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "embodiment" / "assets" / "fonts"

# Constants matching the firmware lambda
COLS = 44
ROWS = 16
CELL_W = 7
CELL_H = 14
SCREEN_W = 320
SCREEN_H = 240
HORIZON_ROW = 11
N_STARS = 25
N_TREES = 12
MAX_CLOUDS = 4

# Plant config (mirrors the t_* arrays in the lambda).
T_COLS    = [ 4, 14, 22, 30,   8, 18, 33, 39,    2,  9, 35, 42]
T_H       = [ 1,  2,  1,  2,   4,  3,  5,  3,    7,  6,  7,  6]
T_CW      = [ 1,  1,  1,  1,   3,  3,  3,  3,    7,  5,  7,  5]
T_PLANE   = [ 0,  0,  0,  0,   1,  1,  1,  1,    2,  2,  2,  2]
T_TRUNK_H = [ 0,  0,  0,  0,   1,  0,  2,  1,    2,  2,  2,  2]
T_LEAN    = [ 0,  0,  0,  0,   0,  0,  0,  0,    0,  0,  0,  0]

# Cloud templates (mirrors CLOUD_MAIN/TOP/* in the lambda).
CLOUD_MAIN = ["~~-.", "----~~.", ".~~~~~.", "~~.~"]
CLOUD_TOP  = ["",     "~~~.",     "",         "___"]
CLOUD_TOP_OFFSET = [0, 1, 0, 1]
CLOUD_Y = [1, 4, 2, 5]
CLOUD_W = [4, 4, 6, 4]

# Star positions seeded once for stable layout (mirror of xorshift seed
# 0xa11ce5 in the lambda, but Python doesn't need to match exactly).
import random
def _seeded_stars():
    rng = random.Random(0xa11ce5)
    stars = []
    for _ in range(N_STARS):
        x = rng.randrange(COLS)
        y = rng.randrange(7)
        phase = rng.uniform(0, 2 * math.pi)
        stars.append((x, y, phase))
    return stars
STARS = _seeded_stars()

def _seeded_tree_phases():
    rng = random.Random(0xb00c11)
    return [rng.uniform(0, 2 * math.pi) for _ in range(N_TREES)]
TREE_PHASES = _seeded_tree_phases()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _bilinear_tint(valence: float, arousal: float):
    """Same bilinear blend as the lambda for the mood accent color."""
    v = (valence + 1.0) * 0.5
    a = arousal
    def b(c00, c10, c01, c11):
        r = ((1 - v) * (1 - a) * c00 + v * (1 - a) * c10
             + (1 - v) * a * c01 + v * a * c11)
        return int(_clamp(r, 0, 255))
    return (
        b(0x4A, 0xC4, 0xB0, 0x7A),
        b(0x55, 0x96, 0x40, 0x50),
        b(0x60, 0x2A, 0x60, 0x8C),
    )


def _sky_color(hour: float, lightning: bool):
    if lightning:
        return (0x90, 0x90, 0xB0)
    if 4.5 <= hour < 6.0:    return (0x30, 0x18, 0x44)
    if 6.0 <= hour < 7.0:    return (0x60, 0x24, 0x40)
    if 7.0 <= hour < 8.0:    return (0x58, 0x38, 0x40)
    if 8.0 <= hour < 16.0:   return (0x20, 0x40, 0x70)
    if 16.0 <= hour < 17.0:  return (0x40, 0x30, 0x40)
    if 17.0 <= hour < 18.0:  return (0x70, 0x28, 0x20)
    if 18.0 <= hour < 19.0:  return (0x50, 0x18, 0x30)
    if 19.0 <= hour < 20.0:  return (0x28, 0x14, 0x40)
    return (0x08, 0x0E, 0x18)


@dataclass
class State:
    hour: float = 12.0
    mood_valence: float = 0.3
    mood_arousal: float = 0.4
    sleep_pressure: float = 0.3
    restlessness: float = 0.2
    attention_mode: str = "diffuse"
    wind_kmh: float = 0.0
    wind_dir: float = 0.0
    precip: float = 0.0
    cloud_pct: float = 0.0
    weather_code: int = 0
    now_ms: float = 0.0
    lightning: bool = False
    tree_species: str = "pine"   # "pine" | "oak" | "mixed"
    ufo_mode: str = "grey"        # "grey" | "mood" | "outline"
    bhumi: str = "landscape"      # "landscape" | "ufo_interior"
    activity_mode: str = "resting"
    alien_activity: str = ""      # "" → derive from activity_mode
    alien_mood: str = "neutral"   # "neutral" | "happy" | "upset" | "sad" | "playful"
    alien_speech: str = ""
    listening_active: bool = False
    stt_transcript: str = ""


def render(state: State) -> Image.Image:
    if state.bhumi == "ufo_interior":
        return _render_ufo_interior(state)
    return _render_landscape(state)


def _render_ufo_interior(state: State) -> Image.Image:
    """Inside-the-saucer view. Cockpit + porthole onto the sky + rigged
    alien-Narada built from the layered vocabulary in
    `deha.expression.rig`."""
    from deha.expression.rig import (
        Rig, compose, alien_for_activity, ALIEN_WIDTH, ALIEN_HEIGHT,
    )

    img = Image.new("RGB", (SCREEN_W, SCREEN_H))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_DIR / "IBMPlexMono-Regular.ttf"), 12)
    now_ms = state.now_ms

    # ----- UFO cabin: large windows on top, opaque floor on bottom -----
    activity_for_porthole = state.alien_activity or alien_for_activity(state.activity_mode)
    has_speech = bool(state.alien_speech) and activity_for_porthole == "speaking"
    floor_y = 168
    top_frame_h = 10
    sill_h = 4
    mull1_col = 14
    mull2_col = 30
    mull_w_px = 12
    floor_col = (0x4A, 0x4E, 0x58)
    frame_col = (0x90, 0x96, 0xA0)
    sky = _sky_color(state.hour, state.lightning)
    is_day = (state.hour >= 6.0 and state.hour < 18.0)

    # 1. Sky band fills the full width as backdrop.
    draw.rectangle((0, top_frame_h, SCREEN_W, floor_y), fill=sky)
    # 2. Floor band.
    draw.rectangle((0, floor_y, SCREEN_W, SCREEN_H), fill=floor_col)
    # 3. Stars in the sky band (night, clear).
    if (not is_day) and state.cloud_pct < 50.0:
        for sx, sy, phase in STARS:
            sx_px = sx * CELL_W + CELL_W // 2
            sy_px = sy * CELL_H + CELL_H // 2
            if not (top_frame_h + 4 < sy_px < floor_y - sill_h - 4):
                continue
            b = (math.sin(now_ms / 700.0 + phase) + 1.0) * 0.5
            if b > 0.65:
                draw.text((sx_px, sy_px), "*", font=font,
                          fill=(0xE8, 0xE0, 0xC4), anchor="mm")
    # 4. Sun (day) — drifts left→right across the sky band.
    if is_day:
        t_day = (state.hour - 6.0) / 12.0
        sxp = int(t_day * SCREEN_W)
        syp = int(top_frame_h + 16 + (1.0 - math.sin(math.pi * t_day)) * 28.0)
        if top_frame_h + 4 < syp < floor_y - sill_h - 4:
            draw.ellipse((sxp - 7, syp - 7, sxp + 7, syp + 7),
                         fill=(0xF8, 0xD8, 0x70))
    # 5. Frame overlay — top, sill, two vertical mullions.
    draw.rectangle((0, 0, SCREEN_W, top_frame_h), fill=frame_col)
    draw.rectangle((0, floor_y - sill_h, SCREEN_W, floor_y), fill=frame_col)
    draw.rectangle((mull1_col * CELL_W, top_frame_h,
                    mull1_col * CELL_W + mull_w_px, floor_y),
                   fill=frame_col)
    draw.rectangle((mull2_col * CELL_W, top_frame_h,
                    mull2_col * CELL_W + mull_w_px, floor_y),
                   fill=frame_col)

    # Mood tint for accent.
    tint = _bilinear_tint(state.mood_valence, state.mood_arousal)

    def put(col, row, color, ch):
        if col < 0 or col >= COLS or row < 0 or row >= ROWS:
            return
        draw.text((col * CELL_W, row * CELL_H), ch, font=font, fill=color)

    # Build the rig from the alien_activity preset + mood overlay.
    # Walking preset is selected by alien_activity directly; daemon decides.
    activity_name = state.alien_activity or alien_for_activity(state.activity_mode)
    rig = Rig.from_preset(activity_name, mood=state.alien_mood)

    # Walking translation: x_offset cycles ±10 cols across ~24s for idle pacing.
    if activity_name in ("walking_right", "walking_left"):
        # Position drifts in the named direction across 12s window then turns.
        cycle_t = (now_ms % 24000.0) / 24000.0
        if activity_name == "walking_right":
            base_offset = int((cycle_t * 20.0) - 10.0)
        else:
            base_offset = int(10.0 - (cycle_t * 20.0))
        rig.x_offset = base_offset

    rows, extras = compose(rig, now_ms)

    # Color palette for the alien — bright cream so it pops against the
    # blue sky behind the windows.
    skin    = (0xF0, 0xE8, 0xD0)
    eyes_col = (0xF8, 0xF8, 0xE8)
    accent_col = tuple(_clamp(int(c * 1.15), 0, 255) for c in tint)

    # Anchor position for the alien — feet flush with screen bottom.
    # legs sit on rig row 11; we anchor so legs glyph bottom = SCREEN_H.
    # When the speech bubble is up, shift the alien left so the bubble
    # can occupy the right ~20 cols without overlapping the face.
    alien_col = (0 if has_speech else 14) + rig.x_offset
    # Idle animations: breathing for non-walking/non-bubble states;
    # resting also paces ±5 cols on a 30s cycle.
    breath_dy = 0
    if activity_name not in ("walking_left", "walking_right") and not has_speech:
        breath_dy = int(math.sin(now_ms / 4000.0 * 2 * math.pi) * 1.8)
    if activity_name == "resting":
        pt = (now_ms % 30000.0) / 30000.0
        alien_col += int(math.sin(pt * 2 * math.pi) * 5.0)
    alien_y_px = SCREEN_H - ALIEN_HEIGHT * CELL_H + breath_dy
    alien_row = alien_y_px // CELL_H                # for extras anchoring

    # Color picker: most rows skin-colored; eyes row uses pale; mouth uses
    # smile-or-mood color; brow uses skin.
    def row_color(rix: int) -> tuple[int, int, int]:
        if rix == 2:        # eyes
            return eyes_col
        if rix == 4:        # mouth
            return accent_col if rig.mouth.startswith("talking") else skin
        return skin

    # Body fills — per-region rectangles hugging the inside of the ASCII
    # silhouette so the glyph walls remain visible against the cabin sky.
    skin_fill  = (0x4A, 0x30, 0x28)   # warm head tone
    kurta_fill = (0x88, 0x3A, 0x18)   # saffron chest
    dhoti_fill = (0x6A, 0x4A, 0x28)   # ochre dhoti
    ax_px = alien_col * CELL_W
    def _fill(col, row, w, h, color):
        draw.rectangle((ax_px + col * CELL_W, alien_y_px + row * CELL_H,
                        ax_px + (col + w) * CELL_W,
                        alien_y_px + (row + h) * CELL_H), fill=color)
    # Head: top wedge, main, chin wedge.
    _fill(3, 1, 7, 1, skin_fill)
    _fill(2, 2, 9, 3, skin_fill)
    _fill(3, 5, 7, 1, skin_fill)
    # Chest (rows 6..8) and dhoti (rows 9..10), inside torso walls.
    _fill(4, 6, 5, 3, kurta_fill)
    _fill(4, 9, 5, 2, dhoti_fill)

    # Paint the 12 alien rows directly in pixel coords (bypasses the
    # 16-row grid limit so feet can sit at SCREEN_H).
    for rix, row_text in enumerate(rows):
        for cix, ch in enumerate(row_text):
            if ch == " ":
                continue
            x_px = (alien_col + cix) * CELL_W
            y_px = alien_y_px + rix * CELL_H
            if 0 <= x_px < SCREEN_W and 0 <= y_px < SCREEN_H:
                draw.text((x_px, y_px), ch, font=font, fill=row_color(rix))

    # Paint the extras (ear waves, hand floats, accents).
    for (dx, dy, ch) in extras:
        c = accent_col if (rig.accent_l in ("music",) or rig.accent_r in ("music",)) else skin
        # Mood overlays: shock uses bright; thinking_q uses pale.
        if ch in ("?",):
            c = (0xC0, 0xC4, 0xD0)
        elif ch in ("!",):
            c = accent_col
        elif ch in ("z", "Z"):
            c = (0xA0, 0xA8, 0xC0)
        put(alien_col + dx, alien_row + dy, c, ch)

    # Live STT transcript — 16px on white panel; preceding words black,
    # last word red. Panel height shrinks to 1 row when text fits.
    if state.stt_transcript:
        per_row = 30
        max_total = per_row * 2
        cw_16 = 10
        t = state.stt_transcript
        visible = t if len(t) <= max_total else "..." + t[-(max_total - 3):]
        two_rows = len(visible) > per_row
        row1 = visible[:per_row] if two_rows else visible
        row2 = visible[per_row:] if two_rows else ""
        last_space = visible.rfind(" ")
        last_word_start = (last_space + 1) if last_space != -1 else 0
        small_font = ImageFont.truetype(str(FONT_DIR / "IBMPlexMono-Regular.ttf"), 16)
        bg_h = 40 if two_rows else 20
        draw.rectangle((0, 12, SCREEN_W, 12 + bg_h), fill=(0xF8, 0xF8, 0xF0))
        black = (0x18, 0x18, 0x18)
        red = (0xC8, 0x18, 0x18)
        def paint_row(y_px, row_offset, row):
            if not row:
                return
            split = last_word_start - row_offset
            if split >= len(row):
                draw.text((8, y_px), row, font=small_font, fill=black)
            elif split <= 0:
                draw.text((8, y_px), row, font=small_font, fill=red)
            else:
                blk = row[:split]
                rd = row[split:]
                draw.text((8, y_px), blk, font=small_font, fill=black)
                draw.text((8 + len(blk) * cw_16, y_px), rd,
                          font=small_font, fill=red)
        paint_row(12, 0, row1)
        if two_rows:
            paint_row(32, per_row, row2)

    # Speech bubble — when alien is speaking and there's text to show.
    if has_speech:
        _draw_speech_bubble(draw, font, now_ms, state.alien_speech,
                            alien_col=alien_col, alien_y_px=alien_y_px)

    return img


def _wrap_text(text: str, chars_per_line: int) -> list[str]:
    """Word-wrap text to chars_per_line; hard-breaks long words."""
    lines: list[str] = []
    cur = ""
    for word in text.replace("\n", " \n ").split(" "):
        if word == "\n":
            if cur:
                lines.append(cur)
            cur = ""
            continue
        if not word:
            continue
        if len(cur) + (1 if cur else 0) + len(word) <= chars_per_line:
            cur = (cur + " " + word) if cur else word
        else:
            if cur:
                lines.append(cur)
            if len(word) <= chars_per_line:
                cur = word
            else:
                for j in range(0, len(word), chars_per_line):
                    lines.append(word[j:j + chars_per_line])
                cur = ""
    if cur:
        lines.append(cur)
    return lines


def _draw_speech_bubble(draw, font, now_ms: float, text: str,
                        alien_col: int, alien_y_px: int) -> None:
    """ASCII-art speech bubble, drawn entirely with character glyphs to
    match the alien's aesthetic. Tail (`<` and `\\`) extrudes left toward
    the mouth. Multi-line word wrap + line scroll on overflow."""
    char_w = CELL_W
    char_h = CELL_H
    W = 28          # outer bubble width in chars
    H = 11          # outer bubble height in rows
    bcol = 16       # bubble left col (where `/` corner sits)
    brow = 5        # bubble top row (top edge underscores)
    frame_color = (0xF8, 0xF8, 0xF0)
    text_color = (0x28, 0x18, 0x10)
    # Aged-parchment interior — warmer / yellower than cream.
    bubble_fill = (0xE8, 0xD4, 0xA8)

    # Frame strings.
    top_edge   = " " + "_" * (W - 2) + " "
    top_corner = "/" + " " * (W - 2) + "\\"
    side       = "|" + " " * (W - 2) + "|"
    bot        = "\\" + "_" * (W - 2) + "/"
    tail_row   = " " * (W - 1) + "|"   # right wall only; left replaced by tail

    bx_px = bcol * char_w
    by_px = brow * char_h

    # Centered fill inside the ASCII frame: inset 1 char + 2px on every
    # side so the `/`, `\`, `|`, `_` frame chars draw cleanly on top.
    fill_x = (bcol + 1) * char_w + 2
    fill_y = (brow + 1) * char_h + 2
    fill_w = (W - 2) * char_w - 4
    fill_h = (H - 2) * char_h - 4
    draw.rectangle((fill_x, fill_y, fill_x + fill_w, fill_y + fill_h),
                   fill=bubble_fill)

    def put_str(col, row, s, color=frame_color):
        draw.text((col * char_w, row * char_h), s, font=font, fill=color)

    put_str(bcol, brow,         top_edge)
    put_str(bcol, brow + 1,     top_corner)
    # Tail mid (row 2): `<` two chars left of bubble.
    put_str(bcol - 2, brow + 2, "<")
    put_str(bcol,     brow + 2, tail_row)
    # Tail bot (row 3): `\` one char left of bubble.
    put_str(bcol - 1, brow + 3, "\\")
    put_str(bcol,     brow + 3, tail_row)
    # Body sides.
    for r in range(4, H - 1):
        put_str(bcol, brow + r, side)
    # Bottom.
    put_str(bcol, brow + H - 1, bot)

    # Text — wrap into lines, scroll if overflow.
    text_col = bcol + 2
    chars_per_line = max(4, W - 4)
    text_top_row = brow + 2
    max_lines = (H - 1) - 2   # rows 2..H-2 inclusive

    lines = _wrap_text(text, chars_per_line)
    if len(lines) > max_lines:
        scroll = int(now_ms / 2000.0) % (len(lines) - max_lines + 1)
    else:
        scroll = 0
    for i in range(max_lines):
        ix = scroll + i
        if ix >= len(lines):
            break
        put_str(text_col, text_top_row + i, lines[ix], text_color)


def _render_landscape(state: State) -> Image.Image:
    img = Image.new("RGB", (SCREEN_W, SCREEN_H))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_DIR / "IBMPlexMono-Regular.ttf"), 12)
    bigfont = ImageFont.truetype(str(FONT_DIR / "IBMPlexMono-Medium.ttf"), 28)

    now_ms = state.now_ms
    is_day = (state.hour >= 6.0 and state.hour < 18.0)

    # Background — sky band + grass band. Grass brightens during the day.
    sky = _sky_color(state.hour, state.lightning)
    night = (0x10, 0x16, 0x10)
    day = (0x28, 0x38, 0x1C)
    if 8.0 <= state.hour < 16.0:
        day_f = 1.0
    elif 5.0 <= state.hour < 8.0:
        day_f = (state.hour - 5.0) / 3.0
    elif 16.0 <= state.hour < 19.0:
        day_f = 1.0 - (state.hour - 16.0) / 3.0
    else:
        day_f = 0.0
    grass = tuple(int(night[i] + (day[i] - night[i]) * day_f) for i in range(3))
    # Cycle 2.24 — cloud shadow on the foreground.
    shadow_strength = max(0.0, (state.cloud_pct - 50.0) / 50.0) * 0.18
    # Cycle 2.27 — golden hour. ~30 min around sunrise (6.0) and sunset
    # (17.0), warm amber lights up the foreground. Suppressed by overcast.
    sunrise_t = max(0.0, 1.0 - abs(state.hour - 6.0) / 0.7)
    sunset_t  = max(0.0, 1.0 - abs(state.hour - 17.0) / 0.7)
    golden_strength = max(sunrise_t, sunset_t) * 0.55 * (1.0 - shadow_strength)
    def shade(c):
        c = (
            _clamp(int(c[0] + 55 * golden_strength), 0, 255),
            _clamp(int(c[1] + 25 * golden_strength), 0, 255),
            _clamp(int(c[2] - 20 * golden_strength), 0, 255),
        )
        return tuple(int(v * (1.0 - shadow_strength)) for v in c)
    if shadow_strength > 0.0 or golden_strength > 0.0:
        grass = shade(grass)
    draw.rectangle((0, 0, SCREEN_W, HORIZON_ROW * CELL_H), fill=sky)
    draw.rectangle((0, HORIZON_ROW * CELL_H, SCREEN_W, SCREEN_H), fill=grass)

    tint = _bilinear_tint(state.mood_valence, state.mood_arousal)

    def put(col, row, color, ch):
        if col < 0 or col >= COLS or row < 0 or row >= ROWS:
            return
        draw.text((col * CELL_W, row * CELL_H), ch, font=font, fill=color)

    # Sun position (12-hour parabola; mirror of lambda).
    sun_x = -1
    sun_y = -1
    sun_glyph = "o"
    if is_day:
        t_day = (state.hour - 6.0) / 12.0
        sun_x = int(t_day * (COLS - 1))
        sun_y = int(round(6.0 - math.sin(math.pi * t_day) * 6.0))
        sun_y = _clamp(sun_y, 0, 6)
        sun_glyph = "@" if sun_y >= 5 else "O" if sun_y >= 3 else "o"

    # Stars at night (cloud_pct < 50%).
    if (not is_day) and state.cloud_pct < 50.0:
        # Cycle 2.26 — shooting star. Each 30s window has a ~30% chance
        # of a meteor flashing diagonally across the sky for ~800ms.
        # Trajectory + timing seeded from the window index, so renders
        # are deterministic across refreshes within a window.
        win_id = int(now_ms / 30000.0)
        h = (win_id * 2654435761) & 0xFFFFFFFF
        if (h % 100) < 30:
            fire_ms = (h >> 8) % 25000
            elapsed = (now_ms % 30000) - fire_ms
            if 0 <= elapsed <= 800:
                p = elapsed / 800.0
                start_col = 30 + ((h >> 16) % 10)   # right side
                start_row = ((h >> 24) % 4)          # top band
                end_col = 4
                end_row = 6
                cur_c = start_col + (end_col - start_col) * p
                cur_r = start_row + (end_row - start_row) * p
                # Head + 3-glyph fading tail
                head_color = (0xF8, 0xF4, 0xD8)
                put(int(round(cur_c)), int(round(cur_r)), head_color, "*")
                for i in range(1, 4):
                    tp = max(0.0, p - i * 0.06)
                    tc = start_col + (end_col - start_col) * tp
                    tr = start_row + (end_row - start_row) * tp
                    fade = 1.0 - i * 0.28
                    tail_color = tuple(int(c * fade) for c in head_color)
                    glyph = "-" if i == 1 else "."
                    put(int(round(tc)), int(round(tr)), tail_color, glyph)
        for sx, sy, phase in STARS:
            b = (math.sin(now_ms / 700.0 + phase) + 1.0) * 0.5
            if b > 0.65:    g = "*"
            elif b > 0.30: g = "."
            else:           g = None
            if g:
                put(sx, sy, (0xD4, 0xCC, 0xB8), g)

    def render_celestial(cx, cy, body_col, pulse_period_ms, body_r):
        if cx < 0 or cy < 0:
            return
        px = cx * CELL_W + CELL_W // 2
        py = cy * CELL_H + CELL_H // 2
        pulse = (math.sin(now_ms / pulse_period_ms) + 1.0) * 0.5

        def add_clamp(base, bonus):
            return int(_clamp(base + bonus, 0, 255))

        # Outer glow — additive on sky_bg.
        outer_r = int(body_r * (2.6 + pulse * 0.8))
        outer_k = 0.20 + pulse * 0.15
        outer = (
            add_clamp(sky[0], body_col[0] * outer_k),
            add_clamp(sky[1], body_col[1] * outer_k),
            add_clamp(sky[2], body_col[2] * outer_k),
        )
        draw.ellipse((px - outer_r, py - outer_r, px + outer_r, py + outer_r),
                     fill=outer)
        # Mid glow — stronger additive lift.
        mid_r = int(body_r * (1.6 + pulse * 0.3))
        mid_k = 0.45 + pulse * 0.20
        mid = (
            add_clamp(sky[0], body_col[0] * mid_k),
            add_clamp(sky[1], body_col[1] * mid_k),
            add_clamp(sky[2], body_col[2] * mid_k),
        )
        draw.ellipse((px - mid_r, py - mid_r, px + mid_r, py + mid_r), fill=mid)
        # Sharp body — full color.
        draw.ellipse((px - body_r, py - body_r, px + body_r, py + body_r),
                     fill=body_col)

    # Sun. Min row 1 (top padding), radius 6 (~20% larger).
    if is_day and state.cloud_pct < 75.0 and sun_x >= 0:
        if sun_y < 1:
            sun_y = 1
        render_celestial(sun_x, sun_y, (0xF4, 0xD0, 0x60), 1500.0, 6)

    # Moon. Same top padding + bigger body radius 5.
    if (not is_day) and state.cloud_pct < 75.0:
        if state.hour >= 18.0:
            t_night = (state.hour - 18.0) / 12.0
        elif state.hour < 6.0:
            t_night = (state.hour + 6.0) / 12.0
        else:
            t_night = 0.5
        moon_x = int(t_night * (COLS - 1))
        moon_y = int(round(6.0 - math.sin(math.pi * t_night) * 6.0))
        moon_y = _clamp(moon_y, 1, 6)
        render_celestial(moon_x, moon_y, (0xC8, 0xCC, 0xD8), 2400.0, 5)

    # Clouds — high-contrast glyphs only, no body fill.
    is_raining = state.precip > 0.05
    n_clouds = math.ceil(state.cloud_pct / 30.0)
    n_clouds = _clamp(n_clouds, 0, MAX_CLOUDS)
    cc = (0x5A, 0x60, 0x68) if is_raining else (0xE8, 0xEC, 0xF0)
    for c in range(n_clouds):
        base_x = (c * 11 + int((now_ms / 100) % COLS)) % (COLS + 8) - 4
        idx = c & 3
        y = CLOUD_Y[c]
        main = CLOUD_MAIN[idx]
        main_len = len(main)
        for k, ch in enumerate(main):
            if is_raining:
                if ch == '~': ch = '='
                elif ch == '-': ch = '+'
            put(base_x + k, y, cc, ch)
        top = CLOUD_TOP[idx]
        if top and y > 0:
            for k, ch in enumerate(top):
                if is_raining and ch == '~': ch = '='
                put(base_x + CLOUD_TOP_OFFSET[idx] + k, y - 1, cc, ch)

    # Grass — wave-pattern density gradient. Blades bend toward the wind:
    # calm → upright glyphs (' , : .); moderate east/west wind → leaning
    # \ or / blades; strong wind → flattened _.
    row_density = [0]*10 + [10, 20, 35, 50, 65, 78]
    wind_east = math.sin(math.radians(state.wind_dir))
    wind_dir_sign = 1.0 if wind_east >= 0 else -1.0
    wave_speed = wind_dir_sign * (0.6 + state.wind_kmh / 8.0)
    wave_pos = (now_ms / 1000.0) * wave_speed
    # bend_strength uses the east-west *component* of wind, not just speed,
    # so a north wind doesn't bend grass east or west.
    bend_strength = abs(wind_east) * state.wind_kmh / 30.0
    lean_glyph = "\\" if wind_east >= 0 else "/"
    for row in range(10, ROWS):
        dens = row_density[row]
        row_phase = (row - 10) * 0.4
        for col in range(COLS):
            h = (col * 2654435761 + row * 1597334677) & 0xFFFFFFFF
            roll = (h >> 8) % 100
            if roll >= dens:
                continue
            ph = (col - wave_pos) * 0.30 + row_phase
            wv = math.sin(ph)
            if bend_strength < 0.20:
                if wv > 0.55:    g = "'"
                elif wv > 0.05:  g = ","
                elif wv > -0.55: g = ":"
                else:             g = "."
            elif bend_strength < 0.65:
                # Leaning — wave peaks lift as a leaning blade, troughs lie low.
                if wv > 0.30:    g = lean_glyph
                elif wv > -0.30: g = ","
                else:             g = "."
            else:
                # Flattened — a few blades still leaning, most pressed flat.
                if wv > 0.40:    g = lean_glyph
                else:             g = "_"
            put(col, row, shade((0x46, 0x60, 0x32)), g)

    # Trees — three planes, triangular crowns.
    plane_blend = [0.65, 0.35, 0.0]
    plane_sway  = [0.0,  0.6,  1.0]
    sway_amp = _clamp(state.wind_kmh / 25.0, 0.0, 3.0)
    foliage_body_pal = [
        shade((0x4A, 0x68, 0x28)),
        shade((0x68, 0x78, 0x30)),
        shade((0x38, 0x5A, 0x28)),
        shade((0x58, 0x6E, 0x38)),
    ]
    foliage_hi_pal = [
        shade((0x8A, 0xA8, 0x58)),
        shade((0xA0, 0xB0, 0x48)),
        shade((0x70, 0x9A, 0x48)),
        shade((0x9A, 0xB0, 0x68)),
    ]
    trunk_base = shade((0x3C, 0x28, 0x18))
    for t in range(N_TREES):
        p = T_PLANE[t]
        base_col = T_COLS[t]
        h = T_H[t]
        cw = T_CW[t]
        pb = plane_blend[p]
        # Per-tree foliage colors from palette.
        hi_rgb = foliage_hi_pal[t & 3]
        tree_color = tuple(
            int(hi_rgb[i] * (1 - pb) + grass[i] * pb) for i in range(3)
        )
        trunk_color = (
            int(0x55 * (1 - pb) + 0x10 * pb),
            int(0x38 * (1 - pb) + 0x16 * pb),
            int(0x20 * (1 - pb) + 0x10 * pb),
        )
        if p == 0:
            for rk in range(h):
                r = HORIZON_ROW - rk
                g = "*" if rk == 0 else ("'" if (t & 1) else ",")
                put(base_col, r, tree_color, g)
            continue

        wind_sway = math.sin(now_ms / 800.0 + TREE_PHASES[t]) * sway_amp
        micro_a   = math.sin(now_ms / 4500.0 + TREE_PHASES[t] * 1.5) * 0.6
        micro_b   = math.sin(now_ms / 1700.0 + TREE_PHASES[t] * 2.3) * 0.5
        crown_sway = (wind_sway + micro_a + micro_b) * plane_sway[p]
        trunk_sway = (wind_sway * 0.4 + (micro_a + micro_b) * 0.5) * plane_sway[p]
        crown_dx = round(crown_sway) + T_LEAN[t]
        trunk_dx = round(trunk_sway)

        trunk_h = T_TRUNK_H[t]
        if trunk_h > h - 1:
            trunk_h = h - 1
        crown_h = max(1, h - trunk_h)
        crown_top = max(0, ROWS - h)
        trunk_top = min(ROWS, crown_top + crown_h)
        trunk_w = 2 if (p == 2 and trunk_h >= 2) else 1

        # Per-plane blended body colors.
        trunk_body = tuple(
            int(trunk_base[i] * (1 - pb) + grass[i] * pb) for i in range(3)
        )
        body_rgb = foliage_body_pal[t & 3]
        foliage_body = tuple(
            int(body_rgb[i] * (1 - pb) + grass[i] * pb) for i in range(3)
        )

        # Trunk body fill — narrow brown rectangle.
        if trunk_h > 0:
            trunk_rect_left = (base_col + trunk_dx) * CELL_W + 1
            trunk_rect_w    = trunk_w * CELL_W - 2
            trunk_rect_top  = trunk_top * CELL_H
            trunk_rect_h    = (ROWS - trunk_top) * CELL_H
            if trunk_rect_left >= 0 and trunk_rect_left + trunk_rect_w < SCREEN_W:
                draw.rectangle((trunk_rect_left, trunk_rect_top,
                                trunk_rect_left + trunk_rect_w,
                                trunk_rect_top + trunk_rect_h),
                               fill=trunk_body)

        # Trunk glyphs on top.
        trunk_glyphs = ["|", "!"]
        for r in range(trunk_top, ROWS):
            for dx in range(trunk_w):
                gi = (r + dx + t + int(now_ms / 4000)) & 1
                put(base_col + trunk_dx + dx, r, trunk_color, trunk_glyphs[gi])

        # Crown shape depends on species.
        is_oak = (state.tree_species == "oak") or \
                 (state.tree_species == "mixed" and (t & 1))

        # Crown body fill.
        if is_oak:
            body_cx = (base_col + crown_dx) * CELL_W + CELL_W // 2
            body_cy = (crown_top + crown_h // 2) * CELL_H + CELL_H // 2
            body_r  = max(1, (cw * CELL_W) // 2 - 1)
            draw.ellipse((body_cx - body_r, body_cy - body_r,
                          body_cx + body_r, body_cy + body_r), fill=foliage_body)
        else:
            for rk in range(crown_h):
                r = crown_top + rk
                w_here = 1 + (rk * (cw - 1)) // max(1, crown_h - 1)
                w_here = _clamp(w_here, 1, cw)
                half_l = w_here // 2
                rect_left = (base_col + crown_dx - half_l) * CELL_W
                rect_w    = w_here * CELL_W
                rect_top  = r * CELL_H + 2
                rect_h    = CELL_H - 2
                if rect_left + rect_w > 0 and rect_left < SCREEN_W:
                    draw.rectangle((rect_left, rect_top,
                                    rect_left + rect_w, rect_top + rect_h),
                                   fill=foliage_body)

        for rk in range(crown_h):
            r = crown_top + rk
            if is_oak:
                frac = (rk + 1) / (crown_h + 1)
                sw = math.sin(math.pi * frac)
                w_here = int(round(cw * sw))
                w_here = _clamp(w_here, 1, cw)
            else:
                w_here = 1 + (rk * (cw - 1)) // max(1, crown_h - 1)
                w_here = _clamp(w_here, 1, cw)
            half_l = w_here // 2
            half_r = w_here - 1 - half_l
            for k in range(-half_l, half_r + 1):
                if is_oak:
                    if k == -half_l or k == half_r:    g = "*"
                    elif k == 0 and w_here >= 3:        g = "O"
                    else:                                g = "o"
                else:
                    g = "*"
                put(base_col + crown_dx + k, r, tree_color, g)

    # Tight clear-zones — only behind wordmark and behind line band.
    draw.rectangle((110,  98, 110 + 100,  98 + 30), fill=sky)
    draw.rectangle((118, 145, 118 +  84, 145 +  8), fill=sky)
    breath = math.sin((now_ms % 4000.0) / 4000.0 * 2 * math.pi)
    delta = int(breath * 55)
    narada = (
        _clamp(0xD4 + delta, 0, 255),
        _clamp(0xCC + delta, 0, 255),
        _clamp(0xB8 + delta, 0, 255),
    )
    # Center NARADA at (160, 112). PIL anchor "mm" centers the text.
    draw.text((160, 112), "NARADA", font=bigfont, fill=narada, anchor="mm")
    line_extra = int(breath * 8)
    draw.line((120 - line_extra, 148, 200 + line_extra, 148), fill=tint)

    # UFO presence indicator — pixel saucer + `@` indicator only.
    if state.attention_mode != "inward":
        ufo_t = (now_ms % 90000.0) / 90000.0
        ufo_cx = int(ufo_t * (COLS - 5)) + 3
        ufo_y_top = round(math.sin(now_ms / 2200.0) + 1.0)
        ufo_y_bot = ufo_y_top + 1
        if ufo_cx - 2 >= 0 and ufo_cx + 2 < COLS and ufo_y_bot < HORIZON_ROW:
            mode = state.ufo_mode
            if mode == "mood":
                dome_fill = tuple(int(c * 0.7) for c in tint)
                disc_fill = tuple(int(c * 0.45) for c in tint)
                glyph_at  = tint
            else:
                dome_fill = (0x88, 0x8C, 0x98)
                disc_fill = (0x5A, 0x5E, 0x68)
                glyph_at  = (0xE0, 0xE4, 0xEA)
            body_cx = ufo_cx * CELL_W + CELL_W // 2

            # Three layers: main disc (wide), mid disc (bridges
            # shoulder gap), dome (narrow top).
            disc_cy = ufo_y_bot * CELL_H + 4
            disc_half_w = (5 * CELL_W) // 2
            disc_half_h = 3
            rect_x = body_cx - disc_half_w + disc_half_h
            rect_w = (disc_half_w - disc_half_h) * 2
            rect_y = disc_cy - disc_half_h
            rect_h = disc_half_h * 2
            mid_cy = ufo_y_top * CELL_H + CELL_H + 1
            mid_half_w = 13
            mid_half_h = 3
            mid_rect_x = body_cx - mid_half_w + mid_half_h
            mid_rect_w = (mid_half_w - mid_half_h) * 2
            mid_rect_y = mid_cy - mid_half_h
            mid_rect_h = mid_half_h * 2
            dome_cy = ufo_y_top * CELL_H + CELL_H - 4

            def _shape(method, *args, **kwargs):
                method(*args, **kwargs)

            if mode == "outline":
                draw.rectangle((rect_x, rect_y, rect_x + rect_w, rect_y + rect_h),
                               outline=disc_fill)
                draw.ellipse((body_cx - disc_half_w + disc_half_h - disc_half_h,
                              disc_cy - disc_half_h,
                              body_cx - disc_half_w + disc_half_h + disc_half_h,
                              disc_cy + disc_half_h), outline=disc_fill)
                draw.ellipse((body_cx + disc_half_w - disc_half_h - disc_half_h,
                              disc_cy - disc_half_h,
                              body_cx + disc_half_w - disc_half_h + disc_half_h,
                              disc_cy + disc_half_h), outline=disc_fill)
                draw.rectangle((mid_rect_x, mid_rect_y,
                                mid_rect_x + mid_rect_w, mid_rect_y + mid_rect_h),
                               outline=dome_fill)
                draw.ellipse((body_cx - mid_half_w + mid_half_h - mid_half_h,
                              mid_cy - mid_half_h,
                              body_cx - mid_half_w + mid_half_h + mid_half_h,
                              mid_cy + mid_half_h), outline=dome_fill)
                draw.ellipse((body_cx + mid_half_w - mid_half_h - mid_half_h,
                              mid_cy - mid_half_h,
                              body_cx + mid_half_w - mid_half_h + mid_half_h,
                              mid_cy + mid_half_h), outline=dome_fill)
                draw.ellipse((body_cx - 9, dome_cy - 9, body_cx + 9, dome_cy + 9),
                             outline=dome_fill)
            else:
                # Main disc
                draw.rectangle((rect_x, rect_y, rect_x + rect_w, rect_y + rect_h),
                               fill=disc_fill)
                draw.ellipse((body_cx - disc_half_w + disc_half_h - disc_half_h,
                              disc_cy - disc_half_h,
                              body_cx - disc_half_w + disc_half_h + disc_half_h,
                              disc_cy + disc_half_h), fill=disc_fill)
                draw.ellipse((body_cx + disc_half_w - disc_half_h - disc_half_h,
                              disc_cy - disc_half_h,
                              body_cx + disc_half_w - disc_half_h + disc_half_h,
                              disc_cy + disc_half_h), fill=disc_fill)
                # Mid disc
                draw.rectangle((mid_rect_x, mid_rect_y,
                                mid_rect_x + mid_rect_w, mid_rect_y + mid_rect_h),
                               fill=dome_fill)
                draw.ellipse((body_cx - mid_half_w + mid_half_h - mid_half_h,
                              mid_cy - mid_half_h,
                              body_cx - mid_half_w + mid_half_h + mid_half_h,
                              mid_cy + mid_half_h), fill=dome_fill)
                draw.ellipse((body_cx + mid_half_w - mid_half_h - mid_half_h,
                              mid_cy - mid_half_h,
                              body_cx + mid_half_w - mid_half_h + mid_half_h,
                              mid_cy + mid_half_h), fill=dome_fill)
                # Dome
                draw.ellipse((body_cx - 9, dome_cy - 9, body_cx + 9, dome_cy + 9),
                             fill=dome_fill)

            # @ indicator (centered on dome)
            draw.text((body_cx, dome_cy + 1), "@", font=font, fill=glyph_at,
                      anchor="mm")

    # Cycle 2.25 — butterfly on calm sunlit days. Drifts horizontally low
    # over the grass; wings flutter between two glyph pairs. Drawn last
    # so it passes in front of everything (wordmark, UFO, trees).
    is_calm = state.wind_kmh < 8.0
    is_dry = state.precip < 0.05
    if is_day and is_calm and is_dry and state.attention_mode != "inward":
        bf_t = (now_ms % 60000.0) / 60000.0       # 60s traversal
        bf_cx = bf_t * (COLS + 6) - 3              # margin so it enters/exits
        bf_y = 10.0 + math.sin(now_ms / 1500.0) * 0.6
        bf_y_int = int(round(bf_y))
        bf_cx_int = int(round(bf_cx))
        if 1 <= bf_cx_int <= COLS - 2 and 8 <= bf_y_int <= 11:
            flutter = int(now_ms / 140.0) & 1
            if flutter:
                wl, wr = "(", ")"
            else:
                wl, wr = "\\", "/"
            bf_color = tuple(_clamp(int(c * 1.15), 0, 255) for c in tint)
            put(bf_cx_int - 1, bf_y_int, bf_color, wl)
            put(bf_cx_int,     bf_y_int, bf_color, "o")
            put(bf_cx_int + 1, bf_y_int, bf_color, wr)

    return img


def _live_state() -> State:
    """Pull current state from the device via aioesphomeapi."""
    import asyncio
    from aioesphomeapi import APIClient

    async def go():
        c = APIClient("192.168.86.35", 6053, password="")
        await c.connect(login=True)
        entities, _ = await c.list_entities_services()
        by_key = {e.key: e for e in entities if type(e).__name__ == "TextSensorInfo"}
        seen: dict[str, str] = {}
        def on_state(s):
            if type(s).__name__ == "TextSensorState" and s.key in by_key:
                seen[by_key[s.key].name] = s.state
        c.subscribe_states(on_state)
        await asyncio.sleep(2.0)
        await c.disconnect()
        return seen

    seen = asyncio.run(go())
    def f(name, default):
        try: return float(seen.get(name, default))
        except: return default
    s = State()
    s.mood_valence    = f("Mood Valence", s.mood_valence)
    s.mood_arousal    = f("Mood Arousal", s.mood_arousal)
    s.sleep_pressure  = f("Vitality Sleep Pressure", s.sleep_pressure)
    s.restlessness    = f("Vitality Restlessness", s.restlessness)
    s.attention_mode  = seen.get("Attention Mode", s.attention_mode)
    s.wind_kmh        = f("Weather Wind Speed Kmh", s.wind_kmh)
    s.wind_dir        = f("Weather Wind Direction Deg", s.wind_dir)
    s.precip          = f("Weather Precipitation Mm Hr", s.precip)
    s.cloud_pct       = f("Weather Cloud Cover Pct", s.cloud_pct)
    s.weather_code    = int(f("Weather Code", s.weather_code))
    return s


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", default="preview.png")
    p.add_argument("--hour", type=float, default=None,
                   help="Brisbane hour 0..24 (default: now)")
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
    p.add_argument("--bhumi", default="landscape",
                   choices=["landscape", "ufo_interior"])
    p.add_argument("--alien-activity", default="",
                   help="Override alien preset (resting, speaking, listening, "
                        "thinking, working, dreaming, walking_right, "
                        "walking_left, greeting, surprised, singing)")
    p.add_argument("--alien-mood", default="neutral",
                   choices=["neutral", "happy", "upset", "sad", "playful"])
    p.add_argument("--alien-speech", default="",
                   help="Text to display in speech bubble (only when speaking)")
    p.add_argument("--anim-ms", type=float, default=None,
                   help="Animation timestamp (default: real time)")
    p.add_argument("--live", action="store_true",
                   help="Pull current state from device")
    args = p.parse_args()

    if args.live:
        s = _live_state()
        s.tree_species = args.tree_species
        s.ufo_mode = args.ufo_mode
        s.bhumi = args.bhumi
        s.alien_activity = args.alien_activity
        s.alien_mood = args.alien_mood
        s.alien_speech = args.alien_speech
    else:
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

    if args.anim_ms is None:
        import time as _t
        s.now_ms = (_t.time() * 1000) % 1_000_000
    else:
        s.now_ms = args.anim_ms

    img = render(s)
    img.save(args.out)
    print(f"Wrote {args.out}  (hour={s.hour:.1f}, cloud={s.cloud_pct:.0f}%, "
          f"wind={s.wind_kmh:.1f} km/h @ {s.wind_dir:.0f}°)")


if __name__ == "__main__":
    main()
