# Browser speech streaming

The browser-only question playback path uses `POST /api/speech/tts/stream/`.
The backend forwards validated PCM fragments from the existing synthesis adapter
as newline-delimited JSON. Playback can start while the provider is still
generating the remainder of the question.

This change does not select a new model, voice, language, provider region or audio
format. The measured deployment uses `qwen3-tts-flash-realtime`, Cherry, English,
and 24 kHz mono signed 16-bit PCM. Provider completion retains its 45-second
budget; the browser request has a 50-second cancellation timer. SDK connection
and cleanup can add elapsed time around the provider completion wait.

## Routing and playback

- With no active or connecting avatar transport, `InterviewVoice.speak` selects
  browser streaming. An active/connecting renderer retains the complete-WAV
  protocol and its existing fallback behavior; no UE source is changed.
- Authentication and CSRF remain enforced by Django middleware. The stream
  response is `application/x-ndjson`, `Cache-Control: no-store`, and
  `X-Accel-Buffering: no`. The existing Nginx configuration already disables
  response buffering; no proxy or TLS configuration change is needed.
- The response contains a format/identity `start`, ordered base64 PCM `audio`
  records, then an explicit `end` with byte/timing totals, or an `error`.
  Transport EOF alone never signifies successful synthesis.
- Each audio record contains at most 8 KiB of PCM. A 32-item queue bounds pending
  transport data and applies backpressure to the producer. The existing
  120-second audio bound remains in force.
- Web Audio starts after at least 240 ms of audio has arrived, with 60 ms of
  scheduling lead. This is an **audio-duration reserve**, not a fixed 240 ms
  wall-clock delay. Adjacent buffers share the same sample clock. Shorter
  utterances start after verified input completion.
- Browser onset, first received audio and server synthesis completion are
  separate UI metrics. The ten-second answer preparation period begins only
  after scheduled audio drains, or after the existing explicit failure/stop
  handling releases playback.
- Replay, question replacement and page cleanup stop queued sources and abort
  the request. Disconnect signals the producer to stop; an in-progress SDK
  connect is bounded by the SDK's existing timeout.
- A provider/protocol failure, truncated response or audio underrun stops partial
  playback. There is no new automatic retry, full-audio replay or model fallback.

NDJSON base64 adds roughly one third to the raw PCM payload size. It provides
explicit completion/error framing using the existing same-origin POST and CSRF
mechanism, and removes the browser-only path's separate WAV download request.
No region migration, connection reuse, speech caching or text segmentation is
introduced.

## Verification on 2026-10-21

The Windows default Python environment lacks Django. Backend tests therefore
ran in a temporary copy of the deployed backend using its installed dependencies
and an isolated test database. Local changed source was overlaid into that copy;
the running service and production database were not modified.

- Backend: `python manage.py test interviews.tests.test_speech interviews.tests.test_speech_streaming --verbosity 1 --noinput`
  passed **38 tests**. This includes existing synthesis/WAV behavior, incremental
  delivery before completion, odd-fragment alignment, disconnect under queue
  backpressure, explicit terminal failure, account and CSRF checks.
- Frontend: from `frontend/digital-human`,
  `node --test tests/speech-stream.test.mjs tests/speech.test.mjs tests/presentation.test.mjs`
  passed **64 tests**. Web Audio doubles verify ordering, sample conversion,
  cancellation, malformed/truncated streams, pending autoplay cancellation and
  preparation timing. They do not verify a physical sound device.
- A real Chromium browser exercised the production PCM player with one second
  of silent samples. Onset preceded delayed input completion; final audio drain
  followed it; the context closed and queued sources were released. This checks
  real audio-clock behavior, not speech quality or audible speaker output.
- `python tools/check_docs.py` completed. All changed/new code files have no
  reported documentation issues. The full backend still reports **271 existing
  issues in other files** (289 before this work; the edited legacy speech test
  file's index/comments were brought into alignment). Manual review of affected
  descriptions and indexes was also completed.
- `git diff --check` passed. This initial verification preceded the Git commit;
  no deployment was performed.

Two real HTTP requests used a loopback-only ASGI process in the isolated server
copy and the deployed provider configuration. Fixed, non-personal English inputs
were used; neither PCM nor credentials were saved in the measurement log.

| Input | First PCM at HTTP consumer | Initial 240 ms audio reserve available | Complete synthesis | Result |
| --- | ---: | ---: | ---: | --- |
| 70 characters | — | — | 46,746.5 ms elapsed | Explicit `speech_timeout`; no audio |
| 208 characters | 1,306.2 ms | 1,318.2 ms | 3,251.1 ms | Success; 11,440 ms audio in 72 chunks |

For the successful request, sufficient data for playback arrived about 1.93
seconds before complete synthesis. Adding the configured 60 ms scheduling lead
gives approximately 1.38 seconds before browser/device overhead on this loopback
path. These are **not end-user WAN-to-speaker measurements** and are not a
latency guarantee. The timeout is retained in the results; streaming does not
resolve upstream requests that never produce audio.

The initial latency audit used different fixed inputs, so its absolute timings
must not be treated as a controlled before/after comparison with these two
requests. Audit scripts and raw logs are in the workspace's
`output/tts-latency-audit-20261021` directory outside Git.

## Integration with remote main on 2026-10-21

Before committing, `origin/main` was fetched and the local branch fast-forwarded
from `5d073cc` to `b5b159b`, incorporating five remote commits. Local streaming
changes were preserved and reapplied without conflicts. The static resource
allowlist retains both the upstream `evaluation-poll.js` and the new
`speech-playback.js`.

The existing page-level VM test harness now loads the real streaming playback
module alongside the voice coordinator and upstream evaluation polling module.
This fixes the new static import dependency in the test environment without
replacing application behavior or weakening test assertions.

After integration:

- Backend speech, static module loading and evaluation recovery suites passed
  **56 tests** in the isolated server copy. The added labels were
  `interviews.tests.test_frontend_modules` and
  `interviews.tests.test_evaluation_worker`.
- Frontend speech, presentation, full interview-page, evaluation polling and
  history suites passed **119 tests**. Run from the backend directory:
  `node --test frontend/digital-human/tests/speech-stream.test.mjs frontend/digital-human/tests/speech.test.mjs frontend/digital-human/tests/presentation.test.mjs tests/agent-client.test.mjs tests/evaluation-poll.test.mjs tests/interview-history.test.mjs`.
- The documentation checker completed with **415 issues in other files** on
  the newer upstream tree; no issue was reported for the files changed by this
  commit. This differs from the earlier 271 because the upstream source changed.
  The edited page test's module variable index now also includes `POLL_SOURCE`.
- `git diff --check` passed and there were no unresolved merge paths.

Implementation, tests, comments and declaration indexes are included together
in the streaming commit. Production deployment and UE changes are outside this
integration.
