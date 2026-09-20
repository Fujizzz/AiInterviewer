# Agent module

Owns bounded model dialogue decisions, validation, and deterministic fallbacks.

## Responsibilities

- conversation continuation, project and topic selection;
- difficulty and probe control;
- question planning, validation and fallback;
- bounded ReAct dialogue decisions with read-only project, history and plan tools;
- state transitions and termination;
- RAG routing policy and bounded context assembly;
- atomic decision logging through `RepositoryPort`.

## Boundary

Other modules should call `InterviewAgentService` or implement a port from `agents/ports/`.
They should not import policy classes, domain persistence models or orchestrator internals.

The Agent module does not own HTTP routes, databases, resume parsing or UI. The application extracts evidence; agents/evidence.py aggregates it inside the atomic turn.

## Project and topic budgets

`policies/dialogue_controller.py` owns both budget checks and durable thread transitions.
Every committed question (initial or follow-up) consumes a project and topic slot.
Defaults: `max_questions_per_project: 4`, `max_questions_per_topic: 3` under `agent`.
A topic reaching its cap permits another unused topic in the same project; a project
reaching its cap excludes all of that project's topics. With no eligible topics the
interview finishes early. Counts derive from active/closed threads, survive history
trimming and restarts, and are committed atomically with questions. Exact normalized
information goals remain blocked across topics within a project; semantic equivalence
is not guaranteed. The legacy CLI follow-up limit N maps to a topic limit of N+1.

## Question generation

The default question agent chooses dialogue action, project, topic and information goal.
The orchestrator prepares a fallback plan but never supplies it to the autonomous model.
question/dialogue.py validates choices and derives IDs, parent/thread links, difficulty
and follow-up depth. Competencies and confidence are absent from question inputs.
`question/react.py` then uses `LLMPort.generate_structured()` to choose one action at a
time: `get_project`, `get_history`, `get_plan`, or `final`. Tool observations are supplied to the next
model call. This is a local action dispatcher; it does not require provider-native
function calling or a new framework. `prompts/question_react_v1.md` defines the policy.

The initial input includes only a project/topic directory, current thread, hard limits,
state summary and latest evaluated question/answer pair. get_project reads parsed
resume details on demand; final includes a structured selection and short factual
justification. It does not return an internal reasoning transcript. `get_history` reads older
retained turns, optionally filtered by topic, and preserves question/answer/conversation-analysis
relationships. `get_plan` reads the overall interview plan. Tools cannot write state or
select another interview. No resume-search or new RAG integration is added in this phase;
the optional RAG interface remains available in the non-autonomous legacy path.

Call `apply_evaluation_feedback(..., answer=candidate_answer)` to make the actual answer
available to the next question. Both the terminal application and backend session do so.
Question snapshots, answers and feedback are saved as `InterviewContext.question_history`
in the same atomic commit as the resulting action, including stage changes and finish.
Retries of a committed feedback request return the saved action without appending history.
Older context JSON loads with an empty history, and callers that omit `answer` retain
question/feedback entries with `answer=null`; old raw answers are not backfilled.
Existing context JSON storage needs no database schema migration. Version 2.0 contexts persist dialogue threads and deduplicated multi-dimensional evidence. Older extra confidence fields are ignored; historical scores are not recomputed. Start a new interview to exercise the new policy.

Defaults under `agent.question_agent` in `config/defaults.yaml`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Set false to use the previous single-pass generator |
| `max_tool_calls` | `2` | Maximum tool attempts per question, including repeated calls |
| `total_timeout_seconds` | `90` | Total ReAct loop deadline, including output repairs |
| `history_retention` | `50` | Maximum evaluated turns retained in context |
| `history_tool_limit` | `10` | Maximum entries in one history observation |
| `text_char_limit` | `4000` | Per-question/answer text limit in history model input |

The existing generation timeout (30 seconds) also bounds each model call. Validation
failures allow the existing one repair attempt; after an invalid final, only another
final is allowed. Identical tool calls produce an error observation without reexecution.
After the tool budget is spent the model must finish; further tool calls, exhausted
repairs, errors or timeouts use the existing deterministic fallback. Cancellation
propagates without committing a generated question. A synchronous provider worker may
continue its in-flight request after the async deadline, but its late result is unused.
Provider HTTP timeouts use the remaining call budget, SDK retries are disabled, and
Question Agent JSON repair is owned by the ReAct loop. Scoped diagnostics record
question/call IDs, phase, step, duration and sanitized error category; late worker
errors do not appear as errors for a later question.

Autonomous finals validate model choices before building a server-owned plan.
The wording-only compatibility mode preserves supplied plan fields. Both check question rules plus
normalized exact-text repetition against available history. These checks do not prove
semantic relevance or factual grounding; those still require quality evaluation with
real interview examples. Fallback retains its existing validation behavior.

Decision logs and action traces expose `question_agent_steps` (action, status, result
count) and `question_agent_stop_reason`. They omit raw observations and model reasoning.
Scratch state is local to each generation call, not stored on the service instance.
