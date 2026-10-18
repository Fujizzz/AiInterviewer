"""Offline-only audio endpoint for Interviewer.NativeAudioSmoke.

This is not a TTS provider. Do not run alongside the real backend (same port).
The default synthetic tone checks inference, not speech quality or visible lip sync.
Use --wav <cached.wav> to replay existing speech without making cloud calls.
"""

import argparse
import io
import math
import struct
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wav",
        type=Path,
        help="Replay a cached 24 kHz mono PCM16 WAV instead of the synthetic tone.",
    )
    args = parser.parse_args()
    if args.wav is not None:
        try:
            wav = args.wav.read_bytes()
            with wave.open(io.BytesIO(wav), "rb") as audio:
                if (
                    audio.getcomptype(),
                    audio.getnchannels(),
                    audio.getsampwidth(),
                    audio.getframerate(),
                ) != ("NONE", 1, 2, 24000):
                    raise ValueError("cached WAV must be uncompressed 24 kHz mono PCM16")
                if (
                    audio.getnframes() <= 0
                    or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2
                ):
                    raise ValueError("cached WAV must contain complete, non-empty PCM data")
                duration = audio.getnframes() / audio.getframerate()
            if len(wav) > 5760044:
                raise ValueError("cached WAV exceeds the runtime's 5,760,044-byte limit")
        except (OSError, EOFError, wave.Error, ValueError) as error:
            parser.error(str(error))
        print(
            f"OFFLINE cached speech fixture on 127.0.0.1:8765: {args.wav.resolve()} "
            f"({duration:.2f}s, 24 kHz mono PCM16); no cloud calls",
            flush=True,
        )
    else:
        print("OFFLINE synthetic tone fixture on 127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
