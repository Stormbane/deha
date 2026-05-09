"""deha — the body.

ESP32-S3-BOX-3 firmware, voice brain server, expression engine, and the
client classes other projects (prana, svapna) import to talk to the body.

Top-level surface:

  - DisplayClient (deha.display)        — heartbeat status to the BOX-3
  - ExpressionClient (deha.expression)  — face / mood / weather to the BOX-3
  - BodyClient (deha.proprioception)    — query device state
  - voice/                              — voice brain server + Wyoming TTS
  - cli/                                — `python -m deha.cli` device management

deha does not depend on prana or svapna. The arrows point inward to the
body, not outward from it.
"""

from .esp_client import (  # noqa: F401
    DeviceStatus,
    DisplayPayload,
    EspClient,
    HeartbeatPayload,
)
