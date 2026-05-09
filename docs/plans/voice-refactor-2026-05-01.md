# Voice bridge refactor — barebones over feature parity

*2026-05-01 · companion to `docs/research/ha-esphome-findings-2026-05-01.md`*

## The decision the rest of this doc hangs on

**Don't make HA a dependency. Don't port HA either. Move to `media_player:` mode and most of HA's logic becomes irrelevant.**

The findings doc shows that nearly every blocker is downstream of running in legacy `speaker:` mode. The temptation after that finding is "let's bring HA's `_stream_tts_audio` pacing in, add their VAD, mirror their pipeline." That's the wrong direction. HA's pipeline is solving a problem we don't have — they're a general-purpose smart-home bus serving an open ecosystem of devices and brains. We have one device and one brain.

What HA actually does well, and which of those things matter to us:

| HA capability | Lines of HA code | Do we need it? |
|---|---|---|
| `assist_pipeline.pipeline` (orchestration) | ~2000 | No. `Pipeline` in our bridge does it in 200. |
| `assist_satellite._stream_tts_audio` (16k WAV pacing) | ~60 | **No, once on media_player.** URL fetch removes the pacing problem. |
| `assist_satellite` event protocol mapping | ~200 | No. We already speak the `aioesphomeapi` event API directly — that *is* the protocol. |
| `assist_pipeline.vad` (Silero VAD wrapper) | ~150 | **Yes, but as a small copy, not via HA.** Silero is pip-installable. |
| `wake_word` engine abstraction | ~300 | No. Wake word is on-device (mWW). |
| `tts` streaming + caching | ~1500 | No. Edge-TTS is one async call; we don't need a cache layer. |

**Net: lift the VAD pattern (~50 lines), copy the YAML topology from voice-pe verbatim (with attribution), and otherwise stay our own thing.**

### Why not HA-as-dependency

`pip install homeassistant` pulls 800+ MB of transitive deps (zigbee, mqtt, every integration). `homeassistant.components.assist_pipeline.pipeline` imports `homeassistant.core.HomeAssistant`, `homeassistant.config_entries.ConfigEntry`, and the whole bus/entity-registry topology. None of these are decomposable. To use one class we'd boot a partial HA instance. That's the opposite of barebones.

### Why not copy more of HA

The pacing-and-streaming code in `assist_satellite.py:639-697` exists to feed PCM into a device that only supports legacy `speaker:` mode. We're moving off that mode. The code we'd be copying is the code we'd be deleting six months later.

### What we DO copy

1. **The Silero VAD wrapper pattern** from `_refs/ha-core/homeassistant/components/assist_pipeline/vad.py`. Their hangover-buffer logic is good. We use `pysilero-vad` directly (pip), reproduce their decision logic, ~50 lines. Attribution comment.
2. **The voice-pe YAML topology** for the firmware — `media_player.speaker_source` + announcement_pipeline + resampler + mixer chain. This is configuration, not code, but it's the load-bearing copy.
3. **Probability-cutoff presets** from voice-pe (`home-assistant-voice.yaml:1772-1802`) — three sensitivity levels with empirically validated cutoffs from the Dinner Party Corpus. Free quality.

That's the whole adoption surface.

---

## Refactored file structure

Today: one 571-line script (`scripts/voice_pipeline.py`) that does HTTP server, brain, TTS, STT, VAD, event protocol, lifecycle. It works, but it's monolithic and not importable.

Proposed:

```
src/svapna/embodiment/voice/
  __init__.py
  bridge.py          — entrypoint. APIClient lifecycle, signal handling,
                       wires the modules below. ~120 lines.
  pipeline.py        — Pipeline: per-turn state machine. handle_start,
                       handle_audio, handle_stop, _process. ~200 lines.
  protocol.py        — Event helpers, state-flag enums, the empty-URL
                       workaround documented with a citation to
                       voice_assistant.cpp:746. ~50 lines.
  vad.py             — Silero VAD with hangover buffer. Pattern adapted
                       from HA's assist_pipeline/vad.py with attribution.
                       ~80 lines.
  stt.py             — faster-whisper wrapper. Off-loop transcribe(),
                       returns text. ~40 lines.
  brain.py           — Brain protocol + ClaudeCodeBrain impl. Easy to
                       swap for HeartbeatBrain later (smriti-aware).
                       ~50 lines.
  tts.py             — edge-tts → FLAC encoder via PyAV. Returns bytes
                       + path-served-URL. ~80 lines.
  http_server.py     — TTSServer (aiohttp). Serves FLAC for media_player
                       URL fetch. ~50 lines.
  config.py          — argparse + typed Config dataclass. ~50 lines.

scripts/
  voice_pipeline.py  — thin CLI wrapper:
                       `from svapna.embodiment.voice.bridge import main; main()`

embodiment/voice/
  narada-voice.md    — system prompt, unchanged.

embodiment/firmware/
  narada-voice-test.yaml  — refactored to media_player mode + mWW + PSRAM
                            tuning. Eventually merged into narada-body.yaml.

tests/embodiment/
  test_pipeline.py   — turn lifecycle with fake APIClient.
  test_vad.py        — Silero VAD with synthesized audio.
  test_protocol.py   — event payload shapes.
```

Total: ~720 lines across modules vs 571 today. Slightly more code, much more testable, and each module has one job.

### Why move from `scripts/` to `src/svapna/embodiment/voice/`

- `scripts/` is for CLI glue. `src/svapna/` is for code that gets imported, tested, and reused. The voice bridge is the latter. Heartbeat will eventually need to invoke speech; embodiment will eventually have non-voice IO needing the same APIClient lifecycle.
- Keeps the existing one-file CLI entry available (`python scripts/voice_pipeline.py`) — but the entry is now a wrapper.
- Tests can import the modules without the `sys.path` dance.

---

## What changes, in order

Phased so each step is independently reversible and testable. Phase 0 is "before refactor" (firmware-only fixes that prove the architectural direction). Phase 1 is the refactor. Phase 2 is the integration.

### Phase 0 — prove the architectural fix (no Python refactor yet)

Goal: validate that media_player mode + mWW + PSRAM tuning actually resolves blockers #1-4 before committing to a refactor.

**0.1 Compile-test voice-pe YAML in our ESPHome install.**
```
esphome compile _refs/home-assistant-voice-pe/home-assistant-voice.yaml
```
- If it builds: toolchain is fine; our YAML is the issue.
- If it fails: pioarduino artifact is broken; we wait or pin a different ESPHome version.
- This unblocks the rest.

**0.2 Fix `narada-voice-test.yaml` minimally.** Single commit:
- Add `CONFIG_SPIRAM_RODATA: "y"` and `CONFIG_SPIRAM_FETCH_INSTRUCTIONS: "y"` to `sdkconfig_options`.
- Switch `voice_assistant:` from `speaker:` to `media_player:` mode.
- Add `media_player: platform: speaker` block with announcement_pipeline (FLAC, 48kHz, mono) — copy verbatim from `_refs/wake-word-voice-assistants/esp32-s3-box-3/esp32-s3-box-3.yaml:178-188`.
- Re-enable `micro_wake_word:` with the modern schema (microphone source, gain_factor 4, vad, stop_after_detection: false, okay_nabu).
- Add `voice_assistant: micro_wake_word: mww, use_wake_word: false` explicitly.
- Drop `buffer_duration: 300ms` workaround (resampler handles it).
- Keep touch handler as fallback while we trust mWW.

**0.3 Stub bridge update.** Two-line change in current `voice_pipeline.py`:
- TTS path: encode to FLAC via PyAV instead of PCM, write via `tts_server.write_flac()`, send `TTS_END {url: server_url}`. Drop the `send_voice_assistant_audio` chunk loop.
- Drop the empty-URL workaround commentary — it's no longer load-bearing once mediaplayer is wired.

**Exit criterion for Phase 0:** Wake word fires on-device. TTS plays through the URL path at the right pitch. No more 300ms ringbuffer hack. If yes → proceed to refactor. If no → diagnose without refactor confusion.

### Phase 1 — the refactor

Goal: split the monolith into the proposed module layout. No behavior change.

**1.1 Create `src/svapna/embodiment/voice/`.** Empty `__init__.py`, then copy the existing `voice_pipeline.py` into `bridge.py` unchanged. Verify `python -m svapna.embodiment.voice.bridge` runs the same loop.

**1.2 Extract modules in order of independence:**
- `config.py` first (just argparse + dataclass).
- `protocol.py` next (constants and helpers — pure, no async).
- `http_server.py` next (it's already a class).
- `tts.py` (factor out `tts_mp3` + `decode_mp3_to_pcm` — the latter becomes `decode_mp3_to_flac`).
- `stt.py` (factor out the whisper bits in `_process`).
- `brain.py` (factor out `ClaudeCodeBrain`).
- `vad.py` last — this is where we replace RMS with Silero.
- `pipeline.py` is what's left.

Each extraction is its own commit. After each, the bridge runs identically.

**1.3 Replace RMS VAD with Silero.**
- `pip install pysilero-vad` (single dep, ~3 MB ONNX runtime + the model).
- New `vad.py`: 30ms frames, hangover buffer of 800ms, decision based on `silero.process_chunk()` returning P(speech).
- Pattern lifted from `_refs/ha-core/homeassistant/components/assist_pipeline/vad.py:VoiceCommandSegmenter` — header-cite `# adapted from HA assist_pipeline/vad.py — Apache 2.0`.
- Replaces `_vad_watch` in `Pipeline`. Same interface (silence callback → STT_VAD_END), better signal.

**1.4 Add tests.**
- `test_protocol.py`: round-trip event payload shapes.
- `test_vad.py`: Silero against synthesized speech + silence buffers.
- `test_pipeline.py`: fake APIClient, drive a full turn, assert the event sequence.

**Exit criterion for Phase 1:** all modules under `src/svapna/embodiment/voice/`. Tests pass. Bridge behavior unchanged. The CLI script is now a 5-line wrapper.

### Phase 2 — integration

Goal: fold voice firmware back into `narada-body.yaml`, and make the bridge usable from heartbeat.

**2.1 Merge `narada-voice-test.yaml` into `narada-body.yaml`.** Display + scene return; voice path stays. Verify I2C bus contention is clean (display + audio currently share `i2c_adc_dac` in our YAML; voice-pe separates them — may need to mirror).

**2.2 Brain abstraction usable by heartbeat. Stub HeartbeatBrain in Phase 1.** `Brain` becomes a protocol with two impls:
- `ClaudeCodeBrain` — current behavior, shells out to `claude -p` (and now `--resume` for multi-turn — see Phase 1.5).
- `HeartbeatBrain` — stubbed in Phase 1 (returns a placeholder reply that names what it WILL do: smriti read → viveka → generate). Real implementation is future work, but having the protocol shape locked from day one prevents a rewrite. The stub lets us test brain swap-in via a CLI flag.

**2.3 Move TTS off edge-tts to Piper.** Decided. Piper is local, fast (~50ms for a 5s utterance on CPU, less on GPU), runs ONNX, and we own the failure mode. Voices ship as `.onnx` + `.json` config files. Pick a base voice now (e.g. `en_GB-alan-medium` for sage register, or audition a few against narada-voice.md prompts); a future arc trains a custom voice via Piper's training pipeline.

Implementation simplification: Piper outputs WAV at the model's native rate (22.05 kHz for medium voices). Announcement_pipeline can take WAV directly with `format: WAV` and resample to 48 kHz internally. We can drop the FLAC encode step entirely — saving ~50ms of PyAV work per utterance and removing one library dependency. Or keep FLAC for the bandwidth (3× smaller over wire, irrelevant on LAN).

Recommendation: WAV for simplicity. Revisit if we ever go off-LAN.

### Phase 1.5 — multi-turn conversation state

Goal: Narada remembers "last turn" within a conversation window. ChatGPT-voice parity is the bar.

`claude -p` supports `--resume <session-id>` and emits the session ID in the JSON output (with `--output-format json`). The shape:

```python
class ClaudeCodeBrain:
    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self._last_session_id: str | None = None
        self._last_turn_at: float = 0.0
        self._conversation_window_s = 30.0  # silence > this → new session

    async def respond(self, user_text: str) -> str:
        now = time.time()
        cmd = ["claude", "-p", "--system-prompt", self.system_prompt,
               "--output-format", "json", "--disallowedTools", ...]
        if (self._last_session_id is not None
                and now - self._last_turn_at < self._conversation_window_s):
            cmd += ["--resume", self._last_session_id]
        # ... run, parse JSON, extract session_id and reply
        self._last_session_id = parsed["session_id"]
        self._last_turn_at = now
        return parsed["reply"]
```

Window heuristic to start: 30 seconds of silence ends the conversation. Tunable. Wake word during an active conversation continues the session; wake word after the window starts fresh. Could also expose a "new conversation" wake word variant later.

### Phase 1.6 — streaming TTS

Goal: latency parity with ChatGPT voice mode. Total response latency = first-token-time + first-audio-chunk-time, not full-response-time + full-tts-time.

The architectural unlock: the announcement_pipeline can receive audio via *either* URL fetch *or* the API audio path. Both feed the same speaker. So we add streaming as a second TTS mode without giving up the URL mode.

Pipeline:
1. Brain streams tokens via `claude -p --output-format stream-json`.
2. Sentence segmenter buffers tokens, emits a sentence when `[.!?]` boundary hits or buffer exceeds N tokens.
3. Each sentence → Piper → WAV bytes → `send_voice_assistant_audio()` chunks.
4. First sentence audio plays while the brain is still generating.

Latency win: roughly (first-token-time + first-sentence-tts-time) ≈ 400-800ms vs the current (full-response-time + full-tts-time) ≈ 2000-3000ms.

Two TTS modes coexisting:
- **Streamed** (low-latency, Piper sentence-by-sentence via API audio): default for conversational turns.
- **URL-fetched** (full FLAC/WAV via media_player): used for cached responses, songs, longer narrative content. Not on critical path for now.

Module shape: `tts.py` exposes `synthesize_streaming(text_chunks)` async generator yielding `bytes` (PCM at the speaker's native rate). `pipeline.py` consumes that generator, paces chunks via `send_voice_assistant_audio`. Sentence segmentation lives in `tts.py` too.

Implementation order: ship Phase 1 with full-utterance URL playback (simpler, validates everything). Add streaming as Phase 1.6 once the round-trip works end-to-end. Keep both — the URL path is the fallback if streaming breaks.

---

## Concrete YAML diff (Phase 0.2 preview)

```yaml
esp32:
  board: esp32s3box
  flash_size: 16MB
  cpu_frequency: 240MHz
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_ESP32S3_DEFAULT_CPU_FREQ_240: "y"
      CONFIG_ESP32S3_DATA_CACHE_64KB: "y"
      CONFIG_ESP32S3_DATA_CACHE_LINE_64B: "y"
      CONFIG_SPIRAM_RODATA: "y"                 # NEW — required for mWW
      CONFIG_SPIRAM_FETCH_INSTRUCTIONS: "y"     # NEW — required for mWW

audio_dac:
  - platform: es8311
    sample_rate: 48000                          # CHANGED 16000 → 48000

speaker:
  - platform: i2s_audio
    id: box_speaker
    sample_rate: 48000                          # CHANGED 16000 → 48000
    bits_per_sample: 16bit
    channel: left
    audio_dac: es8311_dac
    # No more buffer_duration override — default works at 48kHz
    # buffer_duration: 300ms                    # REMOVED

# NEW — wraps box_speaker, exposes URL playback to voice_assistant
media_player:
  - platform: speaker
    id: box_media_player
    announcement_pipeline:
      speaker: box_speaker
      format: FLAC
      sample_rate: 48000
      num_channels: 1

# NEW — on-device wake word
micro_wake_word:
  id: mww
  microphone:
    microphone: box_mic
    channels: 1
    gain_factor: 4
  stop_after_detection: false
  vad:
  models:
    - model: okay_nabu
  on_wake_word_detected:
    - voice_assistant.start:
        wake_word: !lambda return wake_word;

voice_assistant:
  id: va
  microphone: box_mic
  media_player: box_media_player                # CHANGED speaker → media_player
  micro_wake_word: mww                          # NEW
  use_wake_word: false                          # NEW (explicit, not default)
  noise_suppression_level: 2
  auto_gain: 31dBFS
  volume_multiplier: 2.0
  # ... existing on_* loggers unchanged
```

## Concrete bridge diff (Phase 0.3 preview)

In `_process`, replace the PCM streaming block with:

```python
# media_player URL playback. Encode to FLAC, serve over HTTP,
# tell the device the URL — the device fetches and plays through
# the announcement_pipeline (resampler → mixer → I2S).
mp3 = await tts_mp3(reply, voice=self.tts_voice)
flac_bytes = await asyncio.to_thread(encode_mp3_to_flac, mp3)
url = self.tts.write_flac(flac_bytes)
await self._event(
    VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
    {"text": reply},
)
await self._event(
    VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
    {"url": url},
)
```

(No more chunk loop, no more pacing, no more placeholder URL, no more `send_voice_assistant_audio`.)

`encode_mp3_to_flac` is one PyAV invocation:
```python
def encode_mp3_to_flac(mp3_bytes: bytes) -> bytes:
    import av, io
    src = av.open(io.BytesIO(mp3_bytes))
    out_buf = io.BytesIO()
    dst = av.open(out_buf, mode="w", format="flac")
    out_stream = dst.add_stream("flac", rate=48000, layout="mono")
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=48000,
    )
    for frame in src.decode(audio=0):
        for resampled in resampler.resample(frame):
            for packet in out_stream.encode(resampled):
                dst.mux(packet)
    for packet in out_stream.encode(None):
        dst.mux(packet)
    dst.close(); src.close()
    return out_buf.getvalue()
```

## Performance properties of the refactored design

- **No PCM streaming over the API.** TTS is one HTTP fetch the device initiates. Device-side ring buffer is replaced by the announcement_pipeline's internal buffer (handled by ESPHome).
- **Sample rate native at 48 kHz.** No more chipmunking. No more "speaker has the wrong rate."
- **Wake word on-device.** No latency from streaming to laptop and back. Sub-200ms wake response.
- **Silero VAD on Python side.** ONNX runtime, single-threaded, ~0.5ms per 30ms frame. Negligible.
- **No HA framework boot.** No event bus, no entity registry, no state machine on the brain side. Direct flow: APIClient event → Pipeline method → STT/Brain/TTS → APIClient event.
- **Bridge memory footprint unchanged.** Faster-whisper is the dominant resident memory; everything else is small.

## What we're explicitly NOT doing

- Adopting wyoming protocol. ESPHome's native API is what the device speaks; wyoming is HA's pluggable abstraction over it. We don't need the indirection.
- Building a generic conversation engine. There's one brain. It's claude. (And eventually heartbeat.)
- Caching TTS. Piper is fast enough that synthesis is cheaper than cache lookups for our utterance lengths.

## Risks

- **Phase 0.1 may fail upstream.** If pioarduino is broken for everyone, we're stuck waiting. Mitigation: pin to whatever ESPHome version was working before the upgrade attempt.
- **mWW may still fail to allocate even with PSRAM flags.** Less likely (voice-pe's flags are specifically for this) but possible — fallback is bumping `tensor_arena_size` per model.
- **FLAC encoding latency.** PyAV FLAC encode of a 5-second utterance is maybe 50-100ms — negligible vs the 1-2s of STT and 800ms of brain. Worth measuring once.
- **HA copy attribution.** The Silero VAD pattern is Apache 2.0 — easy to attribute. Just don't forget the comment header.

## Open questions for Suti

1. **Heartbeat brain integration in Phase 2.3 — wait or build now?** Sketching it now might inform the brain.py protocol shape; building it now is scope creep.
Suti: yeah we should build it not, at least stub it out 
2. **Drop edge-tts for Piper?** Piper is local + fast + we own the failure mode. Edge-tts is "free Indian English male voice" which we like. Quality call. 
Suti: yeah lets go to piper, we can pick a new voice or train one eventually.
3. **Display merging (2.1) — do voice and scene actually need to share I2C, or should we route them on different buses?** voice-pe separates them. Worth understanding why before merging.
Suti: which is more performant?

**Answer: shared is fine, performance argument doesn't apply on our hardware.** Audio data flows over I2S (a separate, dedicated bus). I2C carries only control-plane traffic — DAC/ADC config (one-time at boot), touchscreen interrupts (event-driven, not polled), and occasional volume changes. No high-bandwidth traffic. voice-pe separates because their dev kit exposes more physical I2C pins and they wanted clean isolation; on the BOX-3 we have one exposed I2C bus, and adding a second would mean software bit-banging or finding another peripheral, neither of which is free. Keep `i2c_adc_dac` shared between audio chips, touchscreen, and (display config — display *data* is on SPI). Performance impact: zero. Closing this question.
4. Suti: These need to be reconsidered:
- Multi-turn conversation state. Each turn is independent; the brain is stateless across turns (smriti is the long-term memory).
- Streaming TTS chunks (LLM-streaming-as-it-generates). Nice to have, real latency win, but adds complexity and the announcement_pipeline doesn't natively chunk-stream FLAC. Defer.
We want to implement multi-turn conversation state (via claude -p resume session maybe?) eventually.
Streaming TTS chunks would also be highly ideal. If speaking to Narada is not as performant (or close) as speaking to Gemini or ChatGPT then it is unlikely to be used.

**Reworked.** Both moved out of "not doing" and into the phased plan. Multi-turn is now Phase 1.5 using `claude -p --resume <session-id>` with a 30s silence-window heuristic. Streaming TTS is Phase 1.6, exploiting that announcement_pipeline can take audio via *either* URL fetch *or* the API audio path — so we ship full-utterance URL playback in Phase 1, then add streaming via the API audio path in Phase 1.6 without giving up the URL mode. Latency target: first-audio-chunk < 800ms from end-of-user-speech. ChatGPT voice mode is the bar.

I was wrong about the announcement_pipeline not supporting chunk-streamed audio — it does, just via a different code path than URL fetch. Correction noted.