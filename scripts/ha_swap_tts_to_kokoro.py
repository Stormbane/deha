"""Swap HA's TTS from wyoming-piper to brain_server's in-process Kokoro.

Preconditions:
  - brain_server is already running and serving the Wyoming TTS protocol
    on host.docker.internal:<port> (default 10210).
  - HA has the existing wyoming-piper config entry (it will be removed).

Steps:
  1. Log into HA (REST + WS).
  2. Find and remove the wyoming config entry titled "piper".
  3. Run the wyoming config flow with host=host.docker.internal, port=10210.
  4. Wait for the new TTS entity to register.
  5. Update the active Assist pipeline to point at the new TTS entity.

Idempotent: safe to re-run. If piper is already gone and kokoro is wired,
exits cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import aiohttp


HA_URL = "http://localhost:8123"
WS_URL = "ws://localhost:8123/api/websocket"
USERNAME = os.environ.get("NARADA_HA_USER") or "narada"
PASSWORD = os.environ["NARADA_HA_PASS"]
CLIENT_ID = HA_URL + "/"

KOKORO_HOST = "host.docker.internal"
KOKORO_PORT = 10210


async def login(s: aiohttp.ClientSession) -> str:
    async with s.post(
        f"{HA_URL}/auth/login_flow",
        json={
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT_ID,
        },
    ) as r:
        r.raise_for_status()
        flow = await r.json()
    flow_id = flow["flow_id"]

    async with s.post(
        f"{HA_URL}/auth/login_flow/{flow_id}",
        json={"client_id": CLIENT_ID, "username": USERNAME, "password": PASSWORD},
    ) as r:
        r.raise_for_status()
        result = await r.json()
    if result.get("type") != "create_entry":
        raise RuntimeError(f"login failed: {result}")
    code = result["result"]

    async with s.post(
        f"{HA_URL}/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
        },
    ) as r:
        r.raise_for_status()
        return (await r.json())["access_token"]


async def list_entries(s: aiohttp.ClientSession, headers: dict) -> list[dict]:
    async with s.get(
        f"{HA_URL}/api/config/config_entries/entry", headers=headers
    ) as r:
        r.raise_for_status()
        return await r.json()


async def remove_entry(
    s: aiohttp.ClientSession, headers: dict, entry_id: str
) -> None:
    async with s.delete(
        f"{HA_URL}/api/config/config_entries/entry/{entry_id}", headers=headers
    ) as r:
        r.raise_for_status()
        print(f"  removed entry: {entry_id}")


async def add_wyoming(
    s: aiohttp.ClientSession, headers: dict, host: str, port: int
) -> str:
    """Run the wyoming flow with (host, port). Returns the new entry_id."""
    async with s.post(
        f"{HA_URL}/api/config/config_entries/flow",
        headers=headers,
        json={"handler": "wyoming", "show_advanced_options": False},
    ) as r:
        r.raise_for_status()
        flow = await r.json()
    flow_id = flow["flow_id"]
    async with s.post(
        f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
        headers=headers,
        json={"host": host, "port": port},
    ) as r:
        r.raise_for_status()
        result = await r.json()
    if result.get("type") != "create_entry":
        raise RuntimeError(f"wyoming flow did not finalize: {result}")
    return result["result"]["entry_id"]


async def find_kokoro_tts_entity(
    s: aiohttp.ClientSession, headers: dict, entry_id: str, timeout_s: float = 15.0
) -> str:
    """Poll the entity registry until the wyoming entry's TTS entity appears."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        async with s.get(f"{HA_URL}/api/states", headers=headers) as r:
            r.raise_for_status()
            states = await r.json()
        for st in states:
            ent = st.get("entity_id", "")
            if ent.startswith("tts.") and "kokoro" in ent.lower():
                return ent
        # Also check via entity registry — the friendly state may not be
        # populated yet. We accept any tts.* entity that did not exist
        # before this flow (caller can pass that in if needed).
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(
                f"no tts.*kokoro* entity registered within {timeout_s}s "
                f"after adding wyoming entry {entry_id}"
            )
        await asyncio.sleep(0.5)


async def update_pipeline_tts(token: str, tts_entity: str) -> None:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            assert (await ws.receive_json())["type"] == "auth_ok"

            mid = 0

            async def call(msg: dict) -> dict:
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

            update: dict[str, Any] = {
                "type": "assist_pipeline/pipeline/update",
                "pipeline_id": target["id"],
                "name": target["name"],
                "language": target.get("language") or "en",
                "conversation_engine": target.get("conversation_engine"),
                "conversation_language": target.get("conversation_language") or "en",
                "stt_engine": target.get("stt_engine"),
                "stt_language": target.get("stt_language") or "en",
                "tts_engine": tts_entity,
                "tts_language": "en",
                "tts_voice": None,
                "wake_word_entity": target.get("wake_word_entity"),
                "wake_word_id": target.get("wake_word_id"),
                "prefer_local_intents": target.get("prefer_local_intents", True),
            }
            r = await call(update)
            if not r.get("success", False):
                raise RuntimeError(f"pipeline update failed: {r}")
            print(f"  pipeline {target['name']} TTS -> {tts_entity}")


async def run(host: str, port: int) -> None:
    async with aiohttp.ClientSession() as s:
        token = await login(s)
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Sweep stale piper entry (idempotent).
        entries = await list_entries(s, headers)
        for e in entries:
            if e.get("domain") == "wyoming" and (e.get("title") or "").lower() == "piper":
                print(f"removing stale piper entry: {e['entry_id']}")
                await remove_entry(s, headers, e["entry_id"])

        # 2. Make sure the kokoro entry exists.
        entries = await list_entries(s, headers)
        kokoro_entry = next(
            (
                e for e in entries
                if e.get("domain") == "wyoming"
                and (e.get("data") or {}).get("port") == port
                and (e.get("data") or {}).get("host") == host
            ),
            None,
        )
        if kokoro_entry is None:
            print(f"adding wyoming entry for kokoro at {host}:{port}")
            new_entry_id = await add_wyoming(s, headers, host, port)
            print(f"  added entry_id={new_entry_id}")
        else:
            new_entry_id = kokoro_entry["entry_id"]
            print(f"kokoro wyoming entry already exists: {new_entry_id}")

        # 3. Find the TTS entity registered for it.
        tts_entity = await find_kokoro_tts_entity(s, headers, new_entry_id)
        print(f"tts entity: {tts_entity}")

    # 4. Switch the active pipeline.
    await update_pipeline_tts(token, tts_entity)
    print("done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=KOKORO_HOST)
    parser.add_argument("--port", type=int, default=KOKORO_PORT)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
