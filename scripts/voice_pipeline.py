"""Voice bridge CLI entrypoint.

Thin wrapper around deha.voice.bridge — the implementation
lives in the package so it can be imported and tested. See:
  src/svapna/embodiment/voice/

Run:
    pip install -e ".[voice]"
    python scripts/voice_pipeline.py
    python scripts/voice_pipeline.py --streaming-tts
    python scripts/voice_pipeline.py --brain heartbeat

Stop with Ctrl+C (clean disconnect).
"""

from __future__ import annotations

import sys

from deha.voice.bridge import main


if __name__ == "__main__":
    sys.exit(main())
