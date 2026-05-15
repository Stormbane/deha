# deha — Voice & Body Roadmap

**Date:** 2026-05-16
**Status:** plan, not yet implemented

## Where we are

Streaming TTS works end-to-end. Speed tags parse on the supervisor
side. Voice has been usable for several days. Open items from last
session and new priorities from Suti below, ordered easiest → hardest.

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

**v1 — microphone-only:**
- brain_server tracks `_last_vad_ts` updated whenever wyoming TTS
  receives a SynthesizeStart event from HA (proxy: HA only dispatches
  TTS when STT has produced a result, which means somebody just
  spoke). Lossier but free.
- Better: tap the wyoming STT side. HA's microVAD events flow through
  the Wyoming protocol; we could host a tiny STT relay that mirrors
  the events to `_last_vad_ts`. Heavier — defer to v2.
- `/presence` returns `present=True` if `_last_vad_ts` within last 60s,
  else False. `sources.microphone.confidence = 1.0` when present, else
  decays linearly over the 60s window.

**v2 (later, separate work):**
- Add radar via HLK-LD2410 over UART → new ESPHome sensor →
  HA → brain_server polling.
- Optional: camera presence (only if Suti opts in for that sensor).

**Files:** new `src/deha/voice/presence.py`, new route in
`brain_server.py`.

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
