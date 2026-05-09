"""Voice bridge for narada embodiment.

Talks the ESPHome native API to the BOX-3 firmware. Runs STT (faster-whisper),
brain (claude -p with multi-turn resume), TTS (Piper), and Silero VAD on the
laptop. The device handles wake word (mWW) and audio I/O.
"""
