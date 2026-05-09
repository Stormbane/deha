# deha_client — API surface

The Python lib prana, svapna, and any other project import to talk to
deha. **Pure HTTP client code.** No body logic, no firmware, no expression
generation.

Lives at `deha/client/` in the deha repo, published as the `deha_client`
package.

## Design rules

- **Stable contract.** Once a method ships, signature stays stable across
  minor versions; new params land as optional kwargs with defaults.
- **Synchronous + async parallels.** Each client class has both `*Client`
  (sync, requests-based) and `Async*Client` (asyncio, aiohttp-based).
  Heartbeat skills typically want sync; voice mediator wants async.
- **No global singletons.** Always pass host/port; default to localhost.
- **Type hints everywhere.** Dataclass returns, never raw dicts.
- **Errors as exceptions.** `DehaError` base; `DehaUnreachable`,
  `DehaTimeout`, `DehaInvalidResponse` subclasses.

## Module layout

```
deha_client/
├── __init__.py        # exports
├── types.py           # Weather, Mood, UtteranceResult, ConversationTurn dataclasses
├── display.py         # DisplayClient / AsyncDisplayClient
├── expression.py      # ExpressionClient / AsyncExpressionClient
├── voice.py           # VoiceClient / AsyncVoiceClient
├── _http.py           # shared HTTP plumbing
└── errors.py          # exception hierarchy
```

## Client classes

### `DisplayClient`

Status text shown on the BOX-3's display.

```python
class DisplayClient:
    def __init__(self, host: str = "localhost", port: int = 8766, timeout: float = 2.0): ...

    def set_status(self, text: str) -> None:
        """Set the status text shown on the display."""

    def show_resting(self) -> None:
        """Switch to the idle/resting animation."""

    def clear(self) -> None:
        """Clear the status display."""
```

### `ExpressionClient`

Mood, weather, face state.

```python
class ExpressionClient:
    def __init__(self, host: str = "localhost", port: int = 8766, timeout: float = 2.0): ...

    def show_desire(self, action: str, topic: str | None) -> None: ...
    def show_judging(self) -> None: ...
    def show_executing(self, topic: str | None) -> None: ...
    def show_result(self, summary: str) -> None: ...
    def show_resting(self) -> None: ...

    def set_mood(self, mood: Mood) -> None: ...
    def set_weather(self, weather: Weather) -> bool:
        """Push current weather to the body's visual layer.
        Returns True on success, False on push failure."""
```

### `VoiceClient`

Heartbeat-initiated speech (queued — never bypasses the mediator).

```python
class VoiceClient:
    def __init__(self, host: str = "localhost", port: int = 8765, timeout: float = 2.0): ...

    def utter(self, text: str, *, priority: int = 0, source: str = "heartbeat") -> UtteranceResult:
        """Append text to the utterance queue. Returns UtteranceResult with
        queue position and ETA. The voice mediator drains when esp32 is
        idle. This call returns immediately; speech happens asynchronously."""

    def is_speaking(self) -> bool:
        """Quick check (reads state.db esp32 slice). For coordination only —
        not strictly authoritative since state can change between read and
        the next event."""

    def cancel_pending(self, source: str | None = None) -> int:
        """Drop pending utterances from the queue. If source is given, drop
        only utterances from that source. Returns number cancelled."""
```

### Async parallels

`AsyncDisplayClient`, `AsyncExpressionClient`, `AsyncVoiceClient` — same
methods, awaitable. Same backing protocol.

## Types

```python
@dataclass
class Weather:
    temperature_c: float
    wind_speed_kmh: float
    wind_direction_deg: float
    precipitation_mm_hr: float
    cloud_cover_pct: float

@dataclass
class Mood:
    name: str   # e.g. "neutral", "thinking", "playful"
    intensity: float = 1.0

@dataclass
class UtteranceResult:
    queue_id: int
    queue_position: int
    estimated_speak_ts: float | None
```

## Error hierarchy

```python
class DehaError(Exception): ...
class DehaUnreachable(DehaError): ...    # connection refused / DNS fail
class DehaTimeout(DehaError): ...
class DehaInvalidResponse(DehaError): ... # malformed JSON / unexpected fields
class DehaServerError(DehaError):         # 5xx from deha
    status_code: int
```

## Backwards-compat with current svapna code

These existing call sites remain identical after migration:

| Existing | After |
|---|---|
| `from svapna.heartbeat.display import DisplayClient` | `from deha_client import DisplayClient` |
| `from svapna.indriyas.karmendriyas.drishti.expression import ExpressionClient` | `from deha_client import ExpressionClient` *(or keep the indriyas path in prana as a thin re-export — see below)* |

**The indriyas-path question**: the karmendriyas/drishti naming is
*Narada's experience of having a body* — semantic value lives in keeping
those names. Recommendation: in prana, keep `prana/indriyas/karmendriyas/drishti/expression.py`
as a 3-line re-export of `deha_client.ExpressionClient`. Names stay; the
shape moves.

## Wire format (for reference, but don't depend on it)

deha exposes JSON over HTTP. Schemas live in `deha/docs/api.md` (TBD).
This client lib is the *only* place that should know the wire format —
callers depend on the Python types, not the JSON shape.
