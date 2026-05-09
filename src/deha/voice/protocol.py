"""ESPHome voice_assistant event helpers.

Thin layer over aioesphomeapi's VoiceAssistantEventType. The empty-URL
quirk is documented here so the lesson doesn't get rediscovered: in the
device firmware (esphome voice_assistant.cpp:746-748), TTS_END with an
empty `url` early-returns and skips the state transition to
STREAMING_RESPONSE. Always send a URL — even a placeholder like
`stream://narada` — when the device is in legacy `speaker:` mode. In
`media_player:` mode (current target), the URL must be a real fetchable
http(s):// resource.
"""

from __future__ import annotations

from aioesphomeapi import VoiceAssistantEventType as VAEvent

# Mic capture rate. The BOX-3 ES7210 ADC is configured at 16kHz; that
# matches faster-whisper's expected input rate so no resampling is needed.
MIC_RATE = 16000

# Whisper expects mono int16 at MIC_RATE.
MIC_BYTES_PER_SAMPLE = 2

# 32ms VAD frames at MIC_RATE. silero-vad v5+ requires exactly 512
# samples per frame at 16kHz (or 256 at 8kHz) — feeding any other size
# raises in the model. The 30ms frames common in webrtcvad don't fit.
VAD_FRAME_SAMPLES = 512
VAD_FRAME_BYTES = VAD_FRAME_SAMPLES * MIC_BYTES_PER_SAMPLE
VAD_FRAME_S = VAD_FRAME_SAMPLES / MIC_RATE  # 0.032


__all__ = [
    "VAEvent",
    "MIC_RATE",
    "MIC_BYTES_PER_SAMPLE",
    "VAD_FRAME_SAMPLES",
    "VAD_FRAME_BYTES",
]
