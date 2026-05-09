"""Per-turn voice pipeline state machine.

Owns one turn: handle_start (from APIClient subscribe_voice_assistant)
through handle_audio (driven by VAD) through STT, brain, TTS, and
eventual TTS_END. Stateless across turns — instance state is reset on
each handle_start.

Phantom-turn gate: turns arrive with wake_word_phrase=None when the
device starts a turn without a real wake-word event (the legacy 8s
phantom case). We acknowledge the protocol so the device doesn't
retry, but discard the transcript without invoking the brain.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING, Callable

from aioesphomeapi import MediaPlayerCommand, VoiceAssistantAudioSettings

from .embodiment_client import EmbodimentClient, Mood, Phoneme
from .phoneme_mapper import PhonemeMapper
from .protocol import VAEvent, MIC_RATE
from .tts import clean_for_voice, resample_pcm_to_16k
from .vad import VadVerdict

if TYPE_CHECKING:
    from aioesphomeapi import APIClient
    from .brain import Brain
    from .http_server import TTSServer
    from .stt import WhisperSTT
    from .tts import PiperTTS
    from .vad import SileroVad


def _default_vad_factory():
    """Lazy import so tests don't require pysilero-vad."""
    from .vad import SileroVad
    return SileroVad()


# Streaming TTS pacing. We send chunks of this duration to give the
# device's announcement_pipeline buffer time to drain. 60ms is the
# sweet spot — small enough to keep latency low, large enough that the
# i2s ring buffer doesn't underrun.
STREAM_CHUNK_MS = 60


# Delay before the kick stop+play_media. The device needs enough time
# to receive TTS_END, run on_tts_end, queue the URL, fetch the WAV,
# and decode the header (so the pipeline is in ANNOUNCING state and a
# stop actually has something to clear). 600ms covers the Decoded-audio
# log line under our observed timing. Too short = stop fires before the
# queue has anything; too long = user hears the gap.
KICK_DELAY_S = 0.6


class Pipeline:
    def __init__(
        self,
        client: APIClient,
        stt: WhisperSTT,
        brain: Brain,
        tts: PiperTTS,
        tts_server: TTSServer,
        streaming_tts: bool,
        vad_factory: Callable[[], "SileroVad"] | None = None,
        embodiment: EmbodimentClient | None = None,
        media_player_key: int | None = None,
    ):
        self.client = client
        self.stt = stt
        self.brain = brain
        self.tts = tts
        self.tts_server = tts_server
        self.streaming_tts = streaming_tts
        self._vad_factory = vad_factory or _default_vad_factory
        # Optional presentation channel — None = no embodiment device.
        # All calls are guarded so a missing/disconnected embodiment
        # never breaks a turn.
        self.embodiment = embodiment
        # media_player entity key for the announcement-queue kick.
        # See _kick_media_player.
        self._media_player_key = media_player_key
        self._phoneme_mapper = PhonemeMapper()

        self._buf: list[bytes] = []
        self._active = False
        self._wake_phrase: str | None = None
        self._gated_phantom = False
        self._vad: "SileroVad | None" = None
        self._closing = False
        self._close_lock = asyncio.Lock()

    async def handle_start(
        self,
        conversation_id: str,
        flags: int,
        audio_settings: VoiceAssistantAudioSettings,
        wake_word_phrase: str | None,
    ) -> int | None:
        self._buf = []
        self._active = True
        self._closing = False
        self._wake_phrase = wake_word_phrase or ""
        self._gated_phantom = wake_word_phrase is None
        self._vad = self._vad_factory()

        if wake_word_phrase:
            print(f"\nwake: {self._wake_phrase!r} (conv={conversation_id})", flush=True)
        else:
            print("\n  phantom turn (no wake_word_phrase) — discarding", flush=True)

        # Embodiment: real wake → look attentive, surface the listening
        # glyph. Phantom wake → don't visually react (the user did
        # nothing). Snap mood so attention reads as immediate.
        if self.embodiment and not self._gated_phantom:
            asyncio.create_task(self._embody_listen())

        await self._event(VAEvent.VOICE_ASSISTANT_RUN_START)
        await self._event(VAEvent.VOICE_ASSISTANT_STT_START)
        await self._event(VAEvent.VOICE_ASSISTANT_STT_VAD_START)
        # Returning a non-None port is required by aioesphomeapi even
        # when audio comes via the API path (a None return makes the
        # device abort with "Server could not be started").
        return 0

    async def handle_audio(self, data: bytes) -> None:
        if not self._active or self._vad is None:
            return
        self._buf.append(data)
        verdict = self._vad.process(data)
        if verdict is not VadVerdict.LISTENING and not self._closing:
            self._closing = True
            asyncio.create_task(self._close(verdict))

    async def handle_stop(self, server_side: bool) -> None:
        """Device-side stop — rare; close path normally driven by VAD."""
        if self._closing:
            return
        self._closing = True
        await self._close(VadVerdict.SILENCE_CLOSED)

    async def _close(self, verdict: VadVerdict) -> None:
        async with self._close_lock:
            if not self._active:
                return
            print(f"  vad: {verdict.value} — closing mic", flush=True)
            await self._event(VAEvent.VOICE_ASSISTANT_STT_VAD_END)
            await self._process()

    async def _process(self) -> None:
        if not self._active:
            return
        self._active = False
        pcm = b"".join(self._buf)
        self._buf = []
        seconds = (len(pcm) // 2) / MIC_RATE
        print(f"  audio: {seconds:.2f}s ({len(pcm)} bytes)", flush=True)

        if seconds < 0.3:
            print("  too short — skipping", flush=True)
            await self._end_run()
            return

        # STT
        t0 = time.monotonic()
        text = await self.stt.transcribe(pcm)
        print(f"  stt: {text!r} ({time.monotonic() - t0:.1f}s)", flush=True)
        await self._event(VAEvent.VOICE_ASSISTANT_STT_END, {"text": text})
        if not text:
            await self._end_run()
            return

        # Phantom-turn gate.
        if self._gated_phantom:
            print("  phantom turn — discarding stt, no brain call", flush=True)
            await self._end_run()
            return

        # Brain
        # Embodiment: thinking state — focused mood, glance down (the
        # face we'll commission later will show contemplation here).
        if self.embodiment:
            asyncio.create_task(self._embody_think())
        t0 = time.monotonic()
        await self._event(VAEvent.VOICE_ASSISTANT_INTENT_START)
        try:
            reply = await self.brain.respond(text)
        except Exception as e:
            print(f"  brain error: {e}", file=sys.stderr)
            reply = ""
        reply = clean_for_voice(reply)
        print(f"  brain: {reply!r} ({time.monotonic() - t0:.1f}s)", flush=True)
        await self._event(VAEvent.VOICE_ASSISTANT_INTENT_END,
                          {"conversation_id": "narada"})
        if not reply:
            await self._end_run()
            return

        # TTS
        t0 = time.monotonic()
        await self._event(VAEvent.VOICE_ASSISTANT_TTS_START, {"text": reply})
        if self.streaming_tts:
            await self._tts_streaming(reply)
        else:
            await self._tts_full(reply)
        print(f"  tts: done ({time.monotonic() - t0:.1f}s)", flush=True)
        await self._end_run()

    async def _tts_full(self, reply: str) -> None:
        """Synthesize one WAV, serve it, send TTS_END {url}."""
        wav_bytes = await self.tts.synthesize(reply)
        url = self.tts_server.write(wav_bytes, ext=".wav")
        # Embodiment: in URL playback we don't get per-chunk PCM at
        # the bridge, so we can only flag start/stop. The mouth will
        # sit in PH_REST while the device plays the URL — the bridge
        # has no signal to drive phonemes here. Improving this would
        # mean intercepting media_player playback events from the
        # device, which is a Phase 3 problem.
        if self.embodiment:
            asyncio.create_task(self.embodiment.set_speaking(True))
        await self._event(VAEvent.VOICE_ASSISTANT_TTS_END, {"url": url})
        # Kick: the device's announcement_pipeline reliably sits paused
        # for 8–40s after Decoded audio until something jolts the state
        # machine. The "second wake fires media_player.stop on_start"
        # pattern (observed 2026-05-04) flushes the queue. Replicate it
        # from the bridge: stop+re-play_media after a short delay.
        if self._media_player_key is not None:
            asyncio.create_task(self._kick_media_player(url))
        if self.embodiment:
            # Estimate playback duration from WAV length and schedule
            # speaking=False afterwards. Approximate (WAV header is
            # 44 bytes, frame = 2 bytes int16 mono).
            seconds = max(0.5, (len(wav_bytes) - 44) / 2 / 22050)
            asyncio.create_task(self._embody_speak_off_after(seconds))

    async def _embody_speak_off_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.embodiment:
            await self.embodiment.set_speaking(False)

    async def _kick_media_player(self, url: str) -> None:
        """Force the announcement queue to advance after TTS_END.

        Sequence: wait KICK_DELAY_S so the device has fetched + decoded
        the WAV, then send STOP (clears the deque + drops the paused
        decoder), then PLAY_MEDIA with the same URL (re-queues — this
        time the state machine starts cleanly because the prior stuck
        item is gone).
        """
        try:
            await asyncio.sleep(KICK_DELAY_S)
            self.client.media_player_command(
                self._media_player_key, command=MediaPlayerCommand.STOP
            )
            await asyncio.sleep(0.05)
            self.client.media_player_command(
                self._media_player_key, media_url=url, announcement=True
            )
            print("  kick: stop+play_media sent", flush=True)
        except Exception as e:
            print(f"  kick failed: {e}", file=sys.stderr)

    async def _tts_streaming(self, reply: str) -> None:
        """Synthesize sentence-by-sentence, stream PCM via API audio.

        Each sentence becomes one or more `send_voice_assistant_audio`
        chunks; we resample to 16kHz mono so the device's announcement
        pipeline takes a uniform format regardless of voice rate.
        """
        await self._event(VAEvent.VOICE_ASSISTANT_TTS_STREAM_START)
        # Embodiment: speaking on, mouth begins driving from chunks.
        last_phoneme = None
        if self.embodiment:
            self._phoneme_mapper.reset()
            asyncio.create_task(self.embodiment.set_speaking(True))
        src_rate = self.tts.sample_rate
        chunk_samples_16k = int(16000 * STREAM_CHUNK_MS / 1000)
        chunk_bytes_16k = chunk_samples_16k * 2
        async for sentence_pcm in self.tts.synthesize_streaming(reply):
            pcm_16k = await asyncio.to_thread(
                resample_pcm_to_16k, sentence_pcm, src_rate
            )
            for i in range(0, len(pcm_16k), chunk_bytes_16k):
                chunk = pcm_16k[i:i + chunk_bytes_16k]
                self.client.send_voice_assistant_audio(chunk)
                if self.embodiment:
                    ph = self._phoneme_mapper.map_chunk(chunk)
                    # Only push when the phoneme actually changes —
                    # holding a vowel shouldn't re-blit the mouth at
                    # 16 Hz against a placeholder palette.
                    if ph != last_phoneme:
                        last_phoneme = ph
                        asyncio.create_task(self.embodiment.set_phoneme(ph))
                await asyncio.sleep(STREAM_CHUNK_MS / 1000 * 0.95)
        if self.embodiment:
            asyncio.create_task(self.embodiment.set_speaking(False))
        await self._event(VAEvent.VOICE_ASSISTANT_TTS_STREAM_END)
        # TTS_END requires a non-empty URL for state transition. The
        # placeholder is fine here — playback already happened via the
        # streamed PCM path; the URL would only be fetched as a fallback.
        await self._event(VAEvent.VOICE_ASSISTANT_TTS_END,
                          {"url": "stream://narada"})

    async def _end_run(self) -> None:
        # Embodiment: settle back to neutral after the turn ends.
        if self.embodiment:
            asyncio.create_task(self._embody_settle())
        await self._event(VAEvent.VOICE_ASSISTANT_RUN_END)

    async def _embody_listen(self) -> None:
        if not self.embodiment:
            return
        # Snap to curious so attention is immediate, look slightly up,
        # show the listening glyph (top-right) as a visible cue that
        # we're capturing audio.
        await self.embodiment.set_mood(Mood.CURIOUS, snap=True)
        await self.embodiment.set_gaze(0.0, -0.3)
        await self.embodiment.set_glyph(True, 280, 30)

    async def _embody_think(self) -> None:
        if not self.embodiment:
            return
        # STT is done, brain is running. Mood becomes focused (smooth
        # transition through neutral if currently curious). Gaze drops
        # slightly — looking inward. Glyph stays visible.
        await self.embodiment.set_mood(Mood.FOCUSED)
        await self.embodiment.set_gaze(0.0, 0.2)

    async def _embody_settle(self) -> None:
        if not self.embodiment:
            return
        # Turn over: speaking off (idempotent), mood ease back to
        # neutral, gaze recenter, glyph off.
        await self.embodiment.set_speaking(False)
        await self.embodiment.set_phoneme(Phoneme.REST)
        await self.embodiment.set_mood(Mood.NEUTRAL)
        await self.embodiment.set_gaze(0.0, 0.0)
        await self.embodiment.set_glyph(False)

    async def _event(self, event_type: VAEvent,
                     data: dict[str, str] | None = None) -> None:
        try:
            self.client.send_voice_assistant_event(event_type, data or {})
        except Exception as e:
            print(f"  event {event_type.name} failed: {e}", file=sys.stderr)


__all__ = ["Pipeline"]
