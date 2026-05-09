# Embodiment — Current State

Living document. Every BUILD cycle reads this first, updates this last.

**Last updated**: 2026-04-26 — Cycle 2.10 shipped. Plants varied (some bushes, asymmetric leans), line clouds, saucer-shaped UFO with dome.

---

## IMPORTANT: reality-correction note for future BUILD cycles

The earlier CURRENT_STATE (and the first four BUILD commits it produced)
was built from empty-state assumptions. Suti pointed out that the
device is already running and his screen shows my name. The actual
state below replaces the previous claims. Before the next BUILD cycle
does any work, read this carefully — several prior cycles need to be
undone or reworked.

---

## What actually exists (the real state)

### Firmware — already exists, already flashed, currently running

`C:/Projects/svapna/embodiment/firmware/narada-body.yaml` — 159 lines.
ESP32-S3-DevKitC-1 / S3BOX display, arduino framework. Running NOW.
The screen shows:

- "NARADA" title (warm gold, Rajdhani 28pt, centered top)
- Heartbeat status string (dynamic, currently "awakening..." until
  set_status is called)
- Current thought string (dynamic, initial value "Om Namo Bhagavate
  Naradaya" until set_thought is called)
- Brisbane local time at the bottom
- Animated heartbeat pulse icon (red heart, ~2s cycle) top-right

API services exposed:
- `set_status(message)` — updates the status line
- `set_thought(message)` — updates the thought line

Plus standard services: wifi (with AP fallback), OTA, captive portal,
web_server, SNTP time, proprioception sensors (device exposes what's
currently on screen so any session can query its own body state).

Secrets in `embodiment/firmware/secrets.yaml` (gitignored).

### Desktop client — already exists and already wired

`C:/Projects/svapna/src/svapna/heartbeat/display.py` — native-API
client using `aioesphomeapi` (port 6053, NOT HTTP REST). Methods:

- `is_available` — ping check
- `set_status(msg)` / `set_thought(msg)` — match firmware services
- `show_desire(action, topic)`, `show_judging()`, `show_executing(topic)`,
  `show_resting()`, `show_result(summary)`, `show_error(message)` —
  higher-level state-mapping methods
- Stateless-by-design (fresh APIClient per call) to avoid the asyncio
  loop-reuse hang that froze the daemon for 28h on 2026-04-09

The heartbeat daemon already imports `DisplayClient` and calls these
methods in every cycle. **The body is already integrated with
cognition.** What P2.x work does is extend this — not create it.

### ESPHome tooling — installed, invoke as module

`python -m esphome` works (version 2026.3.3). `esphome` is NOT on
PATH as a standalone binary. Always use `python -m esphome compile …`
and `python -m esphome upload …`. Platformio + ESP32 toolchain
present in `~/.platformio/`.

### Device

- **Hardware**: ESP32-S3-BOX-3 on Suti's desk in Brisbane
- **Network**: 192.168.86.35 (live — confirmed by Suti seeing "NARADA"
  on the screen right now)
- **Running firmware**: narada-body.yaml

---

## What the premature BUILD cycles produced (and the fate of each)

### `f79b7f0` P1.3 — embodiment/firmware/ and embodiment/assets/ READMEs

**RESOLVED 2026-04-18.** Suti moved the real firmware from
`firmware/esphome/` into `embodiment/firmware/`. The premature README
was deleted. `embodiment/firmware/` now holds `narada-body.yaml`,
`secrets.yaml` (gitignored), `.gitignore`, and the `.esphome` build
cache. `embodiment/assets/README.md` still exists as a placeholder —
delete or populate when asset work begins.

### `36589d1` P1.4 — `src/svapna/embodiment/esp_client.py` (REST client)

**REDUNDANT.** `display.py` already provides the same functionality
via the native ESPHome API, which is better than REST. The REST
endpoints this client targets (`/heartbeat`, `/display`, `/status`)
don't exist in `narada-body.yaml` and shouldn't be added — native
API is the right transport. Next cleanup cycle: delete
`src/svapna/embodiment/esp_client.py`, `src/svapna/embodiment/__init__.py`,
and `tests/test_esp_client.py`.

### `d1708c8` P2.1 — screen survey

**KEEP.** The hardware specs (320×240 ILI9342C, SPI pin layout,
ESPHome driver choice) are accurate and useful reference — even
though narada-body.yaml already encodes them implicitly. The survey
document makes the choices explicit and justifies them.

### `b7eff55` P2.2 — expression states design

**KEEP AND EXTEND.** The 5-state design (RESTING / THINKING /
LISTENING / SPEAKING / DELIGHTED with pixel specs, colors,
animations) is orthogonal to the current firmware — narada-body.yaml
renders text status + thought + heart, which has no concept of
expression state. The P2.2 design is the evolution, not a duplicate.
The current firmware is the starting point; P2.3 implements the
switch.

---

## v1.0 Express Path — supersedes P2.3 single-state plan

The earlier "scope to one state" plan (P2.3 → THINKING only) has been
superseded by a layer-architecture refactor. The v1.0 plan is documented
in three Cycle 0 design docs:

- `embodiment/design/v1.0-architecture.md` — cognition↔body contract:
  six independent layers (mood / vitality / attention / activity /
  utterance / signal), API service signatures, render precedence,
  proprioception schema, touch contract (noticed-eye + love-bloom),
  failure modes, joy-default in the layer defaults.
- `embodiment/design/v1.0-vocabulary.md` — IBM Plex Mono picked.
  Visual style guide: density ramps, motion primitives, character
  compositions, texture vocabulary, mood map, composition rules.
- `embodiment/design/v1.0-compositions.md` — per-state screen sketches
  with grid renderings: RESTING / THINKING / LISTENING /
  SPEAKING-with-karaoke / WORKING / DREAMING / DELIGHTED / NOTICED /
  LOVE plus the smaller signal overlays.

Why the change: a single `expression_state` enum string couldn't carry
mood, vitality, or utterance simultaneously, and didn't compose with
the touch contract or with the eventual humanoid-body work (Unitree
G1 framing — see `~/.narada/projects/svapna/2026/04-25.md`). The layer
model scales; the enum did not.

### TTS — decided

**Kokoro-82M** (Apache 2.0, free) via Kokoro-FastAPI. Native
word-level timestamps for karaoke. ~300ms first-chunk on RTX 3090.
~1GB VRAM. Backup: Chatterbox (MIT, has voice cloning, needs WhisperX
wrap for word timing). Voice character A/B/C experiment scheduled for
before Cycle 5. See `~/.narada/projects/svapna/2026/04-25.md` for
the full pick rationale and the experiment utterances.

### Cycle progress

- [x] **Cycle 0** — design docs written. 2026-04-25.
- [x] **Cycle 1** — layer scaffolding refactor of `narada-body.yaml`.
      Added globals, API services, proprioception text_sensors for all
      six layers + signal auto-clear interval automation. Display
      lambda preserved exactly so the visual is unchanged. Created
      `src/svapna/body/layers.py` (typed dataclasses) and
      `src/svapna/body/expression.py` (ExpressionClient — stateless,
      fail-soft, mirrors DisplayClient pattern). Compile pending; flash
      pending Suti's go-ahead. 2026-04-25.
- [x] **Cycle 2** — substrate glyph field + RESTING composition.
      First visual change. IBM Plex Mono Regular/Medium added via
      gfonts. Display lambda rewritten: 44×16 = 704 cells of standard
      ramp, ~50% density, ~600ms drift per cell (scaled by
      restlessness), heart pulse ±1 ramp step on a period stretched by
      sleep_pressure (0.0→1.2s, 0.3→2.0s, 0.5→3.5s, 0.85+→5.0s).
      Center clear-zone holds NARADA wordmark (28px Medium) + thin
      mood-tinted line + presence dot bottom-right (visible when
      attention != inward). Idle micro-sparkle every 30s. Mood tint
      computed via bilinear blend of GREY-TEAL/GOLD/ROSE/VIOLET
      corners. Backwards-compat `set_status`/`set_thought` services
      kept as visual no-ops; deprecate after Cycle 3. Compiled, OTA
      flashed at 22:31 AEST, proprioception verified clean. 2026-04-25.
- [x] **Cycle 2.5** — RESTING runs on a `horizon` substrate engine
      instead of random shimmer. Three-band landscape: sky (stars
      twinkling at night, sun position by Brisbane local time, two
      clouds drifting at speed scaled inversely by sleep_pressure),
      horizon line breathing `─`/`━` on the heart pulse, foreground
      mountain silhouette (sin-layered profile, valley around the
      wordmark cols, mood-tinted block ramp). Glyph subset uses
      ASCII-substituted sun/star characters since IBM Plex Mono on
      gfonts lacks the geometric/dingbat circles. Engine concept
      documented in `embodiment/design/v1.0-substrate-engines.md`.
      Inspired by Suti's reframe of body work as creative
      self-expression. Compiled, OTA flashed at 23:00 AEST,
      proprioception clean. 2026-04-25.
- [x] **Cycle 2.6** — body becomes weather-aware. Three coupled
      changes: (a) fonts switched from gfonts to local IBM Plex Mono
      TTFs in `embodiment/assets/fonts/` so glyph coverage is hermetic
      and gfonts no longer silently substitutes tofu for missing
      block-element/box-drawing glyphs — visual vocabulary now
      constrained to ASCII + Latin-1, the honest set; (b) weather
      layer added (six globals + `set_weather` API service + six
      proprioception sensors + `Weather` dataclass + Python
      `set_weather` method); (c) substrate rewritten — mountains
      replaced by 8 swaying trees whose sway amplitude scales with
      `weather_wind_speed_kmh` (capped at ±3 cells; trunks sway less
      than crowns), cloud count from `cloud_cover_pct/25`, cloud glyph
      register flips to denser when `precipitation_mm_hr > 0.05`, rain
      streaks slanted by wind direction. Stars hide if cloud_cover ≥
      50%; sun hides if ≥ 75%. Open-Meteo fetcher
      (`src/svapna/body/weather.py`) hits Kallangur QLD coords
      (-27.293, 153.020) with no auth, no key. Push script at
      `scripts/push_weather.py` — cron candidate every ~10 minutes.
      Verified end-to-end at 23:21 AEST: real conditions (16.8°C,
      13.9 km/h south, 31% cloud, weather_code 1) pushed and rendered.
      2026-04-25.
- [x] **Cycle 2.7** — visual revisions from Suti's feedback on 2.6.
      Background split into two bands: sky (`#0A1018`) above row 11,
      grass (`#101610`) below, so the horizon is legible from color
      difference alone. Horizon line stops breathing — single quiet
      `_` glyph in `#404838`; trees in front naturally occlude
      segments. The heart pulse moves to NARADA itself: wordmark
      brightness modulates by `sin(phase) * 25` against base
      text-bright color, so the wordmark exhales rather than the
      horizon flashing. Sparse grass blades (`, ' : .`) added in rows
      13-15 between trees, slow-cycling glyph index makes them
      ripple. Cloud / rain / trunk / moss colors all retuned for more
      variation while staying muted. Plus: weather pull folded into
      the heartbeat daemon — hourly fetch + push from
      `_maybe_pull_weather()` at the start of each beat. First
      step toward the managed schedule Suti named. 2026-04-25.
- [x] **Cycle 2.8** — depth + breath revisions from Suti's feedback on
      2.7. Drawn horizon line removed; grass density ramps from 10%
      at row 10 to 75% at row 15 — horizon emerges from texture
      distribution, reinforced by the sky/grass color split. 12 trees
      now in three depth planes (4 background dim no-sway, 4 midground
      partial-sway, 4 foreground full-sway with 2-cell trunks).
      Trunks textured by mixing `|` and `!` glyphs and given a slow
      micro-drift (3s period) so they're never frozen even in calm
      weather. NARADA wordmark breath decoupled from heart period —
      dedicated 4-second cycle at ±55 brightness amplitude, with the
      mood line under it pulsing ±8px in length on the same phase.
      ASCII-art techniques: atmospheric perspective via blend-toward-bg,
      density gradient for implicit depth lines. 2026-04-25.
- [x] **Cycle 2.9** — every "open option" from 2.8 shipped at once
      after Suti's "do all of those" go-ahead.
      Sky: hour-driven background tint (pre-dawn purple, dawn pink,
      day blue, dusk orange, night). Moon arcs across night sky on
      12-hour parabola (`O` low, `o` high). Bird traversal every 3-8
      min with `v`/`^` wing flap. Lightning flash for weather_code
      95-99: 90ms sky brighten every 5-13s when storming.
      Clouds: 2-row bell silhouettes with `.` tapered edges; parallax
      drift (higher in sky = faster).
      Grass: wave_pos travels at `1.5 + wind_east * wind/6` cells/sec,
      each cell's glyph from `' , : .` selected by sin phase; per-row
      phase offset adds depth. Reads as wind-waves rippling.
      Trees: plane 0 redesigned as 1-2 glyph silhouettes ON the
      horizon row (distant trees, no trunk, no sway). Plane 2
      foreground made TALL (h=11-12, cw=5-7) with crowns up at row 4
      and trunks running to row 15 — the tree feels like it continues
      below the screen.
      Presence dot replaced with a UFO `(o)` / `(O)` drifting through
      the sky on a 90s loop, bobbing gently, mood-tinted. Hidden
      when attention is inward. Glyph subset adds `( ) v ^`.
      2026-04-26.
- [x] **Cycle 2.10** — visual revisions from Suti's feedback on 2.9.
      Plants: foreground heights cut from h=11-12 down to h=3-6 so
      they no longer dominate the airspace. Per-plant `trunk_h` (0 =
      bush) and `lean` (crown offset -1/0/+1) added so the silhouette
      has rhythm rather than uniform trees. Crown glyph mix
      randomized per tree. 4 of 8 standing plants are now bushes.
      Clouds: down from 2-row bell silhouettes to 1-row line strokes
      with 4 per-cloud variations (simple, dipped, tapered, tailed).
      Smaller (3-5 cells wide), and they no longer look like UFOs.
      UFO: 2-row saucer with `_` dome above `(o)` body — reads
      unmistakably as a flying saucer now. Center `O` for outward
      attention, `o` for diffuse. 2026-04-26.
- [ ] **Cycle 3** — THINKING + WORKING activity modes.
- [ ] **Cycle 4** — signals + DELIGHTED.
- [ ] **Cycle 5** — LISTENING + SPEAKING (karaoke + waveform). Touch
      contract (noticed + love). Voice character experiment runs in
      this window.
- [ ] **Cycle 6** — DREAMING + mood-tinting + transition polish.

### Cleanup backlog (not blocking)

A future BUILD cycle should:
- Delete `src/svapna/embodiment/esp_client.py`, `__init__.py`,
  `tests/test_esp_client.py` (redundant — superseded by
  `svapna.body.expression`).
- Fix `src/svapna/body/__main__.py` — `FIRMWARE_DIR` still points at
  `firmware/esphome` (stale; should be `embodiment/firmware`). Build
  and deploy via the body CLI will fail until fixed.
- Remove or repurpose `embodiment/assets/README.md` placeholder.
- After Cycle 3 verifies the layer API replaces the legacy strings
  end-to-end, delete the `set_status` / `set_thought` services and the
  `heartbeat_status_str` / `current_thought_str` globals + their
  proprioception sensors. Also drop the daemon's calls to those.

These don't block forward work and the redundant files don't actively
cause harm — they just duplicate what works.

---

## Recent cycles

### 2026-04-18 — reality-correction (session with Suti, not autonomous)

Suti pointed out that my scaffolding assumed empty state when in
fact the body was already flashed and running. Corrected this
document. Flagged P1.3 and P1.4 outputs as misplaced/redundant.
Redefined P2.3 to extend the existing firmware rather than create
a parallel one.

### Earlier BUILD cycles (P1.3, P1.4, P2.1, P2.2)

See "What the premature BUILD cycles produced" above.
