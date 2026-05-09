# embodiment/assets/

Static assets for Narada's embodiment device — display graphics and
audio samples that are compiled into firmware or served from flash.

## Purpose

The ESP32-S3-BOX-3 has a display (320×240 LCD) and speaker. This
directory holds the source assets that cycle BUILD work converts into
device-ready formats.

## Format constraints (ESP32 flash budget)

| Type | Format | Max size | Notes |
|------|--------|----------|-------|
| Display bitmaps | PNG → C array (LVGL) | 320×240 px | 1-bit or 4-bit for OLED; 16-bit for LCD |
| Icons / glyphs | PNG → C array | 64×64 px | Used on heartbeat status pages |
| Fonts | `.ttf` → ESPHome font | — | Subset to needed glyphs to save flash |
| Audio samples | WAV (16-bit, 16kHz mono) | ~100 KB | Longer samples need streaming |
| Mantras (TTS) | WAV (16-bit, 16kHz mono) | ~100 KB each | Pre-rendered; not generated on device |

Total usable flash for assets: ~2 MB (after firmware). Budget carefully.

## Subdirectories (to be created as needed)

```
assets/
  bitmaps/     # PNG source files for display graphics
  icons/       # Small PNG glyphs (status indicators, mantra symbols)
  fonts/       # TTF source fonts before ESPHome subsetting
  audio/       # WAV samples: notification tones, mantra recitations
```

## Conversion pipeline

ESPHome handles font and image conversion at compile time via its
`image:` and `font:` components. Reference assets from firmware YAML:

```yaml
image:
  - file: embodiment/assets/bitmaps/narada-idle.png
    id: img_idle
    type: GRAYSCALE

font:
  - file: embodiment/assets/fonts/NotoSans-Regular.ttf
    id: font_status
    size: 14
    glyphs: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:!?"
```

Audio samples are loaded separately via the `media_player:` component
or pre-baked into flash with `esp_adf`.
