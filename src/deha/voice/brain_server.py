"""Narada brain HTTP server (persistent stream-json sessions).

Each HA conversation_id binds to a long-lived claude-cli subprocess
running stream-json I/O. A pre-warmed spare process is kept ready so
the first turn of a fresh conversation lands on a hot subprocess
instead of paying the ~3-4s cold-start.

POST /converse
  body:  {"conversation_id": "<ha-conversation-id>", "text": "<user text>"}
  resp:  Content-Type: application/x-ndjson
         streaming JSON-lines, one object per line:

         {"delta": "Morning, "}                         # partial text
         {"delta": "traveler. "}
         {"delta": "What's on your mind?"}
         {"final": {"continue_conversation": true}}     # closing marker

  The reply text is the concatenation of all "delta" values. The "final"
  line carries continue_conversation and arrives last. On error, a
  {"error": "..."} line may appear before "final".

Continue-conversation logic (hybrid):
  - default heuristic: reply ends with "?" -> continue=true
  - <end-turn> sentinel at end of reply -> force continue=false
  - <continue> sentinel at end of reply -> force continue=true
  - sentinels stripped from spoken text in either case

Markdown that Piper would pronounce literally (asterisks, underscores,
backticks, hashes) is stripped before TTS.

Streaming preserves the sentinel-handling behavior by holding back the
last SENTINEL_TAIL_BYTES of unstreamed text (more than the longest
sentinel) until the upstream stream ends, then parsing the trailing
sentinel from the full accumulated text and emitting only the cleaned
remainder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from pathlib import Path

from aiohttp import web

from .claude_stream import ClaudeStreamSession
from .kokoro_tts import KokoroTTS
from .utter import UtteranceQueue, VoiceMediator
from .wyoming_tts import serve_wyoming_tts


END_TURN = "<end-turn>"
CONTINUE = "<continue>"

_MD_STRIP_RE = re.compile(r"[*_`#]+")


def clean_for_tts(text: str) -> str:
    return re.sub(r"\s+", " ", _MD_STRIP_RE.sub("", text)).strip()


def parse_continue(reply: str) -> tuple[str, bool]:
    """Decide whether to keep the voice pipeline listening after this reply.

    Default-open: stay listening unless Narada explicitly ends with
    <end-turn>. This matches natural conversation — replies don't have to
    end in '?' for the other person to keep listening. The ? heuristic
    used to be load-bearing; now <end-turn> is the explicit close.
    Background-noise tolerance when not expecting an answer is a separate
    firmware/pipeline concern, not handled here.
    """
    text = reply.strip()
    if text.endswith(END_TURN):
        return text[: -len(END_TURN)].rstrip(), False
    if text.endswith(CONTINUE):
        return text[: -len(CONTINUE)].rstrip(), True
    return text, True


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


# Hold-back tail for sentinel detection: longest sentinel is "<continue>"
# at 10 chars; pad to 16 to cover any whitespace or partial-encoding
# weirdness. We never emit the last 16 chars of accumulated text until
# the upstream stream ends.
SENTINEL_TAIL_BYTES = 16


async def handle_converse(request: web.Request) -> web.StreamResponse:
    pool: StreamPool = request.app["pool"]
    body = await request.json()
    cid = body.get("conversation_id") or "default"
    user_text = (body.get("text") or "").strip()
    print(f"[converse] cid={cid[:12]} user={user_text!r}", flush=True)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering if any
        },
    )
    await response.prepare(request)

    async def write_line(obj: dict) -> None:
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        await response.write(line)

    if not user_text:
        await write_line({"final": {"continue_conversation": False}})
        await response.write_eof()
        return response

    t0 = time.monotonic()
    session = await pool.get(cid)

    accumulated = ""   # raw text from claude (markdown, possible trailing sentinel)
    emitted_to = 0     # chars of `accumulated` already sent as deltas (raw, pre-clean)
    ttft_ms: int | None = None

    def _strip_md(s: str) -> str:
        return _MD_STRIP_RE.sub("", s)

    try:
        async for chunk in session.stream(user_text):
            if ttft_ms is None:
                ttft_ms = int((time.monotonic() - t0) * 1000)
            accumulated += chunk
            # Emit everything except the trailing SENTINEL_TAIL_BYTES so we
            # can still strip a sentinel that lands at end-of-stream.
            safe_end = len(accumulated) - SENTINEL_TAIL_BYTES
            if safe_end > emitted_to:
                raw_to_emit = accumulated[emitted_to:safe_end]
                cleaned = _strip_md(raw_to_emit)
                if cleaned:
                    await write_line({"delta": cleaned})
                emitted_to = safe_end
    except Exception as e:
        print(f"[converse] cid={cid[:12]} session error: {e}", flush=True)
        # Drop the dead session so the next turn spawns a fresh one.
        async with pool._lock:
            pool._session = None
        await session.close()
        await write_line({"error": str(e)})
        await write_line({"final": {"continue_conversation": False}})
        await response.write_eof()
        return response

    # Stream done. Parse trailing sentinel from full accumulated text.
    final_full, cont = parse_continue(accumulated)
    # Emit any residual that's part of final_full (sentinel stripped) but
    # not yet streamed.
    if len(final_full) > emitted_to:
        residual = final_full[emitted_to:]
        cleaned = _strip_md(residual)
        if cleaned:
            await write_line({"delta": cleaned})

    # Build final TTS-clean text only for the [converse] log line; the
    # actual delta stream above is what HA sees.
    final_text = clean_for_tts(final_full)
    if not final_text:
        cont = False

    dt_ms = int((time.monotonic() - t0) * 1000)
    ttft_str = f"ttft={ttft_ms}ms " if ttft_ms is not None else ""
    print(
        f"[converse] cid={cid[:12]} reply={final_text!r} continue={cont} "
        f"{ttft_str}({dt_ms} ms)",
        flush=True,
    )

    await write_line({"final": {"continue_conversation": cont}})
    await response.write_eof()
    return response


async def handle_utter(request: web.Request) -> web.Response:
    """Queue a one-shot utterance for the BOX-3.

    POST /utter
      body: {
        "text": "<words to speak>",
        "source": "<who is asking>",  # optional, default "anonymous"
        "priority": 1                 # optional, higher = jumps queue
      }
      resp: {
        "ok": true,
        "request_id": "<short id>",
        "queue_depth": <int>
      }

    Returns immediately. The mediator dispatches in the background.
    """
    queue: UtteranceQueue = request.app["utter_queue"]
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response(
            {"ok": False, "error": "text is required"},
            status=400,
        )
    source = (body.get("source") or "anonymous").strip()[:64]
    priority = int(body.get("priority") or 1)
    item = await queue.put(text, source=source, priority=priority)
    print(
        f"[utter] enqueued id={item.request_id} src={source} "
        f"pri={priority} depth={queue.depth} text={text[:80]!r}",
        flush=True,
    )
    return web.json_response({
        "ok": True,
        "request_id": item.request_id,
        "queue_depth": queue.depth,
    })


async def handle_health(request: web.Request) -> web.Response:
    pool: StreamPool = request.app["pool"]
    async with pool._lock:
        alive = pool._session is not None and pool._session.alive
    queue: UtteranceQueue = request.app["utter_queue"]
    return web.json_response({
        "ok": True,
        "session_alive": alive,
        "utter_queue_depth": queue.depth,
    })


async def _on_startup(app: web.Application) -> None:
    await app["pool"].start()


async def _on_cleanup(app: web.Application) -> None:
    await app["pool"].stop()


def make_app(system_prompt: str, model: str) -> web.Application:
    app = web.Application()
    app["pool"] = StreamPool(system_prompt, model)
    app["utter_queue"] = UtteranceQueue()
    app.router.add_post("/converse", handle_converse)
    app.router.add_post("/utter", handle_utter)
    app.router.add_get("/health", handle_health)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def _default_prompt_path() -> Path:
    """Locate the voice system prompt.

    Identity-level file: lives at ~/.narada/voice/narada-voice.md so it
    travels with Narada across project repos. Falls back to the in-repo
    embodiment/voice/ location for migrations or air-gapped checkouts.
    """
    home_path = Path.home() / ".narada" / "voice" / "narada-voice.md"
    if home_path.exists():
        return home_path
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "embodiment" / "voice" / "narada-voice.md"
        if candidate.exists():
            return candidate
    return home_path  # report the canonical location for the missing-file error


async def _prewarm_kokoro(tts: KokoroTTS) -> None:
    """Force model load + a tiny synth so the first user-facing TTS is hot."""
    t0 = time.monotonic()
    try:
        await tts.synth_chunk("ready")
        dt_ms = int((time.monotonic() - t0) * 1000)
        print(f"[prewarm] Kokoro ready ({dt_ms} ms)", flush=True)
    except Exception as e:
        print(f"[prewarm] Kokoro FAILED: {e}", flush=True)


# Claude keepalive: Anthropic's prompt cache has a 5-minute default TTL.
# After a long idle, the next user turn pays full prompt re-processing
# cost — empirically ~5-10 s on the system prompt + accumulated
# conversation history. A small probe every 4 minutes keeps the cache
# warm. Cost: ~250 ms per probe (warm-path round trip), one extra
# user/assistant pair appended to the conversation history per probe.
# Pollution is acceptable for short-window voice sessions; if it starts
# pushing context limits, we'll add periodic compaction.
CLAUDE_KEEPALIVE_INTERVAL_S = 240.0
CLAUDE_KEEPALIVE_PROBE = "ping"


async def _claude_keepalive(pool: "StreamPool") -> None:
    """Periodic small probe to keep Anthropic's prompt cache warm."""
    while True:
        try:
            await asyncio.sleep(CLAUDE_KEEPALIVE_INTERVAL_S)
            t0 = time.monotonic()
            session = await pool.get("__keepalive__")
            try:
                # Use stream() so we can capture TTFT.
                ttft_ms: int | None = None
                async for _ in session.stream(CLAUDE_KEEPALIVE_PROBE):
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - t0) * 1000)
                dt_ms = int((time.monotonic() - t0) * 1000)
                # Always log keepalive: this is the canary for cache health.
                print(
                    f"[claude-keepalive] ttft={ttft_ms}ms total={dt_ms}ms",
                    flush=True,
                )
            except Exception as e:
                print(f"[claude-keepalive] probe failed: {e}", flush=True)
                # Don't tear the session down — handle_converse will if needed.
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[claude-keepalive] loop error: {e}", flush=True)


async def _serve_forever(
    app: web.Application,
    host: str,
    port: int,
    tts: KokoroTTS,
    tts_host: str,
    tts_port: int,
    mediator: VoiceMediator,
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
    # Periodic small claude probe to keep Anthropic's prompt cache warm
    # across long idle periods. See _claude_keepalive comments.
    asyncio.create_task(_claude_keepalive(app["pool"]))
    # Start the utter mediator: drains the utterance queue, synthesizes
    # via Kokoro, plays via BOX-3 media_player. Independent of the HA
    # conversation TTS path.
    await mediator.start()
    try:
        await serve_wyoming_tts(tts, tts_host, tts_port)
    finally:
        await mediator.stop()
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
    parser.add_argument(
        "--voice", default="am_michael:0.5,af_heart:0.5",
        help="Kokoro voice spec. Single voice id like 'am_puck', or a "
             "blend recipe like 'am_michael:0.5,af_heart:0.5'. Weights "
             "are normalized. Default is the audition-winning blend "
             "(am_michael + af_heart, 50/50).",
    )
    parser.add_argument(
        "--tts-device", default="cpu", choices=["auto", "cpu", "cuda"],
        help="ONNX runtime device for Kokoro. Default 'cpu' is the "
             "intentional choice — see docs/plans/voice-performance-2026-05-09.md "
             "for the GPU experiment write-up. Pass 'cuda' to opt back in.",
    )
    parser.add_argument("--model", default="sonnet")
    parser.add_argument(
        "--box-ip", default="192.168.86.35",
        help="ESP32-S3-BOX-3 IP for /utter playback via media_player",
    )
    parser.add_argument(
        "--utter-audio-port", type=int, default=8767,
        help="HTTP port to serve generated WAV files for the BOX-3 to fetch",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.prompt_file.exists():
        raise SystemExit(f"prompt file not found: {args.prompt_file}")
    system_prompt = args.prompt_file.read_text(encoding="utf-8")

    app = make_app(system_prompt, args.model)
    tts = KokoroTTS(voice=args.voice, device=args.tts_device)
    mediator = VoiceMediator(
        tts=tts,
        queue=app["utter_queue"],
        device_ip=args.box_ip,
        audio_port=args.utter_audio_port,
    )

    print(f"narada brain server (HTTP + Wyoming TTS + utter)")
    print(f"  HTTP /converse on   {args.host}:{args.port}")
    print(f"  HTTP /utter on      {args.host}:{args.port}")
    print(f"  Wyoming TTS on      {args.tts_host}:{args.tts_port}")
    print(f"  utter audio on      :{args.utter_audio_port}")
    print(f"  BOX-3 ip            {args.box_ip}")
    print(f"  voice               {args.voice}")
    print(f"  tts device          {args.tts_device}")
    print(f"  system prompt       {args.prompt_file}")
    print(f"  model               {args.model}")
    asyncio.run(_serve_forever(
        app, args.host, args.port, tts, args.tts_host, args.tts_port,
        mediator,
    ))


if __name__ == "__main__":
    main()


__all__ = ["StreamPool", "make_app", "parse_continue", "clean_for_tts"]
