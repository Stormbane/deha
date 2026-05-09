"""Voice bridge entrypoint.

Owns the APIClient lifecycle, signal handling, and module wiring. Run
via `python -m deha.voice.bridge` or the thin
`scripts/voice_pipeline.py` wrapper.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time

from aioesphomeapi import APIClient

from .brain import make_brain
from .config import Config, parse_args
from .embodiment_client import EmbodimentClient
from .http_server import TTSServer, local_ip
from .pipeline import Pipeline
from .stt import WhisperSTT
from .tts import PiperTTS


async def run(cfg: Config) -> int:
    if not cfg.system_prompt.exists():
        print(f"system prompt not found: {cfg.system_prompt}", file=sys.stderr)
        return 1
    system_prompt = cfg.system_prompt.read_text(encoding="utf-8")
    print(f"loaded system prompt ({len(system_prompt)} chars)", flush=True)

    print(f"loading whisper model: {cfg.whisper_model} (float16)...", flush=True)
    t0 = time.monotonic()
    stt = WhisperSTT(cfg.whisper_model)
    print(f"  loaded in {time.monotonic() - t0:.1f}s", flush=True)

    spk = f" speaker_id={cfg.piper_speaker_id}" if cfg.piper_speaker_id is not None else ""
    print(f"loading piper voice: {cfg.piper_voice}{spk} volume={cfg.piper_volume}...", flush=True)
    t0 = time.monotonic()
    tts = PiperTTS(cfg.piper_model_dir, cfg.piper_voice, cfg.piper_speaker_id,
                   cfg.piper_volume, cfg.piper_length_scale)
    print(f"  loaded in {time.monotonic() - t0:.1f}s "
          f"(rate={tts.sample_rate}Hz)", flush=True)

    brain = make_brain(cfg.brain, system_prompt, cfg.conversation_window_s)
    print(f"brain: {cfg.brain}", flush=True)

    tts_host = local_ip(cfg.device_ip)
    tts_server = TTSServer(host=tts_host, port=cfg.tts_port)
    await tts_server.start()
    print(f"tts server: http://{tts_host}:{cfg.tts_port}/tts/", flush=True)

    print(f"connecting to {cfg.device_ip}:{cfg.api_port}...", flush=True)
    client = APIClient(cfg.device_ip, cfg.api_port, password="")
    await client.connect(login=True)

    # Discover media_player entity key. Needed so the bridge can send
    # a delayed stop+play_media "kick" after TTS_END to flush the
    # announcement_pipeline's prefill gate (the queue otherwise sits
    # paused for 8–40s after Decoded audio until something else jolts
    # it — observed 2026-05-04). Test on the next turn proved that the
    # second wake's `media_player.stop` was what unblocked the queue.
    mp_key: int | None = None
    try:
        entities, _services = await client.list_entities_services()
        for e in entities:
            tname = type(e).__name__.lower()
            if "mediaplayer" in tname or "media_player" in tname:
                mp_key = e.key
                print(f"media_player: key={mp_key} name={getattr(e, 'name', '?')!r}", flush=True)
                break
        if mp_key is None:
            print("warn: no media_player entity — kick disabled", flush=True)
    except Exception as e:
        print(f"warn: list_entities failed ({e}) — kick disabled", flush=True)

    embodiment: EmbodimentClient | None = None
    if cfg.embodiment_ip:
        embodiment = EmbodimentClient(cfg.embodiment_ip, cfg.embodiment_port)
        await embodiment.connect()  # Failure inside is logged + tolerated.

    pipeline = Pipeline(
        client=client,
        stt=stt,
        brain=brain,
        tts=tts,
        tts_server=tts_server,
        streaming_tts=cfg.streaming_tts,
        embodiment=embodiment,
        media_player_key=mp_key,
    )

    unsub = client.subscribe_voice_assistant(
        handle_start=pipeline.handle_start,
        handle_stop=pipeline.handle_stop,
        handle_audio=pipeline.handle_audio,
    )
    mode = "streaming" if cfg.streaming_tts else "url"
    print(f"subscribed to voice_assistant (tts mode: {mode}). "
          f"Speak the wake word then your question.", flush=True)

    # Clean shutdown so disconnect actually fires (otherwise the device
    # thinks we're still its api_client_ on next launch and refuses).
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        if not stop_event.is_set():
            print("\nstopping (clean disconnect)...", flush=True)
            stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _request_stop)
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
    except NotImplementedError:
        # Windows asyncio. Falls back to KeyboardInterrupt path.
        pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        print("\nstopping (KeyboardInterrupt)...", flush=True)
    finally:
        try:
            unsub()
        except Exception:
            pass
        try:
            await asyncio.wait_for(client.disconnect(), timeout=2.0)
        except Exception as e:
            print(f"  disconnect: {e}", file=sys.stderr)
        if embodiment is not None:
            await embodiment.close()
        await tts_server.stop()
    return 0


def main() -> int:
    cfg = parse_args()
    try:
        return asyncio.run(run(cfg))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "run"]
