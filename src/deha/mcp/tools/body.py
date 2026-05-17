"""Body MCP tool definitions.

Tool surface follows `docs/contracts/mcp-server.md`. Each tool is a thin
wrapper that calls deha's HTTP API on `DEHA_BRAIN_URL` (default
http://localhost:8765). That keeps the brain_server as the single source
of truth — there's exactly one TTS queue, one face state, etc. — and
the MCP layer adds no business logic, only protocol translation.

Tool naming is namespaced `body.*` so it doesn't collide with smriti,
computer, etc. when multiple MCP servers are mounted in the same client.

Tools currently wired to a live HTTP endpoint:
  - body.speak           → POST /utter
  - body.get_presence    → GET  /presence

Tools whose HTTP endpoints don't exist yet (returns a clear error):
  - body.set_face        → planned: POST /set_face
  - body.set_status      → planned: POST /set_status
  - body.set_weather     → planned: POST /set_weather

When the missing HTTP endpoints land in brain_server, the handlers below
become one-liners like body.speak — no change to the tool schemas
(which are the LLM-facing contract).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


DEFAULT_BRAIN_URL = os.environ.get("DEHA_BRAIN_URL", "http://localhost:8765")


# ── Tool schemas (LLM-facing contract — keep descriptions precise) ───

TOOLS = [
    {
        "name": "body.speak",
        "description": (
            "Make Narada say something out loud through the ESP32 BOX-3. "
            "Synthesizes via Kokoro TTS, plays via the BOX-3's media_player. "
            "Returns a request id and queue depth immediately — actual "
            "audible playback happens asynchronously. Max ~4000 chars per "
            "call; longer text gets split into utterances."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The words Narada should say.",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Who is asking. Free-form, used for logging. "
                        "Examples: 'claude-code', 'prana-checkin', "
                        "'signal-relay', 'hermes-agent'."
                    ),
                    "default": "claude-code",
                },
                "priority": {
                    "type": "integer",
                    "description": (
                        "Higher priority items jump the queue. Default "
                        "1. Reserve >5 for urgent interruptions."
                    ),
                    "default": 1,
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "body.get_presence",
        "description": (
            "Read the body's fused presence signal. Returns whether a "
            "human is currently sensed in the room (radar + camera + mic "
            "VAD), plus the per-source breakdown for debugging. Cheap; "
            "served from in-memory sensor state, safe to poll."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "body.set_face",
        "description": (
            "Change the face / expression shown on the BOX-3's display. "
            "NOT YET WIRED — the brain_server HTTP route is pending; "
            "calling this returns an error. The tool schema is stable so "
            "clients can be coded against it now."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "face": {
                    "type": "string",
                    "description": (
                        "Face identifier. See firmware/illustrations/ "
                        "for available faces. Examples: 'idle', "
                        "'listening', 'speaking', 'thinking'."
                    ),
                },
            },
            "required": ["face"],
        },
    },
    {
        "name": "body.set_status",
        "description": (
            "Set the status text shown under the face. NOT YET WIRED — "
            "the brain_server HTTP route is pending."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Short status message. Empty string clears."
                    ),
                },
            },
            "required": ["status"],
        },
    },
    {
        "name": "body.set_weather",
        "description": (
            "Push weather state to the display (used by the BOX-3's "
            "weather visuals). NOT YET WIRED — the brain_server HTTP "
            "route is pending."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "weather_code": {
                    "type": "integer",
                    "description": "Open-Meteo weather code.",
                },
                "temperature_c": {
                    "type": "number",
                    "description": "Temperature in Celsius.",
                },
                "cloud_pct": {
                    "type": "number",
                    "description": "Cloud cover percentage 0-100.",
                },
            },
            "required": ["weather_code"],
        },
    },
]


# ── HTTP helpers (stdlib-only, no third-party deps) ──────────────────


def _post_json(url: str, body: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _unreachable(method: str, url: str, exc: Exception) -> str:
    return (
        f"error: deha brain server at {url} unreachable for "
        f"{method} ({exc}). Is the supervisor running?"
    )


# ── Tool handlers ────────────────────────────────────────────────────


def handle_body_speak(arguments: dict) -> str:
    text = (arguments.get("text") or "").strip()
    if not text:
        return "error: text is required"
    body = {
        "text": text,
        "source": (arguments.get("source") or "claude-code").strip(),
        "priority": int(arguments.get("priority") or 1),
    }
    url = f"{DEFAULT_BRAIN_URL}/utter"
    try:
        result = _post_json(url, body)
    except urllib.error.URLError as exc:
        return _unreachable("POST /utter", url, exc.reason)
    if not result.get("ok"):
        return f"error from deha: {result.get('error', 'unknown')}"
    return (
        f"queued: id={result.get('request_id')} "
        f"queue_depth={result.get('queue_depth')}"
    )


def handle_body_get_presence(arguments: dict) -> str:
    url = f"{DEFAULT_BRAIN_URL}/presence"
    try:
        result = _get_json(url)
    except urllib.error.URLError as exc:
        return _unreachable("GET /presence", url, exc.reason)
    return json.dumps(result, indent=2)


def handle_pending(tool_name: str, route: str) -> str:
    return (
        f"error: {tool_name} not yet wired. The corresponding "
        f"brain_server route ({route}) is pending — see "
        f"docs/contracts/mcp-server.md and the body MCP tool docstring "
        f"in src/deha/mcp/tools/body.py for status."
    )


HANDLERS = {
    "body.speak":        lambda args: handle_body_speak(args),
    "body.get_presence": lambda args: handle_body_get_presence(args),
    "body.set_face":     lambda args: handle_pending("body.set_face", "POST /set_face"),
    "body.set_status":   lambda args: handle_pending("body.set_status", "POST /set_status"),
    "body.set_weather":  lambda args: handle_pending("body.set_weather", "POST /set_weather"),
}
