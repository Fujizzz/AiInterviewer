"""Local-only speech HTTP endpoints, separate from interview and evaluation APIs.

目录：
- tts：
  Convert bounded English question text into a temporary WAV capability URL.
- audio：
  Return cached WAV to the UE HTTP client; no external media URLs are fetched.

关键变量：
（无模块级变量。）
"""

import time

from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .service import SpeechError, audio_store, synthesize


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
    response = Response(
        {
            "utterance_id": utterance_id,
            "audio_url": request.build_absolute_uri(f"/api/speech/audio/{utterance_id}/"),
            "sample_rate": 24000,
            "generation_ms": round((time.monotonic() - started) * 1000),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@api_view(["GET"])
def audio(request, utterance_id):
    """Return cached WAV to the UE HTTP client; no external media URLs are fetched."""
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
