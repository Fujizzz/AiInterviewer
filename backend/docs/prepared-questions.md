# Prepared resume questions

After a canonical resume profile is saved, preparation starts after the database transaction
commits. Each TopicSelector topic receives one standalone opening question. Each project also
receives one technical principle question when reliable RAG evidence exists. The service reuses
the existing locked-target ReactQuestionAgent, configured quality review and bounded repairs. It
creates no interview, answer or scoring state.

The preparation writer returns question text only. Topic/project/intent IDs remain server-owned;
the model cannot shorten a topic key or retarget the preparation job. Review uses the configured
profile (relaxed by default). Knowledge drafts use neutral technical wording rather than assuming
the candidate implemented the underlying algorithm.

PreparedResumeQuestion stores the resume version, project/topic, content, information goal,
difficulty, references, profile fingerprint, review version and preparation status. Only validated
ready rows matching the current profile and prompt versions can be used. Generation/review failures
are failed; missing reliable knowledge is unavailable. Explicit retries reuse valid ready rows.

QUESTION_PREPARATION_EXECUTION=thread is the default local background mode, with one worker per
process. Upload responses do not wait for question generation. Use celery with the existing
Celery worker for queued execution. disabled suppresses automatic dispatch, while the explicit
preparation service remains usable by isolated tests. Workers have a renewable 15-minute idle
lease. Each result checks the run ID in a short transaction. Interrupted work can be reclaimed by
an explicit retry after expiry. Preparation makes real model calls outside the interview clock.

Authenticated, owner-scoped interfaces:

- GET /api/resume-versions/{id}/prepared-questions/: read progress, questions, references and
  failure statuses without model calls.
- POST /api/resume-versions/{id}/prepare-interview/: reuse/repair the profile and schedule question
  preparation again.
- Resume metadata includes question_preparation_status and question_preparation_error.
  These remain available through the API; the resume page does not display preparation badges.
  The existing preparation retry action remains available for older or incomplete versions.

An interview snapshots the ready questions available at its start. Text can be reused across
interviews, but question/thread IDs are newly allocated. Questions completed after interview
startup become available to the next interview. Before recording a demonstration, use the API
to confirm readiness or inspect the available rows of a partial preparation.

For a new topic, the Agent first checks version, prior use, asked text, difficulty and objective
coverage. When dialogue exists or the live objective differs, one compact PreparedUseDecision
checks semantic repetition, answered coverage and target compatibility. It does not repeat full
standalone quality review. Missing, rejected or unavailable cached content returns to the existing
dynamic generator and full quality gate. Recovery after a failed target also checks the replacement
topic's cached opening first. Its compact admission shares the current turn's three-check limit;
if unavailable, rejected or out of checks, recovery uses the local template without restarting
generation. An accepted cached question retains its reviewed goal in the recorded information need.
Timed-out or failed follow-ups enter this same recovery flow instead of templating the old goal.
Dynamic follow-ups retain their original path. Candidate stop boundaries and time/question limits
always take precedence. Known v4 banks from before the live repeat-evidence clarification remain
accepted; the change does not require regenerating already reviewed standalone questions.

A prepared RAG question uses its saved references without querying again. Its decision records
PREPARED_QUESTION and a cache ID; it must not be presented as a live retrieval. Without a usable
cache, the existing live RAG branch remains best effort. Projects without relevant documents are
not guaranteed a RAG question. The local database currently indexes only two papers; restoring the
team corpus is a separate operation described in rag/docs/TEAM_INTEGRATION.md.

Commands from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/agent/integration/test_prepared_questions.py
# Paid, limited check: one CV ReAct topic, its opening/RAG question, DB reuse and live admission.
# Isolated SQLite and scripted feedback; no scoring API or full interview.
.\.venv\Scripts\python.exe backend/tests/run_prepared_question_smoke.py
```
