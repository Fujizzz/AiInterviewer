"""Responsibilities: Provide authenticated speech HTTP endpoints, separate from interview and
evaluation APIs.
Implementation: Synthesize bounded English question text to a temporary WAV capability and serve
only cached WAV data.
Related Modules: speech.service owns provider configuration and the in-memory audio store;
speech.socket owns recognition; speech.audio_access grants short-lived renderer downloads.

Declaration Index:
- tts:
  Convert bounded English question text into a temporary WAV capability URL.
- audio:
  Return cached WAV to the UE HTTP client; no external media URLs are fetched.
- _wav_duration_ms:
  Read generated WAV duration for the bounded native mouth-preparation deadline.

Variable Index:
None
"""

import io
import time
import wave
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .audio_access import create_audio_token, has_audio_capability
from .service import SpeechError, audio_store, synthesize


def _wav_duration_ms(wav):
    """Read generated media metadata without another synthesis or audio download."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as audio:
            if audio.getframerate() <= 0:
                return None
            return round(audio.getnframes() * 1000 / audio.getframerate())
    except (wave.Error, EOFError):
        return None


@api_view(["POST"])
def tts(request):
    """Convert bounded English question text into a temporary WAV capability URL."""
    text = request.data.get("text") if isinstance(request.data, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 1200:
        return Response(
            {
                "error": {
                    "code": "invalid_text",
                    "detail": "Provide 1-1200 characters of question text.",
                }
            },
            status=400,
        )
    started = time.monotonic()
    try:
        wav = synthesize(text.strip())
        utterance_id = audio_store.put(wav)
    except SpeechError as exc:
        return Response({"error": {"code": exc.code, "detail": exc.detail}}, status=exc.status)
    audio_path = f"/api/speech/audio/{utterance_id}/"
    if settings.INTERVIEW_REQUIRE_LOGIN:
        audio_path += "?" + urlencode({"token": create_audio_token(utterance_id)})
    response = Response(
        {
            "utterance_id": utterance_id,
            "audio_url": request.build_absolute_uri(audio_path),
            "sample_rate": 24000,
            "duration_ms": _wav_duration_ms(wav),
            "generation_ms": round((time.monotonic() - started) * 1000),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@api_view(["GET"])
def audio(request, utterance_id):
    """Return cached WAV to the UE HTTP client; no external media URLs are fetched."""
    if (
        settings.INTERVIEW_REQUIRE_LOGIN
        and not getattr(request.user, "is_authenticated", False)
        and not has_audio_capability(request, utterance_id)
    ):
        response = Response(
            {"error": {"code": "authentication_required", "detail": "Please sign in."}},
            status=401,
        )
        response["Cache-Control"] = "no-store"
        return response
    wav = audio_store.get(str(utterance_id))
    if wav is None:
        return Response(
            {
                "error": {
                    "code": "audio_expired",
                    "detail": "Audio is missing or expired; synthesize it again.",
                }
            },
            status=404,
        )
    response = HttpResponse(wav, content_type="audio/wav")
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
