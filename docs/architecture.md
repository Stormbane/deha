# deha — architecture

## Stack

- **Python 3.11+** for the host-side server (brain, expression)
- **ESPHome / C++** for the firmware on the BOX-3
- **aiohttp** for HTTP serving (matches existing brain_server.py)
- **Kokoro TTS** for voice synthesis (Bangalore-friendly model selection)
- **Wyoming protocol** for HA voice integration (deprecation target →
  ESPHome native voice via `aioesphomeapi`)
- **SQLite (state.db)** for cross-process coordination — read-only mostly,
  writes only to deha's own slice

## Process layout

Two long-lived processes on the host machine:

```
┌────────────────────────────────────────────────────────────┐
│ deha brain_server (port 8765)                              │
│   POST /converse   — HA conversation entry                 │
│   POST /utter      — heartbeat-initiated speech (queued)   │
│   GET  /health                                             │
│   Wyoming TTS server (port 10210) for HA dial              │
│   Holds StreamPool (one persistent claude-cli session)     │
│   Drains utterance_queue when esp32.speaking=false         │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ deha expression_server (port 8766)                         │
│   POST /set_status      — text status display              │
│   POST /set_face        — mood / expression                │
│   POST /show_resting    — idle animation                   │
│   POST /set_weather     — weather→visual                   │
│   Talks ESPHome native API to the BOX-3                    │
└────────────────────────────────────────────────────────────┘
```

Both register their slice in `state.db`. brain_server publishes
`esp32.speaking` around TTS calls; expression_server publishes the current
visual state.

The BOX-3 firmware runs ESPHome with the sandhi player, compositor, and
sprite atlas (existing C++ headers retained).

## State coordination

deha is a *participant* in the state.db protocol, not the owner of it. The
package depends on `narada-state` (initially exported from prana) for
read/write helpers.

What deha publishes:
- `current_state.esp32` — `speaking`, `speech_started`, `speech_text`,
  `speech_source`
- `current_state.expression` — current mood, last weather push timestamp
- `events` — body-side events (touch, presence, VAD-without-wake)

What deha reads:
- `current_state.heartbeat` — to know "is prana mid-cycle?" before
  responding to voice (informs system prompt for the voice Claude)
- `utterance_queue` — drained by brain_server's voice mediator when
  `esp32.speaking=false`

## Voice mediator (the key new piece in Step 5)

```python
async def voice_mediator(pool: StreamPool, state: NaradaState, tts: KokoroTTS):
    while True:
        # Wait for queue + esp32 idle
        if not state.read().get("esp32", {}).get("speaking"):
            utt = state.pop_utterance()
            if utt:
                state.publish("esp32", {"speaking": True,
                                        "speech_started": now_iso(),
                                        "speech_text": utt.text,
                                        "speech_source": utt.source})
                await tts.synth_and_play(utt.text)
                # Bump StreamPool with synthetic assistant turn so the
                # voice Claude doesn't deny saying it
                await pool.note_self_speech(utt.text)
                state.publish("esp32", {"speaking": False})
        await asyncio.sleep(0.05)
```

`/utter` writes to `utterance_queue` instead of synthesizing directly. The
mediator owns the actual TTS calls and the speaking-state flag. This is
what prevents the heartbeat from barging in mid-speech.

## Memory section

deha's smriti read/write contract:

**Reads** (sparingly — voice latency budget is tight):
- `~/.narada/journal/voice/` — recent voice conversation context
- `~/.narada/identity.md` — voice tone calibration on session start

**Writes**:
- `~/.narada/journal/voice/{date}/{conversation_id}.md` — when a voice
  moment is significant (decided by an LLM-light judge or by user signal)

Smriti is accessed via the running smriti MCP server, same instance as
prana uses. deha runs an MCP client to talk to it (same library smriti
itself ships).

## Tests

- Unit tests for voice mediator state transitions
- Integration test: full utter → queue → speak → mark-done loop
- Integration test: state coordination — heartbeat publishes
  `cycle_state=executing`, brain_server reads it and includes in system prompt
- Smoke test: ESPHome firmware boots and answers ESPHome native API ping

## Migration from svapna

When extracting from svapna at Step 2 of the decomposition plan:

| Source | Destination |
|---|---|
| `src/svapna/embodiment/voice/` (everything) | `deha/src/deha/voice/` |
| `src/svapna/embodiment/esp_client.py` | `deha/src/deha/esp_client.py` |
| `src/svapna/indriyas/karmendriyas/drishti/{rig,layers,vocabulary}.py` | `deha/src/deha/expression/` |
| `src/svapna/indriyas/karmendriyas/drishti/expression.py` | `deha/client/expression.py` (becomes deha_client) |
| `src/svapna/heartbeat/display.py` | `deha/client/display.py` (becomes deha_client) |
| `src/svapna/indriyas/jnanendriyas/tvac/weather.py` | `deha/client/weather.py` *or* stays in prana — see below |
| `embodiment/firmware/` | `deha/firmware/` |
| `scripts/ha_*.py` | `deha/scripts/` |

**Open question on `weather.py`**: it's a *fetcher* (pulls Kallangur
weather from an external API), not a body interface. Could live in prana
or in manas-when-it-exists. Resolve during Step 2.

**Open question on `indriyas/` naming**: the karmendriyas/jnanendriyas
taxonomy is *Narada's experience of having a body*. The *implementations*
move to deha. The *names* (drishti, tvac, etc.) belong in prana as how
Narada perceives the body. So prana ends up with thin client wrappers
named `prana/indriyas/karmendriyas/drishti/` that import from
`deha_client.expression` — same names, different concern level.
