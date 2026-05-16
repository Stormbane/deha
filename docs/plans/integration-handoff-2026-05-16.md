# deha integration — cross-session handoff

**Date:** 2026-05-16
**Purpose:** Single entry point for any session (deha or prana) picking up
the deha narrowing + body MCP + voice-parity work. Read this first, then
dive into the spec doc relevant to your session.

## The big picture in one paragraph

deha today owns voice cognition (a `claude -p` subprocess lives inside
`brain_server.py`). That violates the architectural boundary clarified
2026-05-11+: deha is the body, prana is cognition. The narrowing pulls
voice cognition out of deha entirely and routes voice transcripts through
prana's singular cognition pipeline — the same pipeline that handles
Signal/Telegram messages today. After the narrowing lands, **one Narada
speaks across all channels**, the body MCP server exposes the body as
tools that any cognition can call, and `/presence` gives every channel
(not just voice) accurate "is Suti here" signal.

## Read order — what to read depending on your session

### If you are picking up deha-side work
Read in this order:
1. **This doc** (you're here) — for the cross-project picture.
2. `docs/plans/deha-narrowing-2026-05-16.md` — what changes architecturally
   and why. Authoritative spec for the narrowing.
3. `docs/contracts/presence.md` — `/presence` endpoint contract (already
   complete, deha-side only).
4. `docs/contracts/mcp-server.md` — body MCP server tool surface (already
   complete, deha-side only).
5. `docs/plans/voice-roadmap-2026-05-16.md` — Tier 1 (done), Tier 2 (next:
   visuals, presence v1, wake word), Tier 3 (the prana session service
   that consumes escalations).

### If you are picking up prana-side work
Read in this order:
1. **This doc** (you're here).
2. `~/.narada/projects/prana/2026/05-16.md` — Stage 5 host migration that
   set up the supervised process tree this design slots into.
3. `prana/docs/plans/unified-mind-2026-05-11.md` — the bus skeleton.
4. `prana/docs/plans/combined-rollout-2026-05-11.md` — where the cross-
   project work fits (Stages 4 and 6).
5. `deha/docs/plans/deha-narrowing-2026-05-16.md` §"Escalation" and
   §"prana-side wiring" — what prana has to build.
6. `deha/docs/plans/voice-roadmap-2026-05-16.md` §3 — Tier 3, the session
   service architecture and phasing.

### If you are Claude Code helping with general coding sessions on the PC
Mostly only `docs/contracts/mcp-server.md` is relevant — once the body
MCP server lands, you get `body.speak` as a tool and can talk through
the BOX-3 mid-coding-session. Nothing else changes for you.

## What's already specced (don't redo)

| Doc | Status | Owns |
|---|---|---|
| `deha/docs/contracts/presence.md` | Complete | `/presence` HTTP endpoint shape, fusion logic, privacy rules, failure modes, caching |
| `deha/docs/contracts/mcp-server.md` | Complete | body MCP tool surface (`body.speak`, `body.set_face`, etc.), HTTP+MCP coexistence |
| `deha/docs/plans/voice-roadmap-2026-05-16.md` | Tier 1 done, Tier 2-3 specced | Voice work tiers; Tier 3 = the prana session service |
| `deha/docs/plans/deha-narrowing-2026-05-16.md` | This session (2026-05-16) | The architectural narrowing — what comes out of deha, escalation contract, prana wiring |
| `prana/docs/plans/unified-mind-2026-05-11.md` | Phases 1A+1C done | Bus skeleton, sense/action/skill buses, reserved event fields |
| `prana/docs/plans/host-orchestrator-2026-05-11.md` | Phases 1-4 done | Supervised process tree; deha-brain wired in but `enabled: false` until ready |
| `prana/docs/plans/combined-rollout-2026-05-11.md` | Stages 1, 2, 3, 5 done | Sequences host + unified-mind work across stages |

## Phasing — the canonical order

Each row is shippable. Rows in the same step can run in parallel across
sessions; later steps depend on earlier ones.

| Step | Repo | What | Depends on |
|---|---|---|---|
| 1a | deha | Implement `/presence` v1. Firmware audit first (which sensors are exposed to HA today), then the endpoint. | nothing |
| 1b | deha | Bootstrap body MCP server. Tools wrap existing HTTP handlers. | nothing |
| 1c | prana | Build session service per voice-roadmap §3 Phase A. `POST /turn` returning NDJSON deltas, single asyncio lock around the long-lived `claude -p --continue`. | nothing |
| 2a | prana | Wire body MCP into heartbeat + chat-bridge via `--mcp-config`. | 1b |
| 2b | prana | Migrate chat-bridge to call session service instead of spawning its own `claude -p` (voice-roadmap §3 Phase B). | 1c |
| 3 | deha | Switch `/converse` to forward to session service. Delete `StreamPool`, `claude_stream.py`. (voice-roadmap §3 Phase C, narrowing fully in place after this.) | 1c |
| 4 | prana | Stage 4 of combined-rollout: presence onto bus (Phase 1B), chat-bridge split (Phase 1D). | 1a, 2b |
| 5 | deha | Wake-word "Narada" model per voice-roadmap Tier 2.3. | ideally 3 — full narrowing in place when new wake-word starts firing real escalations |
| 6 | deha | Visuals — "more Narada" face per voice-roadmap Tier 2.1. | nothing (parallel) |

Steps 1a / 1b / 1c are independent — three sessions in parallel if you
want to push it.

## Design calls already made in this session (2026-05-16)

These were resolved live and are baked into the specs — don't relitigate
unless something material changes:

1. **No local escalation model in v1.** Every voice transcript escalates
   to prana. The slot for a Gemma/Qwen-small gate exists but is not
   implemented. (Suti, 2026-05-16.)
2. **The narrowing assumes Tier 3's prana session service exists.** Don't
   design an interim "spawn `claude -p` per turn from deha" path —
   session service is the receiving end. (Suti, 2026-05-16.)
3. **Same Narada across channels.** No voice-specific Narada vs.
   signal-specific Narada. Channel hint goes in the user message;
   Narada adapts cadence; system prompt is unified. (Implied by
   unified-mind, made explicit in narrowing §"What deha loses".)
4. **HTTP + MCP coexistence stays.** Body MCP doesn't replace HTTP; the
   two protocols share handlers. Same-host Python uses HTTP for speed;
   LLM-driven cognition uses MCP for tool-shape. (mcp-server.md, baked.)

## What this handoff doc is NOT

- Not a replacement for the underlying specs. Each linked doc is
  authoritative for its scope. This doc just sequences them.
- Not a hard timeline. The steps are gating dependencies, not promises
  about session counts.
- Not a place to design new things. New design goes in a new spec doc
  (or updates an existing one) and gets linked from here.

## Open questions still alive

The narrowing spec has three (see `deha-narrowing-2026-05-16.md`
§"Open questions"):
1. Voice-specific system-prompt cadence vs. unified prompt with channel hint.
2. HA's assist UI mid-migration — feature-flag the new `/converse` body
   so rollback is one env-var flip.
3. "Still thinking" reflex during long tool calls — lives in deha or in
   the session service.

None of these block step 1a / 1b / 1c. Resolve them as the relevant
session reaches them.

## Quick reference — file paths

```
deha/
  docs/
    contracts/
      presence.md                          — /presence endpoint
      mcp-server.md                        — body MCP tool surface
    plans/
      voice-roadmap-2026-05-16.md          — voice tiers
      deha-narrowing-2026-05-16.md         — architectural narrowing
      integration-handoff-2026-05-16.md    — THIS DOC
  src/deha/
    voice/
      brain_server.py:84                   — StreamPool (to be deleted)
      brain_server.py:143                  — handle_converse (to be rewritten)
      claude_stream.py                     — entire file (to be deleted)
    mcp/                                   — TO CREATE
      server.py                            — stdio MCP entry
      tools/body.py                        — tool definitions

prana/
  docs/plans/
    unified-mind-2026-05-11.md             — bus skeleton
    host-orchestrator-2026-05-11.md        — supervised process tree
    combined-rollout-2026-05-11.md         — staged rollout
  src/prana/
    session/                               — TO CREATE
      narada_session.py                    — Tier 3 Phase A
    bus/actions/speak.py                   — HTTP-path body speak (exists)
    heartbeat/daemon.py                    — SPEAK / CHECK_IN already use bus
  scripts/
    narada_chat_bridge.py                  — to migrate to session service

~/.narada/
  host/components.yaml                     — deha-brain wired in, enabled:false
  heartbeat/mcp-config.json                — TO CREATE (body + smriti MCP)
  projects/prana/2026/05-16.md             — Stage 5 migration journal
```
