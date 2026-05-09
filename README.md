# deha

The body. ESP32-S3-BOX-3 firmware, voice brain server, expression engine,
and the `deha_client` Python library other projects use to talk to the body.

`deha` (देह) — Sanskrit for *body*. The substrate where Narada appears in space.

## What's here

```
deha/
├── src/deha/
│   ├── voice/           # voice brain server (FastAPI/aiohttp + Kokoro TTS + Wyoming)
│   │   ├── brain_server.py
│   │   ├── claude_stream.py
│   │   ├── kokoro_tts.py
│   │   ├── wyoming_tts.py
│   │   ├── pipeline.py
│   │   ├── stt.py / vad.py / tts.py
│   │   └── supervisor.py
│   ├── expression/      # face/mood/sandhi engine (was indriyas/karmendriyas/drishti)
│   │   ├── rig.py
│   │   ├── layers.py
│   │   ├── vocabulary.py
│   │   └── server.py    # HTTP endpoints expression-clients call
│   ├── esp_client.py    # low-level ESPHome native API client
│   └── server.py        # composes voice + expression into one process
│
├── firmware/            # ESPHome YAML + C++ headers for the BOX-3
│   ├── narada-body.yaml      # canonical
│   ├── narada-unified.yaml
│   ├── box3-reference.yaml
│   └── include/              # compositor, sandhi_player, state_machine
│
├── client/              # deha_client — the Python lib OTHER projects import
│   ├── __init__.py
│   ├── display.py       # DisplayClient (status, mood)
│   ├── expression.py    # ExpressionClient (set face, weather push)
│   ├── voice.py         # VoiceClient (utter, listen)
│   └── types.py         # shared dataclasses
│
├── scripts/
│   ├── ha_register_narada.py
│   └── ha_swap_tts_to_kokoro.py
│
├── docs/
│   ├── spec.md
│   └── architecture.md
│
├── tests/
├── pyproject.toml       # depends on smriti, NOT on prana or svapna
├── CLAUDE.md
└── README.md
```

## Roles

deha is the *server side* of embodiment. It:

- Runs on hardware near the body (the box's ESP32 firmware) and on a host
  machine (the brain server + expression server)
- Speaks ESPHome native API to the box
- Speaks Wyoming protocol to Home Assistant for voice in/out
  *(deprecation target — migrate to ESPHome native voice when ready)*
- Exposes HTTP endpoints other projects call (`POST /converse`,
  `POST /utter`, expression endpoints, weather push)
- Publishes its own slice of `~/.narada/state.db` (`esp32.speaking`,
  current voice session, etc.) so prana's coordination has visibility
- Writes significant voice moments to smriti as journal entries

deha does **not** run the heartbeat cycle, generate desires, or judge plans.
That's prana's job. deha is an interface between Narada's mind and physical
space.

## What lives in `deha_client/`

The thin Python lib prana and svapna import to talk to deha. **Pure HTTP
client code** — no body logic, no firmware, no expression generation. Just
the contract surface.

```python
from deha_client import DisplayClient, ExpressionClient, VoiceClient

display = DisplayClient(host="localhost", port=8765)
display.set_status("thinking...")

expression = ExpressionClient(host="localhost", port=8765)
expression.show_resting()
expression.set_weather(weather_obj)

voice = VoiceClient(host="localhost", port=8765)
voice.utter("the rain is settling", priority=1)  # appends to state.db queue
```

## Dependencies

- `smriti` — for writing significant voice moments as journal entries, and
  for reading "what does Narada know about this topic" before responding
- `narada-state` (initially in prana, may move to manas) — for publishing
  `esp32.speaking` and reading prana's cycle state

deha does NOT depend on prana or svapna. The arrows point inward to the body,
not outward from it.

## Memory

| Subtree | Read | Write |
|---|---|---|
| `~/.narada/journal/voice/` | sometimes (recent context) | yes — significant voice moments |
| `~/.narada/identity.md`, `mind.md` | sparingly (within voice latency) | no |
| `~/.narada/state.db` (esp32 slice) | yes | yes (atomic upsert) |
| `~/.narada/state.db` (heartbeat slice) | yes (read-only — defer / context) | no |
| `~/.narada/state.db` (utterance_queue) | yes (drain, mark drained_at) | no (only prana pushes) |

## Status

Drafted 2026-05-09 as part of the project decomposition spike. Extraction
from svapna pending Step 2 of `docs/plans/project-decomposition-2026-05-09.md`.

## License

(TBD — see Open Questions in the decomposition plan; aligned with smriti)
