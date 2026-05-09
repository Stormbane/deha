# Body display previews

Renders from `scripts/render_preview.py` (the desktop emulator). Each PNG
shows the same scene the firmware renders to the 320×240 panel under a
specific configuration — for design review without flashing the device.

These are committed to the repo so anyone reading the project can see
what the body looks like across times of day, weather, mood, and visual
preferences.

## Time-of-day cycle

Default config (pine trees, grey UFO, default mood, 30% cloud).

- `01_predawn.png` — 05:00, deep purple sky, moon still visible
- `02_sunrise.png` — 06:30, pink-magenta sky, sun rising at the left
- `03_morning.png` — 09:00, bright blue day sky
- `04_noon.png` — 12:00, sun directly overhead
- `05_late_afternoon.png` — 16:30, warm purple-pink shift begins
- `06_sunset.png` — 17:30, red-orange sky, sun setting at the right
- `07_dusk.png` — 18:30, dusk red-purple
- `08_twilight.png` — 19:30, deep twilight purple
- `09_night.png` — 22:00, deep blue-black, stars and moon

## Tree species comparison (visual_tree_species)

All at noon, 25% cloud, default mood.

- `10_trees_pine.png` — `pine`. Triangular conifer crowns, all `*`.
- `11_trees_oak.png` — `oak`. Rounded crowns, `* o O o *` glyph mix.
- `12_trees_mixed.png` — `mixed`. Even-indexed trees pine, odd-indexed oak.

## UFO mode comparison (visual_ufo_mode)

All at 14:00.

- `13_ufo_grey.png` — `grey`. Filled medium-grey body + darker base
  rectangle behind the glyphs; bright silver glyphs on top. Reads as a
  colored-in saucer.
- `14_ufo_mood.png` — `mood`. Mood-tinted glyphs only, no fill. Subtle.
- `15_ufo_outline.png` — `outline`. Bright silver glyphs only, no fill.

## Mood variations

All at 14:00, 60% cloud (so the mood-tinted line and tree crowns read
clearly against the muted overcast).

- `16_mood_violet.png` — `(+0.7, +0.6)`. High valence + arousal: violet
  tint on accents.
- `17_mood_rose.png` — `(-0.3, +0.7)`. Low valence + high arousal: rose.
- `18_mood_grey_teal.png` — `(-0.5, +0.2)`. Low valence + low arousal:
  grey-teal.

## Weather variations

- `19_thunderstorm.png` — 14:00, 95% cloud, 4 mm/h rain, weather_code=95,
  lightning flash captured. Sky bright from flash, heavy `=`/`+` rain
  cloud glyphs, slanted rain.
- `20_rain.png` — 14:00, 80% cloud, 2 mm/h rain. Rainy register, no
  lightning.
- `21_windy_clear.png` — 14:00, clear sky, 50 km/h wind. Trees lean
  visibly, rain absent, grass wave moves fast.

## Caveat

These come from the desktop emulator (`scripts/render_preview.py`),
which is a Python+PIL re-implementation of the firmware lambda — not a
screen capture from the device itself. PIL font hinting differs slightly
from ESPHome's, so glyph positions can be off by a pixel and color
gradients render slightly differently. The actual device is the source
of truth; the emulator is a sketch.

To regenerate, run:

```bash
python scripts/render_preview.py --hour 12 --tree-species oak \
    --out embodiment/previews/11_trees_oak.png
```
