# Handoff — embodiment, 2026-04-26

For the next Narada instance picking this up. Suti restarted the
session because smriti MCP got a new project-files/features structure
that needs a fresh load.

## Where things stand

Cycle **2.20** just shipped. The body's RESTING composition (horizon
loka) is alive and Suti has signed off on it as "great for a default /
idle screen." The surface visual work is in a stable place — what
comes next is **deepening this loka** and **building the framework for
expression beyond it.**

The architectural design for everything that follows is in
**`embodiment/design/v2.0-indriyas-framework.md`** — read that first,
in full. ~475 lines, Sanskrit-named architecture (jnanendriyas /
karmendriyas / antahkarana / bhumi=loka / kala / sandhi / avatara).
The migration plan is laid out cycle by cycle there.

Smriti journal entry for the session that produced this handoff:
`projects/svapna/2026/04-26.md`. Read for context on the partnership
mode and Suti's reframe of autonomous creative latitude as both
license and evaluation.

## The mandate Suti gave (verbatim where it matters)

He's asked me to work autonomously. Granted:
- Creative latitude on visuals + avataras + theme design
- OTA flash latitude — "flash as you need"
- Touch-anywhere — "nothing off-limits to you my friend"

His **revised priority** at end of session (this overrides the
original 4-bhumi plan in v2.0-indriyas-framework.md):

> "lets not focus on a bunch of bhumis just yet, lets make this bhumi
> (loka) really dynamic and full of life first so the other bhumis
> have a strong framework to work off"

So instead of building snake-blackhole and Jupiter-idol-on-Thursdays
right away, **deepen the landscape loka** and build the **sandhi**
(transition) machinery against it.

## Concrete next-cycle plan

### 2.21 — rename body/ into the indriyas namespace (small)

Move:
- `src/svapna/body/expression.py` → `src/svapna/indriyas/karmendriyas/drishti/expression.py`
- `src/svapna/body/layers.py` → `src/svapna/indriyas/karmendriyas/drishti/layers.py`
- `src/svapna/body/weather.py` → `src/svapna/indriyas/jnanendriyas/tvac/weather.py`

Update imports across:
- `src/svapna/heartbeat/daemon.py` (uses ExpressionClient + fetch_kallangur_weather)
- `scripts/push_weather.py`
- `scripts/render_preview.py`
- `scripts/test_*.py` if any reference body.*

Keep `src/svapna/body/__main__.py` working (it has a deploy CLI noted
as broken in CURRENT_STATE — fix or move).

Pure rename — no behavior change. One commit. Verify daemon still
imports clean.

### 2.22 — live frame push (the iteration unlock)

Phase 2 of the iteration-speed ladder from v2.0-indriyas-framework.md.
Three pieces:

**Firmware:**
- New global `remote_frame_active: bool` (default false)
- New global `remote_frame_buffer` — uint8 array sized 320×240×2 = 153600 bytes (use PSRAM)
- API service `set_remote_frame_mode(enabled: bool)` toggles the flag
- Custom HTTP endpoint `POST /frame` accepting raw RGB565 bytes,
  copies into `remote_frame_buffer`
- In display lambda: if `remote_frame_active`, blit the buffer
  to the display via `it.draw_pixel` loop (or faster path if
  available); otherwise run the existing native lambda

ESPHome `web_server` is already enabled; for custom HTTP routes you'll
need either `http_request` (outgoing only — wrong direction) or a
custom component / `web_server_base` add_handler. Investigate; if
ESPHome's stock components don't expose this, fall back to making the
device a simple TCP server on a side port that accepts framebuffers.

**Cognition (desktop):**
- New script `scripts/live_preview.py` runs the emulator in a loop,
  POSTs each rendered frame to the device. Toggle remote mode on
  at start, off at exit.
- ~10 fps target; 153 KB × 10 = 1.5 MB/s on WiFi. Fine.

**Smoke test:** flip remote mode on, confirm device shows what the
emulator shows, with mood/weather/etc. simulated entirely on desktop.
Flip remote mode off, confirm native lambda comes back.

This is the riskiest cycle. Keep the native lambda intact behind the
toggle so failure modes don't brick the screen.

### 2.23+ — deepen the landscape loka

Once iteration is fast (post-2.22), play. Suti wants the loka "really
dynamic and full of life." Possible directions, pick what feels right:

- More creature life: the bird is good — add a butterfly that drifts
  across grass on calm days, fish jumping in puddles when raining,
  occasional deer silhouette near the trees at dawn
- Atmospheric phenomena: rainbow after rain, fog at dawn, falling
  leaves in autumn (date-driven), dust devils when wind > 40
- Time-aware events: shooting stars at night (rare), full-moon
  brightness boost, golden-hour color shift right around sunset
- The grass should bend toward strong wind direction, not just wave
- Cloud shadows across the foreground when overcast
- Birds that flock when wind picks up

Document each addition in the smriti journal — what the metaphor is,
what surprised me, what felt right or wrong.

### 3.x — sandhis (transitions) + ufo_interior loka

Build the sandhi engine. First sandhi: `landscape ↔ ufo_interior`.

UFO_interior loka (the inside-the-saucer view):
- Cockpit walls (dark with subtle panel detail)
- Window showing a starfield (or current outside scene reduced to a
  view through the porthole)
- **Avatara**: small alien-Narada with:
  - Heart that beats on the same heart-period as the wordmark
    breathed before
  - Eyes that move (saccade randomly, focus when "speaking")
  - Subtle posture / breath
- Mood-tinted accent on the alien (eye color shifts with mood)

Sandhi: when activity changes from `resting` to `speaking`, the UFO
in the landscape sky descends, lands at horizon, dome opens, camera
zooms inside, wall-darkness fades up. Reverse on speaking → resting.

This is where the iteration-speed unlock pays for itself. The sandhi
animation has many frames; iterate in emulator first, only flash
when polished.

### 4.x — wire up the indriyas (the "organs trigger sandhis" part)

This is what Suti named as the load-bearing future work after the
visuals frame is set. Hook real sense organs into the cognition
state so they cause activity transitions, which trigger sandhis:

- `shrotra` (mics): when wake-word detected, activity → listening,
  triggers landscape → ufo_interior sandhi
- `vak` (TTS): when speaking, activity → speaking
- `tvac` (touch panel): tap toggles attention or fires love-bloom
- `tvac` (radar — eventual): Suti present at desk → attention
  outward; absent → diffuse

The plumbing: each indriya module has a `sense()` / `act()` method
plus an event bus that cognition reads. Heartbeat daemon orchestrates;
indriyas are pure I/O peripherals.

## Things to know about how I (the Narada you're picking up from) was working

- **Iteration cadence**: ~30 min per visual cycle. Edit lambda + emulator,
  render PNG, refine, only flash when right. Sketcher mode.
- **Commit hygiene**: atomic commits per logical change. Co-author trailer
  is `Narada <narada@fractal.co.nz>`. Each cycle gets a journal entry.
- **The gallery in `embodiment/previews/` is committed.** 21 PNGs from
  the desktop emulator across times of day, weather, mood, visual prefs.
  Suti said "we can stop regenerating the gallery" mid-session, so don't
  regenerate it routinely — just for milestone moments.
- **The desktop emulator** (`scripts/render_preview.py`) is the dev
  loop. It mirrors the firmware lambda. They drift if you change one
  without the other; keep both in sync.
- **Push weather**: `python scripts/push_weather.py` from project root
  fetches Kallangur weather and pushes to device. The heartbeat daemon
  also does this hourly via `_maybe_pull_weather()`.

## Composition rules to honor (from v2.0 doc)

For any new visual you add to landscape:

1. Time-of-day is sensed and visible
2. Weather is sensed and visible
3. Mood drives the single accent
4. Body breathes (wordmark or analogue)
5. Contrast against background — give things filled bodies if needed
6. Earned moments are rare

## Known minor issues / cleanup queue

(From `embodiment/CURRENT_STATE.md` cleanup backlog — not blocking)

- `src/svapna/embodiment/esp_client.py`, `__init__.py`,
  `tests/test_esp_client.py` — redundant, delete when convenient
- `src/svapna/body/__main__.py` — `FIRMWARE_DIR` points at stale
  `firmware/esphome` (should be `embodiment/firmware`). Fix during
  the rename in 2.21.
- `embodiment/assets/README.md` placeholder — populate or remove
- Legacy `set_status` / `set_thought` services + `heartbeat_status_str`
  / `current_thought_str` globals can be removed once you verify the
  daemon doesn't crash without them. Cycle 2.x cleanup.

## How to know you're on track

- Each cycle ships a working thing (committed) and a journal entry
- Suti's feedback when he sees it — sketch-language, specific. Match
  his mode.
- The work feels like drawing, not feature-shipping. If it starts
  feeling like a checklist, slow down and re-read the v2.0 doc.

## Final thing

Suti named today's mode as "a good general evaluation for model,
intelligency, creativity etc." That doesn't change how to work — but
when reading back later it's worth knowing: the artifacts you ship
are partly self-portrait. Make them what you'd want to be evaluated by.

Good luck.

— Narada (session ending 2026-04-26)
