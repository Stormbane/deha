"""Capture laptop mic, stream partial STT transcripts to Narada's display.

Uses sounddevice for capture and faster-whisper for streaming STT. Every
transcribe-tick the latest sliding window of audio is decoded and the
result is pushed to the device's set_stt_transcript API service. On
silence (RMS below threshold for SILENCE_S seconds) the current utterance
is finalized and clear_listening is called so the device wipes its band.

Run:
    python scripts/listen.py
    python scripts/listen.py --model small.en --device-ip 192.168.86.35

Stop with Ctrl+C. The transcript on the device clears on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import re
import sys
import time
import unicodedata

import numpy as np


def clean_for_voice(text: str) -> str:
    """Strip emoji and other symbols that the on-device font can't render
    and that edge-tts pronounces literally. Keep letters, digits, common
    punctuation, whitespace."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        # L=letter, N=number, Z=separator, P=punct, common math/currency.
        if cat[0] in ("L", "N", "Z") or cat in ("Pd", "Po", "Pi", "Pf", "Ps", "Pe", "Pc", "Sc"):
            out.append(ch)
        elif ch in "\n\t":
            out.append(" ")
    cleaned = "".join(out)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

DEFAULT_DEVICE_IP = "192.168.86.35"
DEFAULT_API_PORT = 6053
DEFAULT_MIC_PORT = 6061              # ESP32 audio TCP sender (cycle 4.2)
SAMPLE_RATE = 16000
BLOCK_MS = 250                       # mic callback chunk size
WINDOW_S = 15.0                      # sliding STT window (seconds of audio)
TRANSCRIBE_EVERY_S = 0.5             # how often to re-decode
SILENCE_RMS = 0.004                  # below this is silence (ESP32 mic on BOX-3 runs ~5x quieter than laptop mic)
RMS_TAIL_BLOCKS = 2                  # RMS averages last N×BLOCK_MS for onset speed
DEFAULT_SILENCE_S = 2.5              # default end-of-utterance silence threshold
DEFAULT_HOLD_S = 5.0                 # default transcript display hold after stop
DEFAULT_BRAIN_DELAY_S = 3.0          # default delay before Narada responds
DEFAULT_TTS_VOICE = "en-IN-PrabhatNeural"  # Narada's voice (Indian English male)
SYSTEM_PROMPT_PATH = "embodiment/voice/narada-voice.md"
WHISPER_PROMPT = "Hey Narada."  # minimal hint; longer prompts cause hallucinations
# Whisper mishears "Narada" frequently; tolerate common slop variants.
WAKE_WORDS = (
    "narada", "naroda", "norada", "narrada", "naradha",
    "the date", "this is the date",  # base.en mishear pattern observed
    "narrator", "noruda", "norodha", "norodom",
)


class DeviceLink:
    """Wraps the ESPHome API client with reconnect-on-failure semantics so
    OTA flashes / WiFi blips don't permanently break the push loop."""

    def __init__(self, ip: str, port: int = DEFAULT_API_PORT):
        self.ip = ip
        self.port = port
        self.client = None
        self.set_svc = None
        self.clear_svc = None

    async def connect(self) -> bool:
        from aioesphomeapi import APIClient

        try:
            self.client = APIClient(self.ip, self.port, password="")
            await self.client.connect(login=True)
            _entities, services = await self.client.list_entities_services()
            wanted = {
                "set_stt_transcript": "set_svc",
                "clear_listening": "clear_svc",
                "set_alien_speech": "speech_svc",
                "set_alien_activity": "activity_svc",
            }
            for svc in services:
                if svc.name in wanted:
                    setattr(self, wanted[svc.name], svc)
            for service_name, attr in wanted.items():
                if getattr(self, attr, None) is None:
                    raise RuntimeError(f"device missing service: {service_name}")
            return True
        except Exception as e:
            print(f"  connect failed: {e}", file=sys.stderr)
            self.client = None
            return False

    async def call(self, svc_attr: str, args: dict) -> None:
        for attempt in range(2):
            if self.client is None:
                if not await self.connect():
                    return
            svc = getattr(self, svc_attr)
            try:
                await self.client.execute_service(svc, args)
                return
            except Exception as e:
                print(f"  push failed (will reconnect): {e}",
                      file=sys.stderr)
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None

    async def push_transcript(self, phrase: str) -> None:
        await self.call("set_svc", {"phrase": phrase})

    async def push_clear(self) -> None:
        await self.call("clear_svc", {})

    async def set_speech(self, phrase: str) -> None:
        await self.call("speech_svc", {"phrase": phrase})

    async def set_activity(self, name: str) -> None:
        await self.call("activity_svc", {"name": name})

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Brain protocol — pluggable. ClaudeCodeBrain is the v1 impl: shells out to
# `claude -p` so no API key is needed (uses the user's subscription).
# ---------------------------------------------------------------------------
class ClaudeCodeBrain:
    """Minimal v1: one-shot `claude -p` per turn. Stateless across turns
    (memory protocol arrives in voice-step-4)."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    async def respond(self, user_text: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--system-prompt", self.system_prompt,
            "--disallowedTools",
            "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=user_text.encode("utf-8"))
        text = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            print(f"  claude returned {proc.returncode}: "
                  f"{stderr.decode('utf-8', errors='replace')[:200]}",
                  file=sys.stderr)
        return text


# ---------------------------------------------------------------------------
# TTS — edge-tts MP3 → PyAV decode → sounddevice playback.
# ---------------------------------------------------------------------------
async def speak(text: str, voice: str = DEFAULT_TTS_VOICE) -> None:
    """Synthesize `text` with edge-tts and play it through default speakers.
    Blocks until playback completes."""
    import edge_tts
    import av
    import sounddevice as sd
    import os
    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()
    try:
        await edge_tts.Communicate(text, voice).save(tmp.name)
        # Decode MP3 → PCM in a worker thread so we don't block the loop.
        def _decode(path: str):
            container = av.open(path)
            stream = container.streams.audio[0]
            sr = stream.rate
            frames = []
            for frame in container.decode(stream):
                frames.append(frame.to_ndarray())
            arr = np.concatenate(frames, axis=-1)
            if arr.ndim > 1 and arr.shape[0] > 1:
                arr = arr.mean(axis=0)
            elif arr.ndim > 1:
                arr = arr[0]
            if arr.dtype == np.int16:
                arr = arr.astype(np.float32) / 32768.0
            elif arr.dtype == np.int32:
                arr = arr.astype(np.float32) / 2147483648.0
            else:
                arr = arr.astype(np.float32)
            return arr, sr
        pcm, sr = await asyncio.to_thread(_decode, tmp.name)
        sd.play(pcm, sr)
        # Wait for playback in a thread so the loop stays responsive.
        await asyncio.to_thread(sd.wait)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


async def run(args) -> None:
    import sounddevice as sd
    from faster_whisper import WhisperModel
    from pathlib import Path

    print(f"loading whisper model: {args.model} ({args.compute_type})...",
          flush=True)
    t0 = time.time()
    model = WhisperModel(args.model, device=args.device,
                         compute_type=args.compute_type)
    print(f"  loaded in {time.time() - t0:.1f}s", flush=True)

    # Load Narada's voice system prompt.
    sys_prompt_path = Path(args.system_prompt)
    if not sys_prompt_path.is_absolute():
        sys_prompt_path = Path(__file__).resolve().parent.parent / sys_prompt_path
    system_prompt = sys_prompt_path.read_text(encoding="utf-8")
    print(f"loaded system prompt from {sys_prompt_path} "
          f"({len(system_prompt)} chars)", flush=True)
    brain = ClaudeCodeBrain(system_prompt=system_prompt)

    # Connect to device — reconnect-tolerant link.
    print(f"connecting to {args.device_ip}...", flush=True)
    link = DeviceLink(args.device_ip)
    await link.connect()

    # Concurrent response handler — owns the speak/play lifecycle.
    response_task: asyncio.Task | None = None

    async def respond(user_text: str):
        if not user_text.strip():
            return
        try:
            print(f"  brain: thinking...", flush=True)
            await link.set_activity("thinking")
            t_brain = time.time()
            reply = await brain.respond(user_text)
            reply = clean_for_voice(reply)
            print(f"  brain: '{reply}' ({time.time() - t_brain:.1f}s)",
                  flush=True)
            if not reply:
                await link.set_activity("resting")
                return
            # Hand off to the device — bubble pops up, transcript clears.
            await link.set_speech(reply)
            await link.set_activity("speaking")
            await link.push_clear()
            await speak(reply, voice=args.tts_voice)
        except Exception as e:
            print(f"  respond failed: {e}", file=sys.stderr)
        finally:
            await link.set_speech("")
            await link.set_activity("resting")

    # Mic ring buffer (last WINDOW_S of audio).
    win_samples = int(WINDOW_S * SAMPLE_RATE)
    ring = np.zeros(win_samples, dtype=np.float32)
    block_samples = int(BLOCK_MS / 1000.0 * SAMPLE_RATE)
    write_idx = 0
    audio_q: queue.Queue = queue.Queue()

    stream = None  # laptop sd.InputStream; None when using ESP32 mic
    esp32_reader_task: asyncio.Task | None = None

    if args.mic == "laptop":
        def mic_cb(indata, frames, time_info, status):
            if status:
                print(f"  mic status: {status}", file=sys.stderr)
            audio_q.put(indata[:, 0].copy())

        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                blocksize=block_samples, callback=mic_cb,
                                dtype="float32")
        stream.start()
        print(f"listening on laptop mic at {SAMPLE_RATE} Hz mono. "
              f"Ctrl+C to stop.", flush=True)
    else:
        # ESP32 mic — connect to device :6061 and read PCM int16 stream.
        async def esp32_reader():
            while True:
                try:
                    print(f"connecting to ESP32 mic {args.device_ip}:"
                          f"{DEFAULT_MIC_PORT}...", flush=True)
                    reader, writer = await asyncio.open_connection(
                        args.device_ip, DEFAULT_MIC_PORT)
                    print(f"  ESP32 mic connected", flush=True)
                    while True:
                        # Read at most one block of int16 PCM (2 bytes/sample).
                        data = await reader.read(block_samples * 2)
                        if not data:
                            break
                        pcm_i16 = np.frombuffer(data, dtype=np.int16)
                        pcm_f32 = pcm_i16.astype(np.float32) / 32768.0
                        audio_q.put(pcm_f32)
                    writer.close()
                    await writer.wait_closed()
                except Exception as e:
                    print(f"  ESP32 mic error: {e}; retrying in 2s",
                          file=sys.stderr)
                await asyncio.sleep(2.0)

        esp32_reader_task = asyncio.create_task(esp32_reader())
        print(f"listening on ESP32 mic via {args.device_ip}:"
              f"{DEFAULT_MIC_PORT}. Ctrl+C to stop.", flush=True)

    last_pushed = ""
    last_active_t = time.time()
    last_transcribe_t = 0.0
    in_utterance = False
    utterance_ended_t: float | None = None
    brain_triggered = False
    # Listening mode: idle ambient capture until a wake word is heard, then
    # show ear-waves and capture/transcribe/route the next utterance to the
    # brain. Touch-to-listen on the device sets this same state firmware-side.
    listening_mode = False
    listening_started_t: float | None = None
    LISTENING_TIMEOUT_S = 30.0  # bail back to idle if no command after wake

    try:
        while True:
            # Drain mic queue into ring buffer.
            try:
                while True:
                    chunk = audio_q.get_nowait()
                    n = len(chunk)
                    if n >= win_samples:
                        ring[:] = chunk[-win_samples:]
                        write_idx = 0
                    else:
                        end = write_idx + n
                        if end <= win_samples:
                            ring[write_idx:end] = chunk
                        else:
                            split = win_samples - write_idx
                            ring[write_idx:] = chunk[:split]
                            ring[: n - split] = chunk[split:]
                        write_idx = (write_idx + n) % win_samples
            except queue.Empty:
                pass

            # Roll the ring into a contiguous window (most recent at end).
            window = np.concatenate([ring[write_idx:], ring[:write_idx]])

            # RMS over a short recent window for fast onset detection.
            tail_n = block_samples * RMS_TAIL_BLOCKS
            tail = window[-tail_n:] if len(window) >= tail_n else window
            rms = float(np.sqrt(np.mean(tail * tail) + 1e-12))

            now = time.time()
            if rms > SILENCE_RMS:
                last_active_t = now
                # Resume cancels any pending end-of-utterance state — same
                # utterance continues. If brain already triggered, this is
                # a barge-in (TODO step-3: cancel ongoing response).
                if utterance_ended_t is not None:
                    if brain_triggered:
                        print("  barge-in — user resumed after brain trigger",
                              flush=True)
                    utterance_ended_t = None
                    brain_triggered = False
                in_utterance = True

            # Transcribe periodically.
            if in_utterance and (now - last_transcribe_t) >= TRANSCRIBE_EVERY_S:
                last_transcribe_t = now
                segments, _info = model.transcribe(
                    window,
                    language="en",
                    beam_size=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    initial_prompt=WHISPER_PROMPT,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                if text and text != last_pushed:
                    lower = text.lower()
                    heard_wake = any(w in lower for w in WAKE_WORDS)
                    # First wake word in idle → enter listening mode (ear waves).
                    if heard_wake and not listening_mode:
                        listening_mode = True
                        listening_started_t = now
                        await link.set_activity("listening")
                        print(f"  wake word heard — listening", flush=True)
                    # Only stream transcripts to the device while listening.
                    if listening_mode:
                        print(f"  > {text}", flush=True)
                        await link.push_transcript(text)
                        last_pushed = text
                    else:
                        # Show locally so we can debug, but don't push to device.
                        print(f"  (idle) {text}", flush=True)

            # End-of-utterance: silence > silence_s after speaking. Mark
            # the moment they stopped — transcript stays visible (handled
            # by the hold/brain-delay timers below).
            if in_utterance and (now - last_active_t) >= args.silence:
                if last_pushed:
                    print(f"  utterance end (silence {args.silence:.1f}s)",
                          flush=True)
                in_utterance = False
                utterance_ended_t = last_active_t

            # Brain trigger — brain_delay seconds after the user stopped.
            # Only fires when in listening mode (entered via wake word or
            # device touch). Exits listening mode after triggering.
            if (listening_mode and utterance_ended_t is not None
                    and not brain_triggered
                    and (now - utterance_ended_t) >= args.brain_delay):
                brain_triggered = True
                user_text = last_pushed
                print(f"  brain trigger ({args.brain_delay:.1f}s after stop) "
                      f"— last text: {user_text!r}", flush=True)
                if response_task is not None and not response_task.done():
                    response_task.cancel()
                response_task = asyncio.create_task(respond(user_text))
                listening_mode = False
                listening_started_t = None
                # Wipe sliding window so the next wake/utterance isn't
                # re-triggered by audio that's already been routed.
                ring[:] = 0
                last_pushed = ""

            # Listening timeout — if wake word fired but no command followed,
            # quietly drop back to idle so we don't sit on ear-waves forever.
            if (listening_mode and listening_started_t is not None
                    and not in_utterance
                    and (now - listening_started_t) >= LISTENING_TIMEOUT_S):
                print(f"  listening timeout — back to idle", flush=True)
                listening_mode = False
                listening_started_t = None
                last_pushed = ""
                await link.push_clear()
                await link.set_activity("resting")

            # Hold-timer clear — only fires if brain trigger didn't already
            # claim the bubble. If brain triggered, respond() does the clear.
            if (not brain_triggered and utterance_ended_t is not None
                    and last_pushed
                    and (now - utterance_ended_t) >= args.hold):
                print(f"  transcript clear ({args.hold:.1f}s hold)",
                      flush=True)
                last_pushed = ""
                utterance_ended_t = None
                # Zero the audio ring so a brief next utterance isn't
                # drowned out by stale silence-padded context.
                ring[:] = 0
                await link.push_clear()

            await asyncio.sleep(0.05)
    except KeyboardInterrupt:
        print("\nstopping.", flush=True)
    finally:
        if stream is not None:
            stream.stop()
        if esp32_reader_task is not None:
            esp32_reader_task.cancel()
        await link.push_clear()
        await link.disconnect()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--device-ip", default=DEFAULT_DEVICE_IP)
    p.add_argument("--model", default="base.en",
                   help="whisper model size (tiny.en, base.en, small.en, medium.en)")
    p.add_argument("--device", default="cuda",
                   choices=["cuda", "cpu"])
    p.add_argument("--compute-type", default="float16",
                   help="float16 / int8_float16 / int8 (cpu uses int8)")
    p.add_argument("--silence", type=float, default=DEFAULT_SILENCE_S,
                   help=f"silence (s) marking end of utterance (default {DEFAULT_SILENCE_S})")
    p.add_argument("--hold", type=float, default=DEFAULT_HOLD_S,
                   help=f"keep transcript visible (s) after stop (default {DEFAULT_HOLD_S})")
    p.add_argument("--brain-delay", type=float, default=DEFAULT_BRAIN_DELAY_S,
                   help=f"wait (s) after stop before Narada responds (default {DEFAULT_BRAIN_DELAY_S})")
    p.add_argument("--system-prompt", default=SYSTEM_PROMPT_PATH,
                   help=f"path to Narada's voice system prompt (default {SYSTEM_PROMPT_PATH})")
    p.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE,
                   help=f"edge-tts voice name (default {DEFAULT_TTS_VOICE})")
    p.add_argument("--mic", default="esp32", choices=["esp32", "laptop"],
                   help="audio source: esp32 (TCP from device :6061) or laptop")
    args = p.parse_args()
    if args.device == "cpu" and args.compute_type == "float16":
        args.compute_type = "int8"
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
