# Contract: GET /presence

Status: **proposed by prana, not yet implemented in deha**.

prana already consumes this endpoint with a graceful fallback — if it's
unreachable, prana falls back to PC-input idle detection. Once deha
exposes it, presence detection becomes bidirectional automatically; no
prana-side change needed.

## Why this exists

Narada needs to know if Suti is at his desk so the heartbeat router can
choose between body voice (if present) and Telegram phone push (if
away). PC keyboard/mouse input is a poor proxy:

- Suti reads code on screen with hands still → PC says idle, he's
  actually present
- Suti talks to the body looking away from PC → PC says away, he's
  actually present
- Suti walks to the kitchen → PC says away (correctly)

The body has the right sensors for this — radar, camera, microphone all
give direct signals of human presence in the room. The PC stays as a
secondary fallback signal on prana's side.

## Endpoint

```
GET http://127.0.0.1:8765/presence
```

Same host:port as `/utter`. Idempotent, cheap, expected to be polled at
~1Hz worst case (prana caches for 5s).

## Response (200 OK)

```json
{
  "present": true,
  "last_seen_ts": 1715403600.0,
  "idle_seconds": 4.2,
  "sources": {
    "radar":      {"detected": true,  "last_seen_ts": 1715403598.0, "confidence": 0.92},
    "camera":     {"detected": false, "last_seen_ts": 1715403520.0, "confidence": 0.71},
    "microphone": {"detected": true,  "last_seen_ts": 1715403595.0, "confidence": 0.55}
  }
}
```

### Fields

| Field | Type | Meaning |
|---|---|---|
| `present` | bool | Fused decision. True if any sensor with sufficient confidence says human-present. |
| `last_seen_ts` | float | Unix epoch (UTC) of the most recent positive detection across any source. |
| `idle_seconds` | float | Seconds since `last_seen_ts`. Convenience for callers. |
| `sources.<name>.detected` | bool | Per-sensor latest reading. |
| `sources.<name>.last_seen_ts` | float | Per-sensor last positive detection. |
| `sources.<name>.confidence` | float | 0.0-1.0, sensor-defined. |

Source names are open-ended; deha can add `pir`, `wifi_csi`, etc. as
sensors come online. prana only reads `present`; the breakdown is for
diagnostics and future fusion work.

## Fusion logic (deha's call, not prana's)

How `present` gets computed from sources is deha's responsibility, but
some reasonable defaults to start with:

```
present = (radar.detected AND radar.confidence > 0.5)
          OR (camera.detected AND camera.confidence > 0.6)
          OR (microphone.detected AND microphone.confidence > 0.7
              AND microphone.last_seen_ts within 30s)
```

Microphone confidence threshold is higher because background sounds
(HVAC, traffic) can false-positive. Camera threshold is mid because
modern detectors are reliable. Radar is the strongest signal — works in
the dark, through soft furnishings, no false-positive on furniture.

## Privacy notes

- **Radar (mmWave)**: no identifying data, just occupancy. No privacy
  concern.
- **Camera**: should run *detection only* — never store frames, never
  transmit images. The detector's output is a single boolean per frame.
  Even local logs should redact any frame buffer.
- **Microphone**: same — voice activity detection only, no recording,
  no transcription on the body.

These should be enforced in deha's implementation. Any code path that
opens a frame or audio buffer for non-detection purposes should require
explicit opt-in.

## Failure modes

- **Endpoint not implemented yet** (current state): prana receives
  connection refused → falls back to PC input.
- **Sensor outage**: deha should still respond 200 with the working
  sensors. Set the failed source to `{"detected": false,
  "last_seen_ts": 0, "confidence": 0.0}` rather than omitting the key
  or returning 500.
- **All sensors quiet**: respond `{"present": false, ...}` after
  ~30s of no detection. Don't synthesize fake activity.
- **Brief gaps**: a 1-2s detection gap shouldn't flip `present`
  to false. Apply hysteresis on the deha side; prana doesn't smooth.

## Caching on prana's side

prana caches the response for 5 seconds (configurable via
`PRANA_PRESENCE_CACHE_S`). So deha sees ~1 request per 5s in steady
state, plus bursts when the heartbeat fires. Optimize accordingly —
serving from in-memory state is fine, no need for a DB read on every
request.

## Implementation pointers (suggestions, not requirements)

- `src/deha/sensors/presence.py` — fuses sensor inputs, exposes
  `current()` returning the dict above.
- The HTTP handler in deha's brain server adds a `GET /presence` route
  that calls `current()` and serializes.
- `tests/contracts/test_presence.py` — round-trip with prana's expected
  schema. The contract here IS the test fixture.

## Versioning

If the schema changes, bump a `version` field in the response and keep
the old shape readable for one release. prana handles missing keys
gracefully (treats as `False` / `0`), so additive changes are safe.
