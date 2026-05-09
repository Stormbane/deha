"""Narada brain HTTP server (persistent stream-json sessions).

Each HA conversation_id binds to a long-lived claude-cli subprocess
running stream-json I/O. A pre-warmed spare process is kept ready so
the first turn of a fresh conversation lands on a hot subprocess
instead of paying the ~3-4s cold-start.

POST /converse
  body: {"conversation_id": "<ha-conversation-id>", "text": "<user text>"}
  resp: {
    "text": "<reply with sentinels stripped>",
    "continue_conversation": true | false,
    "conversation_id": "<echoed>"
  }

Continue-conversation logic (hybrid):
  - default heuristic: reply ends with "?" -> continue=true
  - <end-turn> sentinel at end of reply -> force continue=false
  - <continue> sentinel at end of reply -> force continue=true
  - sentinels stripped from spoken text in either case

Markdown that Piper would pronounce literally (asterisks, underscores,
backticks, hashes) is stripped before TTS.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path

from aiohttp import web

from .claude_stream import ClaudeStreamSession
from .kokoro_tts import KokoroTTS
from .wyoming_tts import serve_wyoming_tts


END_TURN = "<end-turn>"
CONTINUE = "<continue>"

_MD_STRIP_RE = re.compile(r"[*_`#]+")


def clean_for_tts(text: str) -> str:
    return re.sub(r"\s+", " ", _MD_STRIP_RE.sub("", text)).strip()


def parse_continue(reply: str) -> tuple[str, bool]:
    text = reply.strip()
    if text.endswith(END_TURN):
        return text[: -len(END_TURN)].rstrip(), False
    if text.endswith(CONTINUE):
        return text[: -len(CONTINUE)].rstrip(), True
    return text, text.endswith("?")


class StreamPool:
    """One ever-living ClaudeStreamSession across all HA conversations.

    Continuity-over-isolation: voice is a single ongoing conversation
    with Narada from the user's perspective, not a fresh dialog every
    time HA mints a new conversation_id. We keep one persistent session
    forever (or until restart), so Narada accumulates context across
    wake events. The HA conversation_id is ignored for routing.

    Tradeoff: context grows without bound. For voice turns (short user
    text, short replies) on Sonnet 4.6's 200K context, this is fine for
    thousands of turns before any compaction concern.
    """

    def __init__(self, system_prompt: str, model: str):
        self._system_prompt = system_prompt
        self._model = model
        self._session: ClaudeStreamSession | None = None
        self._lock = asyncio.Lock()

    async def get(self, cid: str) -> ClaudeStreamSession:
        # cid is logged-only, not used for routing. Single session
        # serves every turn.
        async with self._lock:
            if self._session is None or not self._session.alive:
                self._session = ClaudeStreamSession(
                    self._system_prompt, self._model
                )
                await self._session.start()
                print(f"[pool] (re)spawned single session for cid={cid[:12]}",
                      flush=True)
            return self._session

    async def start(self) -> None:
        # Pre-warm the single session at server startup so the first
        # voice turn lands on a ready process.
        async with self._lock:
            if self._session is None:
                self._session = ClaudeStreamSession(
                    self._system_prompt, self._model
                )
                await self._session.start()
                print("[pool] pre-warmed single session", flush=True)

    async def stop(self) -> None:
        async with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            await session.close()


async def handle_converse(request: web.Request) -> web.Response:
    pool: StreamPool = request.app["pool"]
    body = await request.json()
    cid = body.get("conversation_id") or "default"
    user_text = (body.get("text") or "").strip()
    print(f"[converse] cid={cid[:12]} user={user_text!r}", flush=True)
    if not user_text:
        return web.json_response({
            "text": "",
            "continue_conversation": False,
            "conversation_id": cid,
        })
    t0 = time.monotonic()
    session = await pool.get(cid)
    try:
        raw = await session.send(user_text)
    except Exception as e:
        print(f"[converse] cid={cid[:12]} session error: {e}", flush=True)
        # Drop the dead session so the next turn spawns a fresh one.
        async with pool._lock:
            pool._session = None
        await session.close()
        return web.json_response({
            "text": "",
            "continue_conversation": False,
            "conversation_id": cid,
        })
    text, cont = parse_continue(raw)
    text = clean_for_tts(text)
    if not text:
        cont = False
    dt_ms = int((time.monotonic() - t0) * 1000)
    print(
        f"[converse] cid={cid[:12]} reply={text!r} continue={cont} "
        f"({dt_ms} ms)",
        flush=True,
    )
    return web.json_response({
        "text": text,
        "continue_conversation": cont,
        "conversation_id": cid,
    })


async def handle_health(request: web.Request) -> web.Response:
    pool: StreamPool = request.app["pool"]
    async with pool._lock:
        alive = pool._session is not None and pool._session.alive
    return web.json_response({"ok": True, "session_alive": alive})


async def _on_startup(app: web.Application) -> None:
    await app["pool"].start()


async def _on_cleanup(app: web.Application) -> None:
    await app["pool"].stop()


def make_app(system_prompt: str, model: str) -> web.Application:
    app = web.Application()
    app["pool"] = StreamPool(system_prompt, model)
    app.router.add_post("/converse", handle_converse)
    app.router.add_get("/health", handle_health)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def _default_prompt_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "embodiment" / "voice" / "narada-voice.md"
        if candidate.exists():
            return candidate
    return Path("embodiment/voice/narada-voice.md")


async def _prewarm_kokoro(tts: KokoroTTS) -> None:
    """Force model load + a tiny synth so the first user-facing TTS is hot."""
    t0 = time.monotonic()
    try:
        await tts.synth_chunk("ready")
        dt_ms = int((time.monotonic() - t0) * 1000)
        print(f"[prewarm] Kokoro ready ({dt_ms} ms)", flush=True)
    except Exception as e:
        print(f"[prewarm] Kokoro FAILED: {e}", flush=True)


async def _serve_forever(
    app: web.Application,
    host: str,
    port: int,
    tts: KokoroTTS,
    tts_host: str,
    tts_port: int,
) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    print(f"narada brain HTTP on http://{host}:{port}", flush=True)
    # Prewarm Kokoro in parallel with HTTP serving startup. The
    # StreamPool prewarms its claude-cli session via app["pool"].start()
    # in _on_startup; this covers the TTS half. Both pay their cold
    # starts before the first user turn lands.
    asyncio.create_task(_prewarm_kokoro(tts))
    try:
        await serve_wyoming_tts(tts, tts_host, tts_port)
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Narada brain server (HTTP + Wyoming TTS)")
    parser.add_argument("--prompt-file", type=Path, default=_default_prompt_path())
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--tts-host", default="0.0.0.0",
        help="Bind host for the Wyoming TTS server",
    )
    parser.add_argument(
        "--tts-port", type=int, default=10210,
        help="Bind port for the Wyoming TTS server (HA dials this)",
    )
    parser.add_argument("--voice", default="bm_george")
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.prompt_file.exists():
        raise SystemExit(f"prompt file not found: {args.prompt_file}")
    system_prompt = args.prompt_file.read_text(encoding="utf-8")

    app = make_app(system_prompt, args.model)
    tts = KokoroTTS(voice=args.voice)

    print(f"narada brain server (HTTP + Wyoming TTS)")
    print(f"  HTTP /converse on   {args.host}:{args.port}")
    print(f"  Wyoming TTS on      {args.tts_host}:{args.tts_port}")
    print(f"  voice               {args.voice}")
    print(f"  system prompt       {args.prompt_file}")
    print(f"  model               {args.model}")
    asyncio.run(_serve_forever(
        app, args.host, args.port, tts, args.tts_host, args.tts_port,
    ))


if __name__ == "__main__":
    main()


__all__ = ["StreamPool", "make_app", "parse_continue", "clean_for_tts"]
