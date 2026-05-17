"""Body MCP server (stdio).

Exposes Narada's body as MCP tools over JSON-RPC on stdin/stdout. Spawned
on demand by MCP clients (Claude Code, prana's session service, hermes,
etc.) via:

    python -m deha.mcp.server

Configure in `~/.claude.json` (or any project-level `.mcp.json`):

    {
      "mcpServers": {
        "body": {
          "command": "python",
          "args": ["-m", "deha.mcp.server"],
          "env": {
            "DEHA_BRAIN_URL": "http://localhost:8765"
          }
        }
      }
    }

Once mounted, any Claude Code session can call `body.speak`,
`body.get_presence`, etc. as tools. See `src/deha/mcp/tools/body.py` for
the live tool surface and which tools are HTTP-wired vs pending.

The plumbing here is hand-rolled JSON-RPC (no third-party SDK) following
the same pattern as `deha.voice.speak_mcp` and `smriti.mcp_server`. That
keeps the body MCP runnable from a fresh venv with just stdlib.
"""

from __future__ import annotations

import json
import sys

from .tools.body import HANDLERS, TOOLS


SERVER_INFO = {
    "name": "narada-body",
    "version": "0.1.0",
}

CAPABILITIES = {
    "tools": {},
}


# ── JSON-RPC plumbing ────────────────────────────────────────────────


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
        handler = HANDLERS.get(name)
        if handler is None:
            return _make_error(id_, -32601, f"Unknown tool: {name}")
        try:
            text = handler(arguments)
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
