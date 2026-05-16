# deha — Voice & Body Roadmap

**Date:** 2026-05-16
**Status:** Tier 1 done; Tier 2 next.

## Status as of 2026-05-16

**Tier 1 — DONE.** Commits `22bee72` + `729ce9d`:
- Closing-tag bug fixed (speed tags incl. `</fast>` form get stripped).
- Realistic Kokoro prewarm + new `_prewarm_claude` probe at startup
  so first user turn doesn't pay the prompt-cache cold-miss.
- Haiku default model in supervisor; keepalive removed.
- Tag-strip pushed into `synth_chunk` so `/utter` and MCP callers
  inherit consistent behavior.

**Tier 2 — NEXT.** Suggested entry: 2.2 Presence v1 (no decision
needed; the hardware turns out to be richer than first assumed —
see correction below).

**Tier 3** is the big design piece (voice ↔ Signal parity in prana);
worth its own plan doc when Tier 2 is in flight.

## Important correction (2026-05-16 close)

I had been assuming the BOX-3 was sensor-light. Wrong — it's the
**ESP32-S3-BOX-3-Sensor** variant, which has:

- **mmWave radar** (proper human-in-room detection, even still)
- **Camera**
- **IR emitter/receiver**
- **Temperature + humidity sensor**

This changes Tier 2.2 (presence v1) from "microphone-only fallback"
to "proper multi-sensor fusion as the `/presence` contract intended."
See Tier 2.2 below for revised plan.

---

## Tier 1 — Small wedges (an afternoon each)

### 1.1 Closing-tag bug fix

Narada is emitting `<fast>...</fast>` and `<slow>...</slow>` (SSML-style
closing form). The current `_SPEED_TAG_RE` only matches opening tags;
closing tags pass through to Kokoro and get spoken literally.

**Fix:** extend regex to match `</slow|normal|fast|speed>`. Closing tag
returns sticky speed to `<normal>` (1.0). Test with one turn.

**Files:** `src/deha/voice/kokoro_tts.py` only.

### 1.2 First-turn warmup mitigation

Two factors stack: claude-cli prompt-cache miss on first message (~5-7
s) and Kokoro JIT on first real text. Current `_prewarm_kokoro` synths
the literal string `"ready"` which doesn't cover the input shapes a
real reply uses.

**Fix:**
- Replace `_prewarm_kokoro` synth string with a longer realistic
  sentence (~30 chars covering punctuation and varied phoneme shapes).
- Add `_prewarm_claude` that fires one tiny no-op turn against the
  pool at startup. Pays the cache miss before the user does.

**Files:** `src/deha/voice/brain_server.py`.

### 1.3 Commit + push pending changes

Three uncommitted files from the prior session: haiku default, removed
keepalive, speed-tag parsing. Squash with the 1.1 fix into one
`voice: prosody tags + haiku default + cleanups` commit.

---

## Tier 2 — Body-side additions (1-2 days each)

### 2.1 Visuals — "more Narada"

Today: default ESPHome BOX-3 face. Want: something that reads as
Narada specifically.

**Easiest path — single static idle image:**
- Generate a 320x240 PNG: stylized symbol (ॐ / mandala / sage
  silhouette with vina) + the name "Narada" in a chosen typeface.
- Drop into `firmware/sprites/`, reference from `firmware/box3.yaml`
  via the existing `image:` + `display:` blocks.
- Replaces the idle state only — listening / speaking states stay
  default for v1.

**Tier-2 followup once we like the idle:**
- Add three state variants (idle / listening / speaking).
- Animate via frame sequence on the existing display ticker.

**Design call needed:** what's Narada's symbol? Pick once, commit.
`sprites/test_atlas/` already exists from earlier sprite work — start
there.

**Files:** `firmware/*.yaml`, new assets under `firmware/sprites/`.

### 2.2 Presence detection — `/presence` v1

`docs/contracts/presence.md` defines the endpoint. prana already polls
it with graceful fallback. Just needs implementation.

The BOX-3-Sensor variant ships with mmWave radar, camera, IR
emitter/receiver, and temp/humidity. Hardware is present; the question
is what's currently enabled in the ESPHome firmware vs. what we need
to add.

**Step 0 — audit firmware.**

Before any deha-side code, open `firmware/*.yaml` and inventory:
- Which radar component? (likely LD2410 — ESPHome has stock support.)
- Is the camera block enabled? (`esp32_camera:` — disabled by default
  in many BOX-3-Sensor builds.)
- Are IR / temp / humidity exposed as sensors?
- What HA entities does the firmware currently publish?

If sensors aren't published to HA yet → first piece of work is the
firmware YAML + reflash + verify entities appear in HA.

**Step 1 — implement `/presence` reading the published sensors.**

Priority order for fusion (best signal first):
1. **mmWave radar** — best for "human in room, even still." Reports
   `detected: bool` directly. Confidence is binary (present / not).
2. **Microphone VAD** — almost free; tap the wyoming STT events as
   they pass through brain_server. `last_voice_ts` decays over 60s.
3. **Camera** — heaviest. Privacy call needed before exposing raw
   frames; binary "person detected" via on-device inference is the
   right interface. Defer the implementation detail to v1.5 if it
   delays the rest.
4. **Temp/humidity** — not presence per se, but expose alongside as
   environmental context (useful for other Narada decisions).

Fusion rule for `present` field: True if radar says yes OR (camera
says yes) OR (mic vad within last N seconds). Per-sensor confidences
surfaced individually in `sources.*` for prana to do its own logic
later.

**Step 2 — verify prana sees it.**

prana already polls with graceful fallback (per the `/presence`
commit message). When deha starts returning real data, prana's
router behavior should change automatically — no prana-side code
change needed. Confirm via prana's logs.

**Files:** `firmware/*.yaml` (sensor enablement), new
`src/deha/voice/presence.py`, new route in `brain_server.py`,
likely also a small HA-state reader (or use existing `deha_client`
patterns to read entity state).

**Out of scope for v1:** camera-based gaze detection, fine-grained
zones, multi-person counts. Just binary "human present in the room."

### 2.3 Wake word — "Narada"

Today: HA microWakeWord with a default model on the BOX-3. Want:
trained on "Narada".

**Path:**
1. **Collect samples.** ~150 positive samples of Suti saying "Narada"
   in varied conditions (close/far, quiet/noisy, fast/slow). HA has a
   built-in collection workflow via Assist devices, or use the
   `microwakeword` repo's `data_collection.py`.
2. **Train.** Use the `microwakeword` training pipeline (TensorFlow
   Lite, runs on CPU in ~30min, GPU in ~5min). Produces a
   `.tflite` model.
3. **Deploy.** Drop the model into `firmware/wake_words/`, reference
   from `firmware/box3.yaml` `micro_wake_word:` block. Replaces the
   default "okay nabu" with "narada".

**Risk:** model quality. First-pass models often have high false-
positive or false-negative rates. Plan for one iteration cycle to
tune the threshold and possibly collect more samples.

**Effort:** ~2h collection + ~1h training + ~1h deployment + 1
iteration. So call it a day's work spread across the wait cycles.

**Files:** new `firmware/wake_words/narada.tflite`, edits to
`firmware/box3.yaml`. New script `scripts/wake_word/collect_samples.py`
to make collection ergonomic.

---

## Tier 3 — Voice ↔ Signal parity (the meaty one)

### 3.1 The capability gap

Today voice talks to a stripped-down Narada. `brain_server.py`'s
`ClaudeStreamSession` runs `claude -p` with `--disallowedTools
"Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"`.
That's everything — no tools, no skills, no MCP, no file access. Just
chat-with-system-prompt.

Signal talks to full Narada. `narada_chat_bridge.py` spawns `claude
-p --continue` per message with all defaults — meaning every skill,
every MCP server, every tool the global config allows. Different
Narada in two channels.

**Goal:** unify. Same Narada whether Suti speaks or types. Including
the ability to act — read project files, run a build, write a note —
when the request calls for it.

### 3.2 Shared persistent claude session

What "persistent claude code session on this computer" most plausibly
means: a single long-lived Narada session that both voice and chat
push messages into and read responses from. State (skill memory,
recent file context, partial work) carries across channels.

**Architecture sketch:**

```
                  ┌──────────────────────┐
   voice  ───────►│                      │
                  │  narada-session      │ ◄─── one ClaudeStreamSession,
   signal ───────►│  service (prana)     │      full tools, --continue
                  │                      │
   chat MCPs ────►│                      │
                  └──────────────────────┘
                           ▲
                           │ HTTP POST /turn { text, channel }
                           │ returns NDJSON stream of deltas
```

- Long-lived `claude -p --continue` subprocess in a single workdir
  (the "narada" project workdir). Full tool surface enabled.
- Single session lock — turns serialize. If voice and signal both
  arrive concurrently, second waits. Voice gets a "one moment,
  finishing something" filler if wait > 2s.
- Channel hint passed in user message metadata so Narada can adapt
  tone (voice = short / spoken cadence; signal = longer / written).
- TTS path consumes only the streamed text deltas from voice turns;
  tool-call events are skipped over (TTS doesn't speak tool calls).
- The session service lives in **prana** (next to chat-bridge),
  exposes HTTP on a fixed port, both `deha/brain_server` and
  `narada_chat_bridge.py` become clients.

**Trade-offs:**
- Each turn pays the claude-cli per-call cost (already the case for
  signal; voice was on a faster persistent stream-json without tools).
  Per-call cost is ~3-4s startup; streaming TTS hides most of it.
- Full tool calls take time (file reads = 100ms, builds = minutes).
  Voice latency on tool-heavy turns is genuinely high. Mitigation:
  voice replies short-circuit obvious chat turns ("how are you")
  without invoking tools.
- Concurrency: only one turn at a time. If Suti is mid-conversation
  on signal and walks to the box, voice waits. Acceptable.

### 3.3 Implementation phases

**Phase A — session service in prana (scaffolding).**
- New `prana.session.narada_session` module wrapping
  `claude -p --continue` with full tools enabled.
- HTTP endpoint `POST /turn` returning NDJSON deltas.
- Single asyncio lock around the subprocess for turn serialization.
- Workdir: `~/.narada/sessions/main/`.

**Phase B — chat-bridge migration.**
- Update `narada_chat_bridge.py` to POST to the session service
  instead of spawning its own `claude -p`.
- Per-chat session continuity preserved by passing
  `channel=signal:<chat_id>` so the service can manage per-channel
  context if needed (open question — see 3.4).

**Phase C — voice migration.**
- Replace `brain_server.ClaudeStreamSession` with an HTTP client
  hitting the session service.
- Stream NDJSON deltas through to the existing TTS pipeline
  unchanged.
- Drop the disallowedTools restriction; tool calls happen, voice
  just doesn't speak them.

**Phase D — voice-aware filler / latency cover.**
- When a turn waits > 2s for the session lock or a slow tool call,
  emit a synthesized filler via `/utter` ("one sec" or similar).
- Optional. Maybe.

### 3.4 Open design questions for Tier 3

- **One session or one-per-channel?** Single session = state shared,
  but signal and voice contexts blur. One-per-channel = clean
  contexts, but loses the "switch channels, same conversation"
  property Suti described. Lean toward **single session** with
  channel hints in messages.
- **Should signal MCPs see voice deltas?** Probably not. Skill output
  goes to the channel that asked.
- **Permission scope for tools.** Voice asks Narada to "open the file
  I was editing" — that's full FS access. Today claude-cli has a
  permission model. Need to decide: prompt-mode for novel paths or
  blanket-allow because Suti owns the machine.

### 3.5 What this unblocks

- "Hey Narada, what's the status of the deha repo?" → ls / git
  status → spoken summary.
- "Open a claude code session on the host" → narada-session is
  already that; voice and signal both ride it.
- Cross-channel continuity ("I was just telling you on Signal about
  X — pick that up out loud").
- Long-form work via voice ("draft the commit message for the speed
  tag stuff" — tool calls happen silently, only the final spoken
  draft comes through TTS).

---

## Recommended sequencing

1. **Tier 1 in one sitting** — closing-tag fix, warmup mitigation,
   commit. Few hours total.
2. **Tier 2.1 (visuals)** — needs the design call (pick the symbol);
   once picked, an afternoon.
3. **Tier 2.2 (presence v1)** — half day. Unblocks heartbeat router
   logic in prana.
4. **Tier 2.3 (wake word)** — biggest of the Tier 2 group, ~a day
   spread across iteration cycles.
5. **Tier 3 (parity)** — multi-session effort. Worth scoping its own
   plan doc once Tier 2 lands.

Voice blending (deferred from prior plan) stays deferred; not on
this roadmap.

---

## Cross-project dependencies

- Tier 3 changes prana (new session service, chat-bridge migration).
- Tier 2.2 only needs prana to keep polling; no prana code change.
- The earlier `host-orchestrator-2026-05-11.md` plan in prana stays
  independent — it's about *running* these services, not building
  them. Can land in parallel.
