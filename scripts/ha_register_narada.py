"""Register the Narada custom integration in HA via REST.

Logs in with username/password, runs the config_entries flow for the
narada domain, and submits the brain URL. Idempotent — if a narada
config entry already exists, exits cleanly.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import aiohttp


HA_URL = "http://localhost:8123"
USERNAME = os.environ.get("NARADA_HA_USER") or "narada"
PASSWORD = os.environ["NARADA_HA_PASS"]
CLIENT_ID = HA_URL + "/"
DEFAULT_BRAIN_URL = "http://host.docker.internal:8765"


async def login(s: aiohttp.ClientSession) -> str:
    """Run the HA login_flow, return an access token."""
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
        json={
            "client_id": CLIENT_ID,
            "username": USERNAME,
            "password": PASSWORD,
        },
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
        token_resp = await r.json()
    return token_resp["access_token"]


async def existing_narada_entry(
    s: aiohttp.ClientSession, headers: dict[str, str]
) -> dict[str, Any] | None:
    async with s.get(
        f"{HA_URL}/api/config/config_entries/entry", headers=headers
    ) as r:
        r.raise_for_status()
        entries = await r.json()
    for e in entries:
        if e.get("domain") == "narada":
            return e
    return None


async def register(brain_url: str) -> None:
    async with aiohttp.ClientSession() as s:
        token = await login(s)
        headers = {"Authorization": f"Bearer {token}"}

        existing = await existing_narada_entry(s, headers)
        if existing is not None:
            print(f"narada already registered: entry_id={existing['entry_id']}")
            return

        async with s.post(
            f"{HA_URL}/api/config/config_entries/flow",
            headers=headers,
            json={"handler": "narada", "show_advanced_options": False},
        ) as r:
            r.raise_for_status()
            flow = await r.json()
        flow_id = flow["flow_id"]
        print(f"started flow: {flow_id} (type={flow.get('type')})")

        async with s.post(
            f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
            headers=headers,
            json={"brain_url": brain_url},
        ) as r:
            r.raise_for_status()
            result = await r.json()
        print(f"flow result: type={result.get('type')} title={result.get('title')}")
        if result.get("type") != "create_entry":
            raise RuntimeError(f"flow did not finalize: {result}")


if __name__ == "__main__":
    brain_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BRAIN_URL
    asyncio.run(register(brain_url))
