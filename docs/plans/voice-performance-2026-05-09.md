# Voice arc — performance plan

*Drafted 2026-05-09. Status of voice arc: HA + brain_server + Kokoro working
end-to-end on the BOX-3, voice = `am_puck`, Phase 1 supervisor live, but
turn latency is unmeasured and feels slack. This plan captures the levers
and the order to pull them.*

## Where we are now

Pipeline as-built today:

```
BOX-3 mWW (okay_nabu) ──┐
                        │ Wyoming
                        ▼
            wyoming-whisper container ──┐ STT
                                        │ HA Assist pipeline
                                        ▼
                       conversation.narada ─POST→ brain_server :8765
                                                   │
                                                   ▼
                                              claude-cli stream-json
                                              (persistent session, prewarmed)
                                                   │
                                                   ▼
                                              full text reply
                                                   │
                                                   ▼
                       brain_server :10210  ←─ HA TTS request
                       (Wyoming TTS, Kokoro)
                                                   │
                                                   ▼
                                              audio chunks → BOX-3
```

What's already optimized:
- Single persistent `claude -p` subprocess across turns (no cold start between turns)
- Kokoro prewarmed at brain_server startup (pays the model load at boot, not at first turn)
- Sentence-level chunking inside the TTS handler (first audio comes out before the full reply is synthesized)
- Stream-able text deltas exposed by `ClaudeStreamSession.stream()` (currently unused — see win #1)

## What "performant" means here

The honest target: **first audio plays within ~1.5 s of the user finishing speaking**, for short utterances. That's the threshold where it stops feeling like "talking to a system" and starts feeling like a conversation. Today we're estimated at 2.5–5 s.

The estimate is unmeasured. Step 0 is to verify before optimizing.

## Latency budget — estimated, unmeasured

| stage | est. ms | known unknowns |
|---|---|---|
| STT (whisper full-buffer) | 500–2000 | Real number depends on utterance length + whisper model size |
| HA pipeline overhead | 100–300 | Each stage transition has small overhead |
| Conversation (claude-cli, prewarmed) | 1000–1500 | first-token ~400 ms, then ~50 tok/s; must be _complete_ before HA hands to TTS |
| TTS first-chunk (Kokoro CPU) | 500–1000 | First sentence has to synthesize before audio starts |
| Network / buffer | 100–200 | LAN, negligible |
| **first-audio total** | **2200–5000** | |

Bold rows are the two we can move. The TTS row scales with reply length;
the conversation row is currently a hard wait because TTS doesn't start
until conversation finishes.

## Step 0 — Instrument before optimizing (do this first)

Without timing data we are guessing. HA debug logging is already on
(`assist_pipeline`, `wyoming`, `conversation`, `esphome.voice_assistant`).
A real turn produces enough log lines to extract every transition with
timestamps.

**Action:** write `scripts/voice_timing.py`. For one named turn (or the
last turn in a window), grep the HA + brain_server logs and emit:

```
T=0.000  wake_word_detected
T=0.082  voice_assistant.start
T=0.145  Synthesize → wyoming-whisper
T=0.812  wyoming-whisper transcript ("good morning")
T=0.851  conversation.narada POST /converse
T=1.604  /converse response text
T=1.625  HA Synthesize → brain_server :10210
T=1.687  Kokoro begin synth (sentence 1)
T=2.341  AudioStart (first chunk)
T=2.398  AudioChunk x N (...)
T=4.215  AudioStop
```

That gives a real budget table. Decisions about which lever to pull come
from this, not from the estimate above.

**Deliverable:** the timing extractor, plus one captured trace appended
to this document.

## Wins, ranked by leverage (pull in this order)

### Win #1 — Pipeline-level streaming: claude → kokoro → audio

**Estimated savings:** 1.0–1.5 s on first-audio latency. Biggest single win.

Today: claude generates the entire reply (~1.5 s for ~30 words) → HA
hands the whole text to TTS → kokoro synthesizes. TTS waits for
conversation to finish.

Target: claude emits text deltas → brain_server forwards them → HA's
pipeline begins TTS on the first sentence while later sentences are
still being generated → audio starts playing before the reply is done
generating.

What's needed:
- `conversation.narada` custom integration emits `intent_progress_event`
  (HA Assist pipeline mechanism for streaming partial text)
- brain_server's `/converse` endpoint changes shape: instead of
  returning the full text in one response, it streams text chunks
  (server-sent events or chunked HTTP). The plumbing on the
  brain_server side already exists in `ClaudeStreamSession.stream()`
  and `KokoroTTS.stream_async_text_iter()` — they are ready.
- HA's pipeline TTS step has to be configured to begin synth on
  partial transcripts. This may require HA ≥ a certain version;
  needs verification.

Risk: real engineering. Touches the custom integration and changes
brain_server's HTTP contract. The continue/end-turn sentinel logic
in `parse_continue` has to survive into the streaming version.

Why this is the right next thing: every other win compounds with this
one. Doing GPU Kokoro before streaming saves 0.5 s; doing it after
streaming saves 0.5 s on each sentence boundary.

### Win #2 — Kokoro on GPU — **TRIED AND REVERTED 2026-05-10**

**Estimated savings (pre-experiment):** 0.3–0.7 s on first-audio. Also
makes long replies cheaper (Kokoro generation goes from ~0.3× real-time
on CPU to ~5–10× real-time on the 3090).

**Outcome:** measured GPU steady-state warm at ~125–400 ms first-chunk
on tight benchmarks (~6× faster than CPU on identical-input loops), but
in real conversational use the GPU paid catastrophic JIT/autotune
penalties on shape changes and after idle periods. Specifically:

- Cold first synth after server start or long idle: **4–10 seconds**
  first-chunk (vs CPU 0.7–1.5 s baseline).
- A 30-second keepalive task (firing a tiny synth every 30 s) was added
  to keep ORT's CUDA caches warm. It kept *its own* shape warm (~200 ms
  steady-state floor for tiny inputs), but did not transfer to longer
  user-facing replies — those still paid 4 s+ first-chunk.
- Root cause: ONNX Runtime's CUDA EP does input-shape-dependent kernel
  selection and (for cuDNN) algorithm autotuning. Conversational TTS has
  continuously-varying input lengths, not repeating shapes. The CUDA EP
  is optimized for high-throughput batch inference where shapes repeat;
  our sparse, variable-shape workload defeats its caching model.
- Whack-a-mole fixes considered: rotating the keepalive across 3–5
  text-length buckets, switching to TensorRT EP for AOT compilation.
  Both add complexity with uncertain payoff for the input-length
  distribution we actually see.

**Decision:** revert to CPU. CPU's 0.7–1.5 s is predictable and
consistent across all reply lengths. The plan's projected 0.3–0.7 s
shave doesn't survive the realities of a live, sparse, variable-input
conversational workload.

**What stayed in the repo:** `[voice-gpu]` extras in `pyproject.toml`
(onnxruntime-gpu + nvidia-cudnn-cu12) for documentation and easy
re-experimentation. `KokoroTTS(device=...)` accepts `"cpu" | "cuda" |
"auto"`; default is `"cpu"`. `brain_server --tts-device cuda` flag
opts back in. The `_register_cuda_dll_dirs()` helper handles cuDNN PATH
setup if anyone re-tries.

**Caveat from the plan that was wrong:** the "GPU is light, ~500 MB" VRAM
estimate was conservative. Measured: ~6 GB resident, mostly ORT
cuDNN/cuBLAS workspaces, not Kokoro itself. Still well within 24 GB on
the 3090 — wasn't a blocker — but worth correcting.

(See "Captured timing traces" below for the GPU-with-keepalive trace
that triggered the revert.)

### Win #3 — Whisper streaming STT (Phase 2 of supervisor work)

**Estimated savings:** 0.3–0.5 s on STT. Smaller win but compounds.

Phase 2 of the supervisor work already plans absorbing wyoming-whisper
into brain_server (faster-whisper in-process). Doing this also enables
streaming-STT — partial transcripts emitted during the user's speech,
HA pipeline can start the conversation step before silence is even
declared end-of-utterance.

Side benefit: drops the wyoming-whisper docker container. End state:
1 container (homeassistant) + 1 host process (brain_server doing
STT + conversation + TTS).

Side benefit: solves the 15 s VAD bug indirectly — if STT acts on
partials, we don't depend solely on the server-side end-of-speech
detection that occasionally hangs.

### Win #4 — Sonnet 4.6 → Haiku 4.5 (audition only)

**Estimated savings:** ~50 % token-generation speed = ~500 ms on a
30-word reply.

Haiku produces faster but is less smart. For voice replies — short,
conversational — the persona quality difference may not be audible.
Worth a side-by-side once Win #1 is live (because token speed only
matters once the streaming bottleneck is removed).

Decision: defer until streaming is in. Then audition.

## Smaller knobs (catalog, not actions)

These are noted so we don't lose them, not necessarily to do.

- **Wyoming TTS chunk size** — currently 1024 samples (~43 ms). Could
  shave ~30 ms off first-audible-byte by going to 512. Marginal.
- **System prompt size** — `narada-voice.md` is the system prompt.
  After the first turn it's prompt-cached, so this only affects first-
  turn latency. Lean version would help if cold-start is a concern
  (post-reboot scenario).
- **`auto_gain` and VAD silence_seconds** — separate correctness
  issue (the 15 s stuck-listening bug), not a perf concern. Tracked
  outside this plan.
- **Kokoro model size** — Kokoro v1.0 is already small (310 MB).
  No smaller variant available.
- **mWW barge-in** — can't run during TTS due to i2s contention on
  BOX-3. Touch interrupt is the workaround. Not a latency thing,
  a UX thing.

## Phase 2 (next session) — Absorb wyoming-whisper into brain_server

Already on the task list as #13. Concretely:

1. Wrap `faster-whisper` as `WhisperSTT` (mirrors the `KokoroTTS` shape).
2. Add a Wyoming STT server in `brain_server.py` (mirrors `wyoming_tts.py`).
3. Write `scripts/ha_swap_stt_to_brain_server.py` (mirrors the kokoro swap).
4. Stop and remove `wyoming-whisper` container.
5. Extend the supervisor heartbeat to check the new STT port.

After Phase 2 completes: 1 docker container, 1 host process, fewest
moving parts that satisfy the architecture constraint (HA stays in
docker because moving it out is a 60-min migration).

## Project split context

This work is destined for the `deha` project per `docs/plans/project-
decomposition-2026-05-09.md`. The voice/embodiment lifecycle
(supervisor, brain_server, kokoro_tts, wyoming_tts, claude_stream,
ha_swap_*, audition_voices) is the embodiment surface, not the
training judge. When the split happens:

- Files that move to `deha/`:
  - `src/svapna/embodiment/voice/` → `deha/voice/`
  - `embodiment/firmware/` → `deha/firmware/`
  - `scripts/ha_*` → `deha/scripts/`
  - `scripts/install_narada_supervisor_task.ps1` → `deha/scripts/`
  - `scripts/audition_voices.py` → `deha/scripts/`
  - `scripts/voice_timing.py` (when written) → `deha/scripts/`

The split is **gated** on this perf plan being right. We want to know
the contract `deha` should expose before we commit to the boundary.
Specifically:

- Does the brain_server's HTTP `/converse` API stay non-streaming,
  become streaming (Win #1), or both? That decides the public
  surface of `deha_client` (the Python lib referenced in the
  decomposition plan).
- Does brain_server own the Wyoming protocol implementations, or do
  they live in a shared `deha-server` that the Hermes Agent can also
  drive? Streaming bus shape depends on this.

Resolve those after Win #1 is implemented and the timing trace shows
where the savings actually came from. Then split.

## Sequencing — concrete path forward

1. **Step 0 — instrument**: write `voice_timing.py`, capture one
   real "good morning" trace, append numbers to this doc.
2. **Decide based on the trace** whether Win #1 is worth the
   engineering, or whether Win #3 (which is smaller scope) gets us
   most of the way.
3. If Win #1: design the streaming `/converse` API; modify
   conversation.narada to emit `intent_progress_event`; verify HA
   version supports the streaming TTS path.
4. **Win #2** is a 30-minute win. Land it whenever, doesn't gate
   anything. Could land in parallel with Win #1.
5. **Phase 2** (Win #3 + container retirement) when Win #1 is stable.
6. **Project split** after Phase 2 when contracts are settled.

## Open questions

These need answers before the next major chunk of work:

- **Does HA's Assist pipeline actually support streaming TTS in the
  version you're running?** `intent_progress_event` is the mechanism
  but the TTS-begin-on-partial-text behavior depends on HA version.
  Check release notes / source before designing Win #1 around it.
- **Is the conversation.narada custom integration already on a recent
  HA SDK?** If not, Win #1 might require an SDK version bump.
- **Once the timing trace exists, is the bottleneck where we think
  it is?** STT may be faster than estimated; conversation may be
  slower; TTS may already be fine. The estimate could be wrong in
  either direction.

## Done state

This plan is "done" when:
- The timing trace exists and is appended below.
- Wins #1 and #2 are live OR explicitly deferred with reasons.
- The project split contracts (HTTP API shape, Wyoming protocol
  ownership) are decided.

After that, the project split happens in a separate session.

---

## Captured timing traces

*Append real traces here as they're collected. First trace blocked
on `voice_timing.py`.*

### 2026-05-10T04:15:06+10:00 -- good morning + followup, CPU Kokoro, sonnet

```
# T0 = 2026-05-09T18:13:32.742000+00:00  (microvad_init)

T= 0.000  [ha] microVAD initialized
T= 4.804  [ha] voice command started
T= 7.432  [ha] voice command finished
T= 8.258  [sup] /converse received  'Okay, now, what are you up to today?'
T= 8.528  [ha] STT result  'Okay, now, what are you up to today?'
T= 8.529  [ha] HA -> /converse
T=10.258  [sup] /converse returned  (2281 ms server-side)
T=10.810  [sup] TTS Synthesize received
T=10.825  [ha] HA conversation step done
T=11.425  [sup] first audio chunk
T=11.973  [sup] TTS done (AudioStop)  (am_puck chunks)

## derived
  STT (vad_end -> stt_result)     1.096 s
  Conversation (claude)           2.000 s
  TTS first-audio (synth -> chunk)  0.615 s
  TTS total (synth -> done)       1.163 s
  End-to-end (vad_end -> synth_first_chunk)  3.993 s
  First-audio from utterance start (vad_start -> synth_first_chunk)  6.621 s
```

**Headline: end-to-end first-audio is 3.99 s.** Plan target was 1.5 s. We're
2.5 s over.

Breakdown vs the plan's pre-measurement estimate:

| stage                   | plan estimate | measured  | verdict     |
|-------------------------|--------------:|----------:|-------------|
| STT (vad_end -> result) |  500-2000 ms  |  1096 ms  | in budget   |
| Conversation (claude)   | 1000-1500 ms  |  2281 ms  | **over**    |
| TTS first-chunk         |  500-1000 ms  |   615 ms  | in band     |

Conversation is the long pole. The reply was 9 words, so at ~50 tok/s
Sonnet generation that's ~180 ms of token-generation; the remaining
~2.1 s is first-token latency on the prewarmed claude-cli session. This
is the case Win #1 (streaming `/converse`) targets — TTS could have
started on the first sentence delta instead of waiting for the full
reply. For a one-sentence reply the streaming win is smaller in
absolute ms (no later sentences to overlap) but still gives first audio
on the first comma instead of the period. For longer replies the
compounding effect is large.

TTS first-chunk at 615 ms is comfortably in the plan's CPU-Kokoro band.
Win #2 (Kokoro on GPU) would push this to ~150 ms — a 0.4-0.5 s shave.

Caveat: n=1. Need a few more turns to know whether 2.28 s claude
latency is typical or noisy.

Side observation, not on the optimization path: the **first** wake-word
attempt in this session never triggered HA's `Voice command started`
/`finished` events. HA sat for 16 s after `Initialized microVAD`, then
ran STT on the buffered audio anyway and fired `/converse` — but no
Synthesize event followed, so no TTS. This matches the "15 s VAD bug"
the plan flags as a separate correctness issue. Recording it here so
the bug isn't lost: it now reproduces in deha post-extraction too, so
the fix lives in either the BOX-3 firmware VAD config or HA's pipeline,
not in the now-deleted svapna voice tree.
