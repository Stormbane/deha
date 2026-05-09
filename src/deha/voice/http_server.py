"""HTTP server for serving TTS audio to the device's media_player.

The device's announcement_pipeline fetches via http_request when we send
TTS_END {url: ...}. We serve WAV (Piper's native format) directly — the
pipeline resamples internally.

Threading model: the server runs in its OWN thread on its OWN socket
loop. It does NOT share asyncio with the APIClient. This matters: a
large WAV (~1-3 MB for a 30s reply) takes seconds to drain over slow
WiFi to an ESP32. If the HTTP send loop ran on the same asyncio
event loop as the APIClient, it would block keepalive processing —
Windows would time out the API socket with WinError 121, killing the
voice_assistant subscription mid-turn (we hit this on 2026-05-03).
A stdlib `ThreadingHTTPServer` in a separate thread isolates the I/O
completely.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import SHUT_WR


def local_ip(target_ip: str) -> str:
    """Find the laptop IP that the target device can reach."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 1))
        return s.getsockname()[0]
    finally:
        s.close()


_CTYPES = {
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
}


def _make_handler(serve_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/tts/"):
                self.send_error(404)
                return
            name = self.path[len("/tts/"):]
            # Strip any query string just in case.
            if "?" in name:
                name = name.split("?", 1)[0]
            path = serve_dir / name
            if not path.exists() or not path.is_file():
                self.send_error(404)
                return
            ctype = _CTYPES.get(path.suffix.lower(), "application/octet-stream")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(data)
                self.wfile.flush()
                # Force the FIN immediately so esp_http_client's
                # `is_complete_data_received` flips and audio_pipeline's
                # READER_MESSAGE_FINISHED fires within ~one HTTP read
                # cycle. Without SHUT_WR the device sits up to seconds
                # waiting for the close handshake — that was the
                # post-decode 8-12s playback gap on the BOX-3.
                # Discovered 2026-05-04 via audio_pipeline.cpp source
                # research. The SHUT_WR after Content-Length matches
                # is the canonical "tell the slow client we're done"
                # pattern.
                try:
                    self.connection.shutdown(SHUT_WR)
                except OSError:
                    pass
            except (BrokenPipeError, ConnectionResetError):
                # Device aborted mid-fetch — fine, next turn will retry.
                pass

        def log_message(self, fmt, *args):  # silence default access logging
            return

    return Handler


class TTSServer:
    """Serve TTS audio files at http://host:port/tts/{name}.

    `start()` spawns a daemon thread running ThreadingHTTPServer.
    `stop()` shuts it down. `write()` saves bytes and returns the URL.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.dir = Path(tempfile.gettempdir()) / "narada-tts"
        self.dir.mkdir(exist_ok=True)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        handler_cls = _make_handler(self.dir)
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="tts-http",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def write(self, audio_bytes: bytes, ext: str = ".wav") -> str:
        """Save audio bytes and return the URL the device can fetch."""
        name = f"{uuid.uuid4().hex}{ext}"
        (self.dir / name).write_bytes(audio_bytes)
        return f"http://{self.host}:{self.port}/tts/{name}"


__all__ = ["TTSServer", "local_ip"]
