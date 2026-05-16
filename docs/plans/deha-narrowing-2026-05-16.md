# deha narrowing — move cognition out, keep the body

**Date:** 2026-05-16
**Status:** design (proposed)
**Companion docs:**
- `docs/contracts/presence.md` — `/presence` HTTP contract (already specced)
- `docs/contracts/mcp-server.md` — body MCP server contract (already specced)
- `docs/plans/voice-roadmap-2026-05-16.md` — voice/body roadmap (Tier 3 builds
  the receiving end of the escalation contract specced here)
- `~/.narada/projects/prana/2026/05-16.md` — Stage 5 host migration (sets up
  the supervised process tree this design slots into)

## The shift

The architecture clarified on 2026-05-11+ says: **deha is the body, prana is
cognition, and they are different layers.** Today deha violates that — `deha`'s
brain_server runs the voice-conversation `claude -p` itself, with tools
disallowed. That made sense when deha was the only thing running on the box;
it stops making sense once prana hosts a singular cognition pipeline that
every channel (Telegram, voice, wake-word, presence-edge) funnels into.

This doc specs the narrowing: what comes out of deha-brain, what stays, and
how the handoff to prana works.

### Where the cognition lives today (concrete pointers)

- `deha/src/deha/voice/brain_server.py:84` — `StreamPool` holds one ever-living
  `ClaudeStreamSession` across all HA conversations.
- `deha/src/deha/voice/claude_stream.py:58` — spawns `claude -p
  --input-format stream-json` with `--disallowedTools "Bash,Edit,Write,Read,
  Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"`. This subprocess **is** the
  voice cognition.
- `brain_server.py:143` — `handle_converse` pumps HA transcripts into the
  StreamPool and streams responses back through Wyoming TTS.

Everything below moves this `claude -p` subprocess (and the tool restriction)
out of deha entirely.

## What deha keeps (its scope after narrowing)

### 1. Sense / reflex layer — "lizard brain"
- Wake-word detection (microWakeWord on the BOX-3, model trained on "Narada"
  per voice-roadmap Tier 2.3).
- Presence fusion (mmWave radar + camera + mic VAD + IR + temp/humidity).
  Specced in `docs/contracts/presence.md`. Single fused boolean + per-sensor
  breakdown, hysteresis applied locally.
- Voice activity detection — Wyoming STT events; produces transcripts but
  does NOT decide what to say back.
- Sensor noise suppression — debounce, smoothing, false-positive filtering on
  every source before it leaves the body.

### 2. Body API — the instrument surface
- HTTP endpoints (today): `/utter`, `/converse`, `/set_face`, `/set_status`,
  `/set_weather`, `/presence` (new).
- MCP server (next): `body.speak`, `body.set_face`, `body.set_status`,
  `body.set_weather`, `body.get_presence`. Future: `body.look`, `body.listen`,
  `body.set_light`. Same handlers as HTTP, different protocol.
- Both protocols are first-class. HTTP for same-host Python callers (lowest
  latency); MCP for LLM-driven cognition (heartbeat's `claude -p`, the
  prana session service, Claude Code on the PC).
- Embodiment-specific extras live next to the common set (e.g. Unitree:
  `body.walk`, `body.grasp`). Different bodies = different deha
  implementations, same core contract.

### 3. Hardware orchestration
- HA container lifecycle (when applicable).
- brain_server / expression_server process supervision (the existing
  `deha.voice.supervisor`).
- TTS daemon (Kokoro), Wyoming TTS bridge to HA, ESPHome native-API link.

### 4. Reflexes — bounded latency responses
Things that can't wait for cognition latency:
- Acknowledgment cues ("I heard you" tone, visual indicator on the face when
  wake-word fires) — sub-100ms feedback that signals "your voice was received,
  Narada is now thinking."
- Safety stops (Unitree-style embodiments: hard-stop motors on collision).
  Not applicable to BOX-3 today; reserved.

The escalation packet to prana includes the reflex already taken, so cognition
knows what the body has already done.

### 5. Escalation contract — see §"Escalation" below

## What deha loses

- **`StreamPool`** — delete. No more long-lived `claude -p` inside
  brain_server.
- **`ClaudeStreamSession`** — delete (`claude_stream.py` goes away).
- **`--disallowedTools` restriction** — moot. Cognition is no longer inside
  deha, so there's nothing to restrict.
- **`/converse` as a cognition entry** — repurposed. It becomes a thin
  HTTP shim that forwards transcripts to prana's session service and pipes
  back the deltas (so HA's existing assist pipeline keeps working without
  HA-side changes during the migration).
- **System-prompt assembly for voice** — moves to prana. deha doesn't
  decide who Narada is anymore; prana's session service does, using the
  same wake-context.md / SOUL.md that signal already uses. **One Narada,
  one voice, two channels.**

## Escalation — how the body hands off to cognition

### Design choice: no local escalation model (v1)

Per the 2026-05-16 design call: every voice transcript escalates to prana.
No on-device gating model deciding "is this worth waking the big cognition."
Rationale: keeps deha simple, keeps latency bounded by one fewer hop,
defers a real design call until we have actual data on what should NOT
escalate. The slot exists for a future Gemma/Qwen-small gate but is
not implemented in v1.

### The escalation HTTP shape

deha POSTs to prana's session service (built in voice-roadmap Tier 3, see
`docs/plans/voice-roadmap-2026-05-16.md` §3):

```
POST http://127.0.0.1:8771/turn
Content-Type: application/json

{
  "text": "what's the weather looking like",
  "channel": "voice:box3-livingroom",
  "body_id": "box3-livingroom",
  "trace_id": "vox-2026-05-16T10:24:31.812-7f3a",
  "context": {
    "presence": { "present": true, "idle_seconds": 0.4 },
    "wake_source": "microwakeword",
    "reflex_taken": "ack_tone_played"
  }
}
```

Response: NDJSON stream of cognition deltas (same shape as `claude -p
--output-format stream-json`). deha pipes the `text` deltas straight into
the existing Kokoro TTS path; tool-call events are ignored (TTS doesn't
speak tool calls).

Port `8771` is a placeholder — pinned by prana's session service when
Tier 3 lands. See voice-roadmap §3.2 architecture sketch.

### Fields

| Field | Meaning |
|---|---|
| `text` | The transcript that triggered cognition (post-VAD, post-STT). |
| `channel` | Where cognition's output should be routed. Today `voice:<body_id>`. |
| `body_id` | Which body produced this. Lets cognition pick the right `body.*` MCP target. |
| `trace_id` | For cross-cognition / cross-log correlation. Matches the reserved field in prana's bus events. |
| `context.presence` | Snapshot of `/presence` at trigger time. Cheap; saves cognition one round-trip. |
| `context.wake_source` | `microwakeword`, `vad_after_silence`, `manual_button`, etc. |
| `context.reflex_taken` | What deha already did so cognition doesn't duplicate it. |

### When deha escalates

Triggers for an escalation POST:
- Wake-word fires AND following utterance segment ends (VAD-silence
  threshold). Today this is HA's assist pipeline; in v1 of the narrowing
  we keep that flow but redirect its output target.
- Manual button on the body (future).
- Camera-detected gesture (future, reserved).

Triggers that do NOT escalate:
- Background mic activity below wake threshold.
- Presence change without speech (publishes to bus instead — Phase 1B
  of unified-mind).

## What changes in brain_server.py

Concretely, the migration steps inside `brain_server.py`:

1. **Keep `/converse` route shape.** HA-side wiring stays identical.
2. **Replace handler body.** Instead of pulling a StreamPool session and
   pumping deltas, the new handler:
   - Reads request → builds escalation payload (see above).
   - POSTs to prana session service.
   - Streams the NDJSON response back to the caller (HA) AND tees text
     deltas into the existing Kokoro TTS path.
3. **Delete `StreamPool` and `_prewarm_claude`.** No longer needed.
4. **Keep `_prewarm_kokoro`** — TTS still lives here.
5. **Delete `claude_stream.py`** entirely.
6. **`/utter` keeps working unchanged.** It's a queue-write path for
   heartbeat-initiated speech; it never went through claude in deha.
   When heartbeat moves to body MCP (see §"prana-side wiring" below),
   `/utter` becomes the HTTP equivalent that `body.speak` MCP routes to.

## Failure modes

- **prana session service unreachable.** deha plays a short fallback line
  via Kokoro ("one moment, I'm having trouble reaching cognition") and
  surfaces a body-visible status on the face. Logs the failure with the
  trace_id so prana can correlate when it comes back.
- **prana session service slow (>2s before first delta).** deha emits a
  filler tone or short Kokoro utterance covering the latency, then plays
  the real reply when deltas arrive. Optional, per voice-roadmap §3.3
  Phase D.
- **Wake-word false-positive followed by silence.** deha never escalates
  (no transcript to send). Visual ack times out and clears.
- **STT returns empty / unintelligible.** deha plays a short "didn't
  catch that" cue locally — does NOT escalate empty transcripts to
  prana. This is one of the few reflexes that lives in the body.

## Phasing — what lands in what order

Each step is shippable and reversible. Cross-repo so listed by repo:

| Step | Repo | What | Blocks |
|---|---|---|---|
| 1 | deha | Implement `/presence` v1 per `docs/contracts/presence.md`. mmWave radar + camera + mic VAD fusion. | nothing |
| 2 | deha | Bootstrap body MCP server per `docs/contracts/mcp-server.md`. Tools wrap existing HTTP handlers. | nothing |
| 3 | prana | Wire body MCP into heartbeat + chat-bridge `claude -p` invocations. See §"prana-side wiring" below. | (2) |
| 4 | prana | Build session service per voice-roadmap §3 Phase A. `POST /turn` returning NDJSON deltas, single asyncio lock. | nothing |
| 5 | prana | Migrate chat-bridge to call session service (voice-roadmap §3 Phase B). | (4) |
| 6 | deha | Switch `/converse` to forward to prana session service. Delete `StreamPool` + `claude_stream.py`. | (4) |
| 7 | deha | Land wake-word "Narada" model per voice-roadmap Tier 2.3. | (6) ideally — full narrowing in place when the new wake-word starts firing real escalations |

Steps 1, 2, 4 are independent and can run in parallel across sessions.
Step 3 unblocks heartbeat from speaking through the body via MCP (instead
of the direct HTTP `route_utterance` call that the bus action wraps today).
Steps 5 and 6 are the narrowing proper — after these land, voice and
chat are *one Narada*.

## prana-side wiring (folded in from the mcp-server.md gap)

`docs/contracts/mcp-server.md` covers how Claude Code consumes the body via
`~/.claude.json`. It does NOT cover how prana's heartbeat and chat-bridge
mount the body MCP server. Filling that gap:

### How heartbeat consumes body MCP

The heartbeat fires via `python -m prana.heartbeat --once`. Inside, the
EXECUTE / SPEAK stages invoke `claude -p` for tool-using work. Today those
invocations get smriti MCP via the global Claude Code config. The body MCP
is added the same way — via `--mcp-config` pointed at a prana-managed JSON:

```
~/.narada/heartbeat/mcp-config.json
{
  "mcpServers": {
    "smriti": {
      "command": "python",
      "args": ["-m", "smriti.mcp_server"],
      "timeout": 30
    },
    "body": {
      "command": "python",
      "args": ["-m", "deha.mcp.server"],
      "timeout": 30
    }
  }
}
```

Heartbeat passes `--mcp-config ~/.narada/heartbeat/mcp-config.json` to its
`claude -p` calls. Heartbeat code path that today calls `invoke_speak()`
(the bus action wrapper around `route_utterance`) keeps working unchanged —
it's the in-process HTTP path. The MCP path is for when the heartbeat
LLM *chooses* to speak as part of its reasoning, not for the daemon's
own SPEAK stage.

### How chat-bridge consumes body MCP

Same shape. `narada_chat_bridge.py`'s per-message `claude -p` spawn already
inherits MCP from the global Claude Code config. The simplest path:
add a `body` entry to `~/.claude.json`'s `mcpServers` and chat-bridge picks
it up for free. If we want chat-bridge's MCP set to differ from Claude
Code's (e.g. exclude smriti, include body), point chat-bridge at its own
`--mcp-config` file instead.

Once Tier 3's session service lands (step 4 in the phasing table),
chat-bridge no longer spawns its own `claude -p` — it POSTs to the session
service, and the session service owns the MCP config. Body MCP moves with
it.

### Coexistence of MCP and direct HTTP

After body MCP lands, prana has two ways to reach the body:
- **Direct HTTP** (`prana.bus.actions.invoke_speak` → `route_utterance` →
  `POST /utter`). Fastest path; no subprocess. Used by the heartbeat
  daemon's own SPEAK stage and by anything in prana that wants to make
  the body speak from straight Python.
- **Body MCP** (LLM picks `body.speak` from its tool palette). Slower
  (subprocess + framing) but enables cognition to *decide* to speak as
  part of reasoning. Used by every `claude -p`-driven path: heartbeat's
  EXECUTE/SPEAK turns, chat-bridge's per-message claude, future session
  service's persistent claude.

Both paths hit the same handler. No duplication of business logic — that's
the whole point of the MCP/HTTP coexistence specced in `mcp-server.md`.

## Open questions

1. **System-prompt assembly for voice in the session service.** wake-context.md
   today is built for chat. Voice cadence is different (shorter, spoken).
   Does the session service compose a voice variant when `channel` starts
   with `voice:`, or does Narada adapt based on the channel hint in the
   user message? Lean toward: same system prompt, channel hint in the
   user message, Narada handles cadence.
2. **HA's assist UI during the migration.** While `/converse` is in
   transition (steps 6 mid-flight), HA might briefly see incomplete
   responses. Mitigation: feature-flag the new code path in
   brain_server.py and flip via env var, so rollback is one restart.
3. **Reflex for "still thinking" during long tool calls.** Tier 3 §3.3
   Phase D mentioned this. Does the reflex live in deha (the body
   says "one sec" autonomously when no deltas arrive in 2s) or in
   the session service (sends a `speak_filler` directive to body)?
   Lean toward: session service sends the directive, body executes it.
   Keeps cognition decisions in cognition.

## Out of scope for this narrowing

- Multi-body fanout (presences[] schema is reserved, not used).
- Cross-channel handoff ("I was just telling you on signal about X —
  pick that up out loud"). Tier 3 §3.5 — wants the session service
  fully live first.
- Body-side caching of recent cognition replies (low value; cognition
  is now centralized).
- Auth/scope partitioning on body MCP tools. Same-host trust assumed
  today, per `mcp-server.md` §"What this doesn't cover".

## Cross-references

- `deha/docs/contracts/presence.md` — sensor fusion endpoint
- `deha/docs/contracts/mcp-server.md` — body MCP server tool surface
- `deha/docs/plans/voice-roadmap-2026-05-16.md` §3 — the prana session
  service that consumes escalations
- `prana/docs/plans/unified-mind-2026-05-11.md` — bus skeleton this
  narrowing fits into
- `prana/docs/plans/combined-rollout-2026-05-11.md` — Stage 4 is where
  presence and chat-bridge split land
- `~/.narada/projects/prana/2026/05-16.md` — host migration that put
  the supervised process tree in place
