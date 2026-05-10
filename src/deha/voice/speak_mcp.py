"""narada-speak MCP server.

Exposes a `speak` tool over MCP stdio. Any Claude Code session, agent,
or hermes integration that registers this server can ask Narada to say
something — which routes through deha's /utter endpoint, into the
UtteranceQueue, and out through the BOX-3.

Run via stdio:

    python -m deha.voice.speak_mcp

Configure in ~/.claude/settings.json or any project-level .mcp.json:

    {
      "mcpServers": {
        "narada-speak": {
          "command": "python",
          "args": ["-m", "deha.voice.speak_mcp"],
          "env": {
            "DEHA_BRAIN_URL": "http://localhost:8765"
          }
        }
      }
    }

Tools:
  - speak(text, source?, priority?) — queue an utterance for the BOX-3
  - speak_status() — return queue depth and brain_server health
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


SERVER_INFO = {
    "name": "narada-speak",
    "version": "0.1.0",
}

CAPABILITIES = {
    "tools": {},
}

DEFAULT_BRAIN_URL = os.environ.get("DEHA_BRAIN_URL", "http://localhost:8765")


TOOLS = [
    {
        "name": "speak",
        "description": (
            "Make Narada say something out loud through the ESP32 BOX-3. "
            "Routes through deha's voice mediator: synthesizes via Kokoro, "
            "plays via the BOX-3's media_player. Returns a request id "
            "and the resulting queue depth. Returns immediately — actual "
            "playback happens asynchronously."
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
                        "Who is asking Narada to speak. Free-form, used "
                        "for logging and (eventually) policy. Examples: "
                        "'claude-code', 'prana-checkin', "
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
        "name": "speak_status",
        "description": (
            "Return current state of the speak pipeline: brain_server "
            "reachability, claude session liveness, current utterance "
            "queue depth."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── HTTP helpers ─────────────────────────────────────────────────────


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


# ── Tool implementations ─────────────────────────────────────────────


def handle_speak(arguments: dict) -> str:
    text = (arguments.get("text") or "").strip()
    if not text:
        return "error: text is required"
    source = (arguments.get("source") or "claude-code").strip()
    priority = int(arguments.get("priority") or 1)
    body = {"text": text, "source": source, "priority": priority}
    try:
        result = _post_json(f"{DEFAULT_BRAIN_URL}/utter", body)
    except urllib.error.URLError as exc:
        return (
            f"error: deha brain server at {DEFAULT_BRAIN_URL} "
            f"unreachable ({exc.reason}). Is it running?"
        )
    if not result.get("ok"):
        return f"error from deha: {result.get('error', 'unknown')}"
    return (
        f"queued: id={result.get('request_id')} "
        f"queue_depth={result.get('queue_depth')}"
    )


def handle_speak_status() -> str:
    try:
        result = _get_json(f"{DEFAULT_BRAIN_URL}/health")
    except urllib.error.URLError as exc:
        return (
            f"deha brain server at {DEFAULT_BRAIN_URL} unreachable: "
            f"{exc.reason}"
        )
    return json.dumps(result, indent=2)


# ── JSON-RPC plumbing (mirrors smriti.mcp_server pattern) ────────────


def _make_response(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _make_error(id_, code, message):
    return {
        "jsonrpc": "2.0", "id": id_,
        "error": {"code": code, "message": message},
    }


def handle_message(msg: dict) -> dict | None:
    method = msg.get("method", "")
    id_ = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return _make_response(id_, {
            "protocolVersion": "2024-11-05",
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _make_response(id_, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            if name == "speak":
                text = handle_speak(arguments)
            elif name == "speak_status":
                text = handle_speak_status()
            else:
                return _make_error(id_, -32601, f"Unknown tool: {name}")
            return _make_response(id_, {
                "content": [{"type": "text", "text": text}],
            })
        except Exception as exc:
            return _make_response(id_, {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            })

    if method == "ping":
        return _make_response(id_, {})

    if method.startswith("notifications/"):
        return None

    if id_ is not None:
        return _make_error(id_, -32601, f"Method not found: {method}")
    return None


def main() -> None:
    """Run the MCP server over stdio."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
