# Contract: body MCP server

Status: **proposed, not yet implemented**. Companion to the existing
HTTP endpoints (`POST /utter`, `POST /set_face`, etc.). Same handlers,
two exposure protocols.

## Why

Today the body is reachable only via HTTP from same-host callers.
That's fine for prana's heartbeat (it can curl `/utter` locally), but
it doesn't generalize.

The architecture (clarified 2026-05-11+) says: **the body is an
instrument, accessible by any cognition with the MCP config.**

- prana cognition (heartbeat, chat-bridge) calls body tools
- Claude Code sessions on the PC call body tools (so the assistant
  helping with code can also speak through the BOX-3)
- Future wake-word-triggered cognition calls body tools
- ESP32-driven flows don't need this — deha is local to them — but
  same shape works fine

MCP is the standard protocol for tool-using LLMs. Every Claude-family
cognition can consume MCP servers via `--mcp-config`. Exposing the
body as MCP is the simplest way to make it ubiquitously available.

## Scope

The MCP server exposes the same operations as the HTTP endpoints,
named as MCP tools:

| MCP tool name | What it does | HTTP equivalent |
|---|---|---|
| `body.speak` | Speak text aloud via TTS + speakers | `POST /utter` |
| `body.set_face` | Change face/expression on display | `POST /set_face` |
| `body.set_status` | Set the status text on display | `POST /set_status` |
| `body.set_weather` | Push weather state to display | `POST /set_weather` |
| `body.get_presence` | Read presence (radar+camera+mic fusion) | `GET /presence` |
| `body.look` *(future)* | Take a photo, return description | (none yet) |
| `body.listen` *(future)* | Capture N seconds of audio, return STT | (none yet) |
| `body.set_light` *(future)* | Set body lighting / LED ring color | (none yet) |

Embodiment-specific extras (Unitree: `body.walk`, `body.grasp`) live
alongside the common set. Tool names are namespaced under `body.` so
they don't collide with other MCP servers (smriti, computer, etc).

## Implementation notes

- Run alongside the existing HTTP server in `deha-brain` — same
  process, same port range (or a sibling port). MCP uses stdio for
  local invocation; HTTP for remote.
- Both protocols share the same handler functions internally —
  whether you HTTP POST `/utter` or call `body.speak` via MCP, the
  code path is one function.
- Tool descriptions ARE the doc. LLMs see them when deciding to call.
  Be precise about side effects ("makes audible sound from speakers")
  and limits ("max 4000 chars").

## How prana consumes it

prana's claude-p invocations pick up the deha MCP server via
`--mcp-config` pointed at a JSON config like:

```json
{
  "mcpServers": {
    "body": {
      "command": "python",
      "args": ["-m", "deha.mcp.server"],
      "timeout": 30
    }
  }
}
```

After this lands, the heartbeat's `claude -p` and the chat-bridge's
`claude -p` both get `body.speak`, `body.look`, etc. as tools they
can choose to invoke. Whether they use them is up to cognition.

## How Claude Code consumes it

Same shape via `~/.claude.json` — add a `mcpServers.body` entry, every
Claude Code session inherits body tools. This means a Claude Code
session helping Suti with code can call `body.speak` to make Narada
say something through the BOX-3 mid-conversation. Continuity across
session types.

## How HTTP and MCP coexist

For local calls from same-host Python (e.g. `prana.state.router`
calling `body.speak` synchronously), HTTP stays the fastest path —
no subprocess, no MCP framing.

For LLM-driven cognition (claude -p, Claude Code, anything that uses
MCP tools), MCP is the right shape.

Two paths to the same handler. No duplication of business logic.

## How prana wires body MCP into heartbeat and chat-bridge

See `docs/plans/deha-narrowing-2026-05-16.md` §"prana-side wiring" for
the concrete `--mcp-config` pattern, the per-process config layout
(`~/.narada/heartbeat/mcp-config.json` vs. global `~/.claude.json`),
and how this evolves once prana's session service (voice-roadmap §3)
takes over the `claude -p` subprocess lifecycle.

## Implementation pointer

- `deha/src/deha/mcp/server.py` — MCP protocol implementation
- `deha/src/deha/mcp/tools/body.py` — tool definitions; thin wrappers
  around the existing HTTP handlers
- `python -m deha.mcp.server` — stdio entry point

## Versioning

Tool descriptions are the contract. If you change semantics, also
change the name (`body.speak` → `body.speak_v2`) so old MCP clients
fail visibly rather than silently misbehave. Or bump a `version` in
the description and let cognition adapt.

## What this doesn't cover (yet)

- Auth — same-host trust assumed today. When multi-host arrives,
  needs a scope/role model.
- Rate limiting — TTS queue is already serialized in deha; the MCP
  layer just forwards. If LLMs over-call, may need cost caps.
- Streaming output — `body.listen` returning audio chunks rather than
  a single STT result is future work.
