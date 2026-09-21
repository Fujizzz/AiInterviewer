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

The initial input includes a project/topic directory, current thread, hard limits,
state summary, latest evaluated question/answer pair and a scoped follow-up brief.
get_project reads parsed
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
| `quality_timeout_seconds` | `20` | Per-review deadline, also inside the total 90 seconds |
| `history_retention` | `50` | Maximum evaluated turns retained in context |
| `history_tool_limit` | `10` | Maximum entries in one history observation |
| `text_char_limit` | `4000` | Per-question/answer text limit in history model input |

The existing generation timeout (30 seconds) bounds each generation call. Validation
failures allow up to three repair attempts (`retries.llm_generation_retries`); after an invalid final, only another
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
The wording-only compatibility mode preserves supplied plan fields. Both ReAct modes
check structure, explicit internal-rule leakage and exact-text repetition before
`question/quality.py` reviews topic alignment, semantic repetition, overloaded requests,
hidden-rule leaks and unsupported factual premises with `prompts/question_quality_v1.md`.
The review receives selected-project resume claims, same-project questions and current-thread
answers only; it does not receive scores, budget metadata or closed-thread answers.
An empty issue list passes. Rejections and review-service errors share three retry attempts.
A service error retries the SAME draft's review, without calling the writer again;
a content rejection sends revision feedback to the writer. Repaired wording is reviewed again.
A failed review never publishes the draft. Auxiliary review notes and decision summaries
are bounded on ingestion, not rejected solely for excess length; codes and IDs remain strict.
For `OVERLOADED_QUESTION`, the reviewer must provide `answer_requests`: one item per
independent answer actually required by the draft. Alternatives/examples for one answer
stay in one item. The gate discards an overload code inconsistent with a single distinct
request; it preserves all other blocking codes. Missing request evidence for an overload
code is an invalid review and retries the same draft. Request extraction remains semantic,
so this consistency check reduces contradictions rather than guaranteeing correctness.
Each valid draft costs one extra model call, with at most four reviews per attempt
(initial review plus three content/service retries). Timeouts still stop early;
all calls share the total deadline. No additional tool calls are forced.
Semantic judgments remain fallible. Legacy generation (`enabled: false`) and deterministic
fallback use structural validation only; they are not marked as semantically reviewed.
Fallback resets the information goal to its actual recovery question (responsibility,
one task, or one implementation step) in the selected project/topic, preserving routing
and budget fields. It no longer extracts architecture/metric keywords from diagnostics.

The CLI's single Markdown record adds concise `提问质量` results/codes and, on rejection,
the draft and bounded revision advice, not full review inputs or reasoning.
Offline fixtures explicitly script semantic verdicts; real-provider
behavior can be checked with `python -m docs.examples.verify_question_quality` (uses
the configured API and writes `output/提问质量真实模型验证.md`).
For a complete real-provider run with the historical preset answers, use
`python -m docs.examples.replay_interview Wang_Shunyao_CV.pdf`. Resume parsing,
question generation/review, answer evaluation and reporting all use real providers;
only candidate answers are scripted. The normal CLI remains `python main.py ...`.
Add `--answers recent` to replay the 05:59 interview's answers. Run
`python -m docs.examples.verify_review_boundaries` for real-provider single-request,
alternative-example and genuine multi-request regressions. Its record is
`output/审查边界真实模型回归.md`.

Decision logs and action traces expose `question_agent_steps` (action, status, result
count) and `question_agent_stop_reason`. They omit raw observations and model reasoning.
Scratch state is local to each generation call, not stored on the service instance.
