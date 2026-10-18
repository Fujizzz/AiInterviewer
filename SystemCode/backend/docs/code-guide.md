# Backend code guide

The backend provides practice APIs, transport diagnostics and the MVP Agent interview. `agent_session.py` calls the shared `InterviewAgentService` and reuses its planning, assessment and reporting implementations. Django stores practice records, Agent state, completed responses and resume versions. Diagnostic media remains in memory.

## Reading order and responsibilities

| Order | File or directory | Responsibility and main entry points |
| --- | --- | --- |
| 1 | `config/asgi.py`, `config/urls.py` | ASGI lifecycle, HTTP/WebSocket dispatch and URL composition |
| 2 | `interviews/access.py`, `middleware.py`, `session_socket.py`, `accounts.py` | Loopback proxy, Host/origin checks and account/session boundaries |
| 3 | `interviews/models.py`, `agent_models.py`, `resume_models.py`, `docs/schema.md` | Practice, interview and resume records, state fields and database constraints |
| 4 | `interviews/api/serializers.py` | Explicit input fields, unknown-field rejection, action validation and response definitions |
| 5 | `interviews/api/views.py`, `services.py` | HTTP adaptation, transactions and state transitions |
| 6 | `interviews/errors.py` | Expected error structures and contextual exception logs |
| 7 | `interviews/streaming/protocol.py` | `EchoState` validation of modes, sequences, capacity and verification counts |
| 8 | `interviews/streaming/websocket.py` | Receive loop, message dispatch, acknowledgements, binary delivery and closure |
| 9 | `interviews/demo.py` | Page/asset allowlist and responses that disable caching |
| 10 | `frontend/stream-client.js` | Request correlation, deadlines, SHA-256 checks, completion confirmation and cancellation |
| 11 | `frontend/media.js` | Media sources, recording, serial chunk delivery and resource cleanup |
| 12 | `frontend/view.js`, `app.js` | DOM and Blob URLs, buttons, diagnostic flows and page lifecycle |
| 13 | `interviews/tests/`, `tests/` | Business/protocol boundaries, client failure semantics and live-server integration |
| 14 | `interviews/agent_provider.py` | Explicit provider configuration, shared MVP calls, redacted logs and client cleanup |
| 15 | `interviews/agent_session.py` | Preparation, answering and reporting with the existing decision order and timing |
| 16 | `interviews/agent_socket.py`, `agent_records.py`, `agent_repository.py` | Strict commands, request ordering, persistence and connection lifecycle |
| 17 | `frontend/agent.html`, `agent.js`, `agent.css` | Interview workspace; browser code does not store provider credentials |
| 18 | `frontend/interview-voice.js`, `speech-capture.js`, `interviews/speech/` | Speech state, microphone capture, transcription and completion receipts |
| 19 | `frontend/digital-human/` | Pixel Streaming SDK wrapper, player build and speech client tests |
| 20 | `interviews/answer_mcp.py` | Authenticated completion receipts and submission through the existing assessment flow |
| 21 | `interviews/resume_editor.py`, `resume_slots.py`, `api/resume_versions.py` | Resume units, confirmed recommendation fields, versions and editions |
| 22 | `frontend/i18n.js` | English defaults and explicit Chinese/system-language selection |

Frontend source for backend-served pages remains under `backend/frontend/`. Player maintenance and build commands are in [the frontend guide](../frontend/README.md).

## Data paths

REST writes pass access checks and serializer validation before entering the service layer. Updates claim a version with a conditional `UPDATE`, then validate question state and commit. Validation failures roll back both version and business changes. SQLite correctness does not depend on row-level locking. Session creation queries only required questions; default selection reads at most 101 rows so the final row can detect the existing 100-question limit.

Diagnostic WebSockets pass a separate handshake check before the receive loop. The protocol retains counters and metadata, acknowledges validated chunks and returns binary payloads unchanged. The client serializes hash/payload verification and confirms final counts. The server does not store chunks. The browser owns only the current playback Blob URL and releases it on clearing, a new test or page exit.

Agent connections pass origin and session checks before `agent_records` reserves requests in the database. `agent_models` separates Agent records from fixed practice tables. `agent_repository` commits context, questions, answer assessments, actions and decisions atomically by version. Shared MVP components still parse resumes and assess answers; planning and reporting retain their existing policies. Each connection processes one command at a time, and the database permits at most one running request per interview. A successful response is stored before delivery. Exceptions and disconnections end local work and preserve history; automatic reconnection is not implemented. In-flight synchronous SDK calls release clients after returning. History queries enforce ownership, and list queries avoid loading full JSON bodies.

Resume uploads, editor drafts and recommendation profiles have distinct boundaries. User text remains user data; system-generated section labels and source labels use English. Chinese heading aliases remain valid input. Confirmed recommendation values keep the same units, preprocessing and model contract. The research catalog retains its experimental status and never implies current vacancies.

## Documentation format

Write new implementation comments and documentation in English. Each Python file starts with a module docstring; JavaScript files start with an `@module` header. Include responsibilities, implementation, related modules, a declaration index and a variable index. The checker requires these exact section markers on separate lines:

```python
"""Responsibilities: Describe this file's role and boundaries.
Implementation: Describe the principal algorithm or data flow.
Related Modules: Name collaborators and the interfaces used.

Declaration Index:
- Session: Manage resources owned by one session.
- Session.close: Release the resources owned by that session.

Variable Index:
- LIMIT: Describe the limit, its unit and its purpose.

State:
Session.active indicates whether resources are usable; explain transitions and constraints.
"""
```

Use only real declarations, including nested functions and implemented methods. Qualified names must be locatable, for example `AgentSession.answer` or `AgentTests.test_busy_cancel_and_disconnect_release_session.slow_start`. Imported implementations belong in related-module descriptions. Each entry has the form `- symbol: description`; a description may continue on a following line indented by at least two spaces. Do not retain empty descriptions, duplicate entries or deleted symbols. Use `None` for an empty section. The English contract replaces the previous Chinese markers; there is no parallel legacy format.

The variable index lists module-level assignments and their purposes. Describe class members, instance attributes and important local state near the relevant implementation, including units, ownership and invariants. Do not mechanically index every loop variable.

Functions and methods document functionality, actual inputs, outputs, logic and constraints. Explain relevant exceptions, transaction boundaries, state changes and side effects. A simple helper can use a precise sentence; complex methods need enough detail to verify their contract. Test documentation identifies prerequisites, mocking boundaries and the invariant verified. Comments explain rationale and relationships without invented citations or unsupported performance claims.

JavaScript declaration comments use independent, adjacent, nonempty JSDoc. A module header cannot also document the first declaration. Qualified names distinguish scopes, such as `StreamClient.connect` and `createDeviceSource.cleanup`. Anonymous functions receive `callback1`, `callback2`, etc. in lexical order within their scope; unbound objects use `object1`, etc. Thus `StreamClient.waitFor.callback1.object1.resolve` identifies an object method inside a Promise callback. Numbering does not depend on line numbers. Adding, removing or moving callbacks requires reviewing index descriptions.

Functions bound to variables, static properties or assignment targets use the binding name. Getters/setters add `.get`/`.set`; private methods retain `#`. Internal aliases of function expressions do not create duplicate entries. Dynamic computed names or duplicate qualified names fail inspection. Anonymous default-export classes use a synthetic `callbackN` name.

A JSDoc can precede a function expression directly. When a registration statement has exactly one direct function argument, its JSDoc may precede the whole statement, for example `/** Verify the scenario. */ test("case", () => {});`. Statements with multiple callbacks need separate comments. Empty comments, labels without descriptions and intervening comments fail the adjacency contract.

HTML/CSS headers identify page regions, key elements/selectors and style groups. They explicitly state when there are no function/class declarations. Their semantics, key code blocks, class/member state and comment accuracy require manual review.

## Checks and maintenance

Install documentation dependencies and run checks from `backend/`:

```text
python -m pip install -r requirements-docs.txt
python tools/check_docs.py
python tools/test_check_docs.py
python -m unittest discover -s tools -p "test_*.py"
```

The checker verifies:

- Python declarations in all AST branches have docstrings and exact, nonempty index entries.
- Module assignments have indexed descriptions; duplicate, malformed, empty and stale entries fail.
- JavaScript functions, anonymous callbacks, classes and methods have adjacent JSDoc and qualified index entries, including multiline signatures, object methods, generators, class fields and functions in template interpolations.
- JavaScript module bindings follow syntax scopes rather than indentation. Direct module declarations and top-level-block `var` bindings are included; destructuring counts only bound names. Block-level `let`/`const`, class fields and function locals are documented separately.

A declaration must pass both checks: a declaration comment and a header index entry. Bidirectional comparison detects removed/renamed declarations. Duplicate sections, incorrect separators and missing descriptions also fail. Exit code 0 means no structural problems; exit code 1 means problems were found.

The checker does not import business modules or load `.env`. It excludes environments, third-party dependencies and test outputs before traversal. Python uses the standard AST; JavaScript uses pinned Tree-sitter dependencies. Missing parsers, syntax error recovery and unindexable methods fail explicitly. Structural checks do not prove runtime correctness, comment meaning or commit atomicity. Run language syntax checks and relevant business tests, and manually review behavior descriptions. Python lambdas are not named declarations. Code, comments and indexes must be delivered together; when committed, include them in the same commit.

Checker tests cover the four combinations of declaration/header documentation, nested definitions, stale entries, anonymous callbacks, repeated names in separate scopes, variable ownership, missing JSDoc, syntax errors and missing dependencies. Do not exclude business files or weaken criteria to hide failures.

Implementation references: [Tree-sitter Python API](https://github.com/tree-sitter/py-tree-sitter) and [official JavaScript grammar](https://github.com/tree-sitter/tree-sitter-javascript). The project's additional bidirectional file-index contract is maintained by its Python checker. These links identify implementation references, rather than claiming independent validation of the current change.

## Compatibility boundaries

REST implementations live in `api/`, and streaming implementations live in `streaming/`. Call sites use those paths; obsolete duplicate entry points are removed. Access checks and pending-request cleanup are shared.

Historical migrations remain complete. The legacy diagnostic table created by `0001` is removed by `0003` and is absent from current models. Historical migrations remain so existing databases can upgrade. Translation of migration comments does not authorize rewriting applied schema or seeded data.

Language changes preserve practice/media deadlines, capacity, encoding, scoring, extraction and failure semantics. Agent timing and existing model recovery behavior remain owned by shared MVP modules. English is the default browser interface; explicit Chinese and system preferences remain available. User-authored data and intentional bilingual recognition/test inputs keep their original language.
