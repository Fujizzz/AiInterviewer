"""Offline-only synthetic audio endpoint for Interviewer.NativeAudioSmoke.

This is not a TTS provider. Do not run alongside the real backend (same port).
The smoke test checks native inference, not speech quality or visible lip sync.
"""

import io
import math
import struct
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

output = io.BytesIO()
with wave.open(output, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(24000)
    audio.writeframes(
        b"".join(
            struct.pack("<h", int(5000 * math.sin(2 * math.pi * 220 * index / 24000)))
            for index in range(24000 * 4)
        )
    )
wav = output.getvalue()


class Handler(BaseHTTPRequestHandler):
    """Serve only the explicitly named offline test capability."""

    def do_GET(self):
        """Return known mono PCM bytes or 404; no files or credentials are read."""
        if self.path not in {
            "/api/speech/audio/00000000-0000-0000-0000-000000000001/",
            "/api/speech/audio/00000000-0000-0000-0000-000000000002/",
        }:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        self.wfile.write(wav)


if __name__ == "__main__":
    print("OFFLINE synthetic tone fixture on 127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
