# Backend, MVP Agent and streaming diagnostics

Django and Django REST Framework provide question-bank, practice-session and per-question record APIs. Development uses SQLite; production explicitly uses PostgreSQL. Transport diagnostic media and counters stay in memory.

The same ASGI service exposes WebSockets and browser diagnostic pages for ping/pong, binary chunks and audio/video transport. The root MVP Agent is available at `/ws/agent/`; its interview workspace is `/agent/`. The web and CLI clients share Plan and Execute: the default budget is 30 minutes, the Planner allocates goals/time and the Question Agent generates questions.

Interview model output passes the behavioral safety gateway before delivery, including profile previews, question/assessment payloads, score events and final reports. Failed or timed-out checks stop the session; history exposes only checked responses. The gateway uses the existing project model and a five-second safety budget. See [the behavioral security contract](../docs/modules/AI_SECURITY_BEHAVIOR.md). Tool calls and internal assessment commits are outside that gateway.

`start.duration_minutes` defines the time budget. The three `max_questions*` parameters are safety limits. Timing starts when the first question is ready and includes later user input and model waits. Completion is checked after answer submission; active input is not forcibly interrupted. The separate practice APIs retain their ten-second preparation and ninety-second answer settings.

The interview workspace provides questions, a digital interviewer, local camera preview, speech/text answers and resume/job/budget selection. Camera preview starts only after an explicit action. UE Pixel Streaming supports the digital interviewer, question speech, interruption, microphone answers and editable transcription. Only ready resume versions owned by the current user are selectable. Real progress and elapsed timing remain visible; scores can arrive before report text. The backend `prepare` protocol remains available to independent clients, while the workspace starts interviews with a selected version UUID. See [performance boundaries](docs/performance.md).

`/resumes/` provides profile details, PDF/text upload, parsing, an editor and version history. Traditional extraction is the default; advanced multimodal review is optional. Confirmed versions can become current or be selected for an interview. See [PDF processing](docs/resume-pdf.md). PDF extraction/rendering uses a Linux/WSL sandbox, with host-level interview and upload capacity limits. Configure [the sandbox runtime](sandbox/README.md) before processing PDFs.

The fixed v4-B bidirectional ranker accepts incomplete profiles and exposes `/api/recommendations/jobs/` and `/api/recommendations/candidates/`. Weights ship with the repository; local inference needs neither Kaggle nor LLM credentials. This experimental model did not meet the overall research acceptance threshold and does not report hiring probabilities. See [the recommendation contract and limitations](docs/recommendation.md).

## Engineering and documentation requirements

Write new backend implementation comments and developer documentation in English. All coding agents changing this directory must follow these requirements:

1. Provide accurate, verifiable implementation comments for functions, methods and important code blocks. Explain functionality, inputs, outputs, logic, rationale and constraints, including relevant state transitions, boundary cases, exceptions and side effects. Avoid restating code or inventing academic citations.
2. Start each code file with responsibilities, implementation, related modules, a declaration index and a variable index. Index only declarations actually implemented in that file and document key variables/constants/configuration. Use the English `Declaration Index:` and `Variable Index:` markers, `- symbol: description` entries and `None` for empty indexes.
3. Update code, comments and indexes as one logical unit. When committing, include them in one commit. Signature, behavior, state and data-flow changes require corresponding documentation changes; deleted implementations require removing obsolete documentation.
4. Manually review descriptions and run `python tools/check_docs.py` from this directory. Checker changes also require `python tools/test_check_docs.py` and `python -m unittest discover -s tools -p "test_*.py"`. Python declarations and JavaScript declarations, including anonymous callbacks, need both declaration comments and exact header entries. Checks reject stale entries. Automated structural checks do not establish semantic accuracy or commit atomicity.

See [the code guide](docs/code-guide.md) for the exact format and module responsibilities.

## Stack and environment

| Technology | Previously verified version | Purpose |
| --- | --- | --- |
| Python | 3.12.14 | Backend runtime |
| Django | 5.2.17 | ORM, migrations and HTTP routing |
| Django REST Framework | 3.18.1 | JSON APIs, serializers and validation |
| SQLite | 3.53.4 locally | Default storage at `backend/db.sqlite3` |
| Uvicorn | 0.52.4 | ASGI HTTP/WebSocket server |
| websockets | 16.1.1 | WebSocket protocol support |
| Native browser APIs | WebSocket / MediaRecorder / Web Audio / Canvas | Frontend diagnostics without an npm build |

Direct backend dependencies are pinned in `requirements.txt`. Install them with `python -m pip install -r requirements.txt`; use a Python environment of your choice. The examples assume `python` refers to that interpreter. The file includes root MVP dependencies through `-r ../requirements.txt`, so keep the complete repository. Root `pyproject.toml` and `uv.lock` still manage the CLI MVP environment.

Development defaults to SQLite. Production explicitly selects PostgreSQL via `config.production`; connection failures do not switch databases. HTTPS, accounts, systemd and maintenance commands are in [the deployment guide](../deploy/README.md).

## Starting the service

From this directory in PowerShell:

```powershell
python -m pip install -r requirements.txt
python -m pip check
$env:DJANGO_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

Open [the workspace](http://127.0.0.1:8765/), [transport diagnostics](http://127.0.0.1:8765/stream-demo/) or [the health endpoint](http://127.0.0.1:8765/api/health/). Open [the interview workspace](http://127.0.0.1:8765/agent/) after configuring model credentials.

Use the ASGI command; `manage.py runserver` does not serve these WebSocket routes. `DJANGO_SECRET_KEY` must be set in the process environment or repository-root `.env`. The example creates a random value for the current shell; source code includes no application secret. `INTERVIEW_DB_PATH` can explicitly select another SQLite path. Local databases and secret files are ignored by Git. Migrations create tables and seed the original two generic practice questions without overwriting existing questions.

## Interface language

English is the default when no valid browser preference is stored. The language selector retains English, Chinese and follow-system choices. Explicit preferences are stored in `localStorage`. Follow-system selects Chinese for a Chinese browser and English otherwise, and responds to browser language changes.

Initial page markup uses English before scripts run. English translation lookups do not use Chinese strings for missing entries. System-generated resume headings and the bundled catalog's source label use English. User-authored resume text, answers, uploaded filenames and intentional bilingual recognition data keep their original language.

The language layer changes interface text, statuses and diagnostic messages; WebSocket commands, API field names, scoring and experimental model inputs retain their existing contracts. Browser language selection does not itself translate user data or change the shared interview model's language policy.

## Agent model configuration

Resume-version, interview-binding and history contracts are in [the version API guide](docs/resume-versions.md). Resources use authenticated sessions and ownership checks without changing question-generation or assessment strategy.

Place API credentials in the **repository-root `.env`**. For initial setup, copy `.env.example` if no file exists; edit an existing file without overwriting it. OpenAI uses `LLM_PROVIDER=openai`, `OPENAI_API_KEY` and `OPENAI_MODEL`. DashScope uses `LLM_PROVIDER=dashscope`, `DASHSCOPE_API_KEY` and `DASHSCOPE_MODEL`.

The backend loads that file automatically, with existing process environment values taking precedence. Restart after configuration changes. Git ignores `.env`; the template contains no credentials. See [Agent integration](docs/agent-integration.md) for configuration, protocol and cancellation limits.

## Transport diagnostics

1. **Ping/pong:** Correlates message IDs and displays round-trip latency.
2. **Binary chunks:** Sends twelve 32 KiB chunks and updates verification counts before closing.
3. **Synthetic audio/video:** Records canvas video and a generated tone for about three seconds as WebM, returns MediaRecorder chunks and plays the reconstructed result.
4. **Real devices:** Requests microphone/camera access after a user action and stops manually or after thirty seconds.

Blob conversion, SHA-256 checks and returned chunks are processed in order. The client waits for final verification before sending `finish`. Unsupported codecs, denied permission, deadlines, disconnects, oversized chunks and verification failures surface errors without automatic reconnect, retries, dropped frames or format substitution.

Recording targets a 250 ms chunk interval; browsers do not guarantee exact cadence. Playback after completion verifies reconstruction/decoding. Remote playback while recording is not implemented.

## Directory and documentation map

```text
backend/
  config/                    Django settings, URLs, ASGI and Celery
  interviews/
    models.py                Practice tables
    services.py              Practice transactions and state transitions
    agent_provider.py        Model configuration, redacted logs and cleanup
    agent_session.py         Per-turn MVP network adapter and database repository
    agent_models.py           Interview/request/question/answer/commit schemas
    agent_repository.py       Atomic state, assessment and action commits
    agent_records.py          Request reservation, response storage and interruption records
    agent_socket.py           Interview commands and connection lifecycle
    answer_mcp.py             Completion receipts and existing answer submission
    resume_pdf.py             PDF text extraction and bounded rendering
    resume_api.py             Multipart uploads and NDJSON stages
    resume_vision.py          Async vision-model adapter
    access.py                 Shared HTTP/WebSocket access policy
    middleware.py             HTTP access checks
    demo.py                   Page/asset allowlist
    api/                      REST serializers, views and routes
    streaming/                Protocol validation and ASGI connections
    speech/                   ASR/TTS and independent completion detection
    presentation/             Four-state facial behaviour plans; recorded clips are failure fallback
    migrations/               Schema history and initial questions
    tests/                    Django/ASGI tests
  frontend/                   Backend-served pages and browser modules
  docs/                       Schema, APIs, implementation and test notes
  tests/                      Node client tests and live-server harnesses
  tools/check_docs.py          Declaration comments and index consistency
  tools/javascript_docs.py    Tree-sitter declarations and adjacent JSDoc
  tools/test_check_docs.py     Isolated documentation-checker regression tests
  tools/test_docs_contract.py  Header/declaration, parsing and exit-code contracts
  requirements-docs.txt       Pinned development-only parser dependencies
```

Start with [the code guide](docs/code-guide.md), [schema](docs/schema.md), [API contract](docs/api.md) and [test notes](docs/testing.md). The independent digital-human expression service and local preview controls are described in [facial presentation](docs/digital-human-presentation.md).

## Tests

Documentation checks use Python AST and Tree-sitter JavaScript parsing. Install `requirements-docs.txt` first. Missing parsers or parse errors fail explicitly; checks do not skip files or substitute regex parsing.

```powershell
# Set DJANGO_SECRET_KEY for this shell as shown in the startup instructions.
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test interviews
python tools/check_docs.py
python tools/test_check_docs.py
python -m unittest discover -s tools -p "test_*.py"

# Node.js 22+; harnesses use temporary SQLite databases and ports.
python tests/run_e2e.py
python tests/run_agent_e2e.py  # Real ASGI with an offline model double.
node --test tests/*.test.mjs frontend/digital-human/tests/*.test.mjs
```

Mocked microphone/camera and provider tests do not verify actual devices or external services.

## Current boundaries

- Development requires loopback access and same origin, and binds to `127.0.0.1`. Production uses a same-host Nginx HTTPS proxy; the application still binds to loopback. Django sessions protect pages, APIs and WebSockets. Accounts own separate interview/practice records; the question bank is shared.
- Diagnostic media, byte counts and verification counters stay in memory. Connections use temporary IDs. Page logs retain thirty lines, and server logs use the console. Playback Blob URLs are released on clearing, the next diagnostic run or page exit. Diagnostic media is not stored in browser databases or downloaded.
- `verified_chunks` is a client-reported diagnostic count, not proof against malicious clients.
- Agent interviews persist parsed profiles, questions, answers, state, decisions and checked responses. Read-only history APIs enforce access and ownership. Original resume/PDF handling and temporary upload files follow the existing version/upload contracts; clearing the page does not delete interview history. Disconnect recovery is not implemented.
- DashScope ASR/TTS and digital-human WebRTC are integrated. Video storage and MySQL adaptation are not implemented. Scoring and planning remain owned by shared root modules.
- Questions and reports arrive as complete payloads rather than token streams. The existing MVP model-repair and question/report fallback behavior remains unchanged. Disconnecting cannot guarantee that a synchronous remote request stops provider-side execution or billing.

## Protocol references

- [MediaRecorder dataavailable](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event): chunk events and final data.
- [WebSocket binaryType](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/binaryType): browser ArrayBuffer delivery.
- [Django SQLite notes](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes): concurrency and transaction limits.

## Registration and login

Production `/register/` and `/login/` require a username and password. Registration logs in immediately; email, verification codes and password-composition rules are not required. Usernames are nonempty and at most 150 characters; passwords are nonempty and at most 128 characters. Django hashes passwords. Logout is a CSRF-protected POST to `/logout/`.

Production requires login; default loopback development permits anonymous access. Anonymous pages redirect to login; APIs return 401. WebSockets validate session cookies and recheck sessions on later messages. Writes require session/CSRF validation; the PDF client reads the page token and sends it with requests. Cross-account interview/request/practice IDs return 404. Pre-ownership records remain unassigned.

## Background PDF tasks

Production uses Redis/Celery workers for PDF processing while retaining NDJSON progress and cancellation. Inline development needs no Redis. Explicit Celery mode has no fault fallback or business-task retries. Short-lived Redis keys are backend-only; ownership and CSRF checks run before enqueueing. Clients cannot choose task IDs. Production deployment and automatic publication from `main` are documented in [the deployment guide](../deploy/README.md).
