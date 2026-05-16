# Narada voice-state illustrations

Drop the 5 generated PNGs here. ESPHome bakes them into flash at build
time when `narada-faces.yaml` is compiled.

## Required files (320x240 RGB+alpha PNG each)

| Filename               | Voice assistant state            |
|------------------------|----------------------------------|
| `state_idle.png`       | Default / standby (also loading) |
| `state_listening.png`  | Wake word fired, recording       |
| `state_thinking.png`   | Between speech-end and TTS-start |
| `state_speaking.png`   | Narada is talking back           |
| `state_error.png`      | Pipeline failure / unreachable   |

## Notes

- Resolution: **320×240 landscape**. Source larger than this is fine —
  ESPHome's `image:` block resizes at build time (`resize: 320x240`).
- Format: RGB, alpha channel honored. Areas outside the figure can be
  transparent if you want them blended with the LVGL background color,
  or opaque if the PNG IS the whole scene.
- Source generations from Midjourney can be 1024×768 or larger; the
  build script will downscale.
- This directory is in-tree but the PNGs themselves are NOT in git yet
  — Suti generates and commits them when a set is settled.
