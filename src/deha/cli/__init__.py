"""deha CLI — device management.

The developer CLI for talking to the physical body:
ping, status, build, deploy, logs. See `python -m deha.cli --help`.

Body subsystems live next to this CLI:

  - drishti (display / face)        → deha.expression
  - proprioception (BodyClient)     → deha.proprioception
  - voice (brain server, TTS)       → deha.voice
  - tvac (touch / weather fetcher)  → currently in svapna; moves to prana
"""
