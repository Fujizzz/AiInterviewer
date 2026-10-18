"""Responsibilities: Grant short-lived download access to one generated interview WAV.
Implementation: Sign its canonical UUID with Django's timestamped, namespaced signer; permit only
a GET on that precise route with one bounded token. No sessions or provider credentials are shared.
Related Modules: speech.views issues/validates the capability; accounts checks it before login.

Declaration Index:
- create_audio_token: Issue a 600-second capability tied to one canonical audio UUID.
- has_audio_capability: Validate the method, canonical path, sole query token, signature and age.

Variable Index:
- AUDIO_TOKEN_SALT: Signing namespace reserved for temporary speech audio downloads.
- AUDIO_TOKEN_MAX_AGE: Maximum capability lifetime in seconds.
- MAX_AUDIO_TOKEN_LENGTH: Bound token decoding and signature verification work.
- MAX_AUDIO_QUERY_LENGTH: Reject excessive raw query strings before parsing them.
- AUDIO_PATH: Exact route pattern; other speech and business endpoints cannot use this exception.
- AUDIO_TOKEN: URL-safe characters produced by the uncompressed Django signer.
"""

import re
from uuid import UUID

from django.core import signing

AUDIO_TOKEN_SALT = "interviews.speech.audio.v1"
AUDIO_TOKEN_MAX_AGE = 600
MAX_AUDIO_TOKEN_LENGTH = 255
MAX_AUDIO_QUERY_LENGTH = 1024
AUDIO_PATH = re.compile(
    r"/api/speech/audio/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
)
AUDIO_TOKEN = re.compile(r"[A-Za-z0-9_:-]+")


def create_audio_token(utterance_id):
    """Sign only the canonical UUID; Django supplies the expiry timestamp and secret key."""
    return signing.dumps(str(UUID(str(utterance_id))), salt=AUDIO_TOKEN_SALT)


def has_audio_capability(request, utterance_id=None):
    """Accept one bounded token for this GET only, without widening any login or CSRF gate."""
    if request.method != "GET":
        return False
    match = AUDIO_PATH.fullmatch(request.path)
    if match is None:
        return False
    audio_id = match[1]
    if utterance_id is not None and str(utterance_id) != audio_id:
        return False
    query = request.META.get("QUERY_STRING", "")
    if not isinstance(query, str) or len(query) > MAX_AUDIO_QUERY_LENGTH:
        return False
    if set(request.GET) != {"token"}:
        return False
    tokens = request.GET.getlist("token")
    if len(tokens) != 1:
        return False
    token = tokens[0]
    if not token or len(token) > MAX_AUDIO_TOKEN_LENGTH or AUDIO_TOKEN.fullmatch(token) is None:
        return False
    try:
        signed_audio_id = signing.loads(token, salt=AUDIO_TOKEN_SALT, max_age=AUDIO_TOKEN_MAX_AGE)
    except (signing.BadSignature, ValueError, TypeError):
        return False
    return isinstance(signed_audio_id, str) and signed_audio_id == audio_id
