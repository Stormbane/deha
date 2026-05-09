"""Configure HA Assist pipeline to use Wyoming Whisper + Piper.

Updates the default 'Home Assistant' pipeline so the BOX-3's voice
assistant uses our local STT/TTS containers. Idempotent.
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp


HA_URL = "ws://localhost:8123/api/websocket"


async def main(token: str) -> None:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(HA_URL) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            r = await ws.receive_json()
            assert r["type"] == "auth_ok", r

            mid = 0

            async def call(msg):
                nonlocal mid
                mid += 1
                msg["id"] = mid
                await ws.send_json(msg)
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == mid:
                        return r

            r = await call({"type": "assist_pipeline/pipeline/list"})
            pipelines = r["result"]["pipelines"]
            preferred = r["result"]["preferred_pipeline"]
            target = next(p for p in pipelines if p["id"] == preferred)
            print(f"updating pipeline: {target['name']} ({target['id']})")

            # Update fields. Keep conversation engine as built-in HA for
            # phase 1 — we'll swap in Narada in phase 2.
            update = {
                "type": "assist_pipeline/pipeline/update",
                "pipeline_id": target["id"],
                "name": target["name"],
                "language": "en",
                "conversation_engine": "conversation.narada",
                "conversation_language": "en",
                "stt_engine": "stt.faster_whisper",
                "stt_language": "en",
                "tts_engine": "tts.piper",
                "tts_language": "en_US",
                "tts_voice": "en_US-libritts_r-medium",
                "wake_word_entity": None,
                "wake_word_id": None,
                "prefer_local_intents": True,
            }
            r = await call(update)
            print("update result:", json.dumps(r, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
