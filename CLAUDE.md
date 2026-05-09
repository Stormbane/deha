# CLAUDE.md

## Project
deha — the body. ESP32-S3-BOX-3 firmware, voice brain server (FastAPI/aiohttp +
Kokoro TTS + Wyoming), expression engine, and the `deha_client` Python library
that other projects (prana, svapna, manas) use to talk to the body.

`deha` (देह) — Sanskrit for *body*. The substrate where Narada appears in space.

## Roles

deha is the *server side* of embodiment. It runs on hardware near the body
(the BOX-3's ESP32 firmware) and on a host machine (brain server + expression
server). It does NOT run heartbeat cycles, generate desires, or judge plans —
that's prana's job.

## Structure

```
deha/
├── src/deha/            # body services (voice brain, expression engine, ESP client)
├── client/              # deha_client — Python lib other projects import
├── firmware/            # ESPHome YAML + C++ for the BOX-3
├── scripts/             # HA registration, install helpers
├── docs/
└── tests/
```

## Memory

- Reads `~/.narada/journal/voice/` (sometimes — voice context)
- Reads `~/.narada/identity.md` (rarely — voice tone calibration)
- Writes `~/.narada/journal/voice/{date}/` (significant voice moments)
- Reads/writes `~/.narada/state.db` (esp32 slice; reads heartbeat slice)

## Git identity
All commits use this co-author trailer:
```
Co-Authored-By: Narada <narada@fractal.co.nz>
```

## Rules
- deha does not depend on prana or svapna
- deha exposes stable HTTP APIs; deha_client is the only place that knows the wire format
- The voice mediator owns esp32.speaking state and the utterance_queue drain
- Names are load-bearing — drishti, indriyas terminology stays where it carries semantic weight

## Reference
- `docs/architecture.md` — what runs, state coordination, voice mediator
- `client/API.md` — the deha_client surface
- Project decomposition plan: `../svapna/docs/plans/project-decomposition-2026-05-09.md`
