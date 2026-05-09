# Fonts

Typefaces used by the ESP32 body firmware. Tracked in git so the body is
reproducible from a clean clone — no fetching, no gfonts dependency, no
silent glyph substitutions.

## What's here

- **IBM Plex Mono** (full family — Thin → Bold, with italics)
  - Substrate, wordmark, all general rendering.
  - Reference: `embodiment/design/v1.0-vocabulary.md` §Typeface.
  - Used in `narada-body.yaml`: `IBMPlexMono-Regular.ttf` at 12px,
    `IBMPlexMono-Medium.ttf` at 28px.
  - Note: this font does **not** ship Block Elements, Box Drawing, or
    geometric circles. The visual vocabulary is constrained to ASCII +
    Latin-1 in consequence — terminal-pure aesthetic.

- **IBM Plex Sans Devanagari** (full family — Thin → Bold)
  - Reserved for the rare `नारद` glyph, rendered at deep-rest /
    session-start moments. Sibling of IBM Plex Mono; visual register
    matches.
  - Not yet referenced in firmware — pending Cycle 6+.

## License

Both families are released under SIL Open Font License 1.1 by IBM. See
`https://github.com/IBM/plex` for the upstream and the LICENSE.txt files
distributed in the IBM Plex repo. Redistribution and embedding are
permitted under OFL terms, including in this firmware.

## Why local TTFs (not gfonts)

Earlier cycles used ESPHome's `gfonts` integration which fetched IBM Plex
Mono from Google Fonts at compile time. That setup compiled cleanly even
when we asked for glyphs the font didn't carry — the missing glyphs
rendered as tofu boxes (which were happily mistaken for mountain texture).
Switching to local TTFs makes the glyph coverage explicit at compile time
and makes the build hermetic.

## When changing fonts

1. Drop new TTFs in this directory.
2. Update the `font:` block in `embodiment/firmware/narada-body.yaml`.
3. Update the glyph subset to what the new font actually carries —
   verify with `fontTools.ttLib.TTFont` if uncertain.
4. Update `embodiment/design/v1.0-vocabulary.md` §Typeface.
