# Lightweight interview review

CLI, backend and resume-question preparation use `QUESTION_QUALITY_MODE=relaxed` by
default through `app.settings.interview_settings`. `strict` keeps the previous evidence
and adjudication path for contract regressions. The core settings loader remains strict;
direct integrations should use the application settings resolver or set the mode explicitly.

The relaxed path uses a short writer prompt and a small review schema. It blocks material
repetition, invented experience/results, unrelated targets, actual answer disclosure,
internal-rule leakage and three or more independent required outputs. Relevant subgoals,
design rationale, neutral examples and minor introductory wording are accepted. It performs
no additional grounding/repetition adjudication. Invalid or unavailable reviews are never
represented as a successful review and are not recursively repaired.

An `already_answered` finding must include an exact prior-answer quote and explicit
`answer_coverage=direct`. Related implementation context is not proof that a testing,
measurement or failure-handling follow-up was answered. Missing or unsupported repeat
evidence becomes a recorded warning rather than a blocking issue; no extra adjudication
request is sent. A new unanswered detail remains valid, while directly covered details
and repeated requests for explicitly unavailable facts are still blocked.

One outgoing turn shares at most three review requests across prepared-question admission,
dynamic reviews and target changes. The limit includes the first review. There are no hidden
provider JSON repairs for these calls or for the foreground CompactAnswerDecision. A timeout
does not trigger an identical retry. Specific valid rejection feedback may trigger a rewrite
while review budget remains. Budget exhaustion prevents even starting another draft.

Single-call review timeout is 10 seconds; foreground answer analysis is 20 seconds. The
existing generation and total ReAct guards remain. Provider HTTP timeout uses the remaining
execution deadline. Cancelling a local wait cannot guarantee server-side work or billing stops.

Prepared content is checked before dynamic generation. A follow-up that times out or fails to
produce an accepted question never uses an unreviewed same-target template, whether the model
had locked its intent or not. The controller records the failed scope without marking its
evidence sufficient, selects another scope and first tries its cached opening within the
remaining shared review budget. If unavailable, it uses the replacement scope's local template
without restarting generation. New-topic opening failures can still use a constrained local
question unless a repeat/unsupported intent requires recovery. Failed analysis preserves old
objective evidence and produces no fabricated completion or score. These failures alone do not
finish an interview. Normal time, explicit user stop and question ceilings still apply.

Inputs are role-specific: review receives each related answer once, a small resume source
view and an older-question index. The writer receives one latest turn, related older answers,
available route IDs and selected resume facts. Analysis focuses on the current goal plus at
most two explicitly related goals. Older accepted evidence remains available as original
segment references for contradiction checks; omitted goals keep their existing coverage.
Full durable interview history is unaffected.

## Scoped unavailable details

Foreground analysis now returns `needs` in the same model request. These are executable
open details and blocked unavailable details, separate from evidence-coverage gaps. The
adapter restores source quotes from current answer segment IDs. Every validated limitation
segment is also persisted as an exact boundary when the model omits a blocked entry; a fact
does not have to be correctly repeated in two output fields to survive into the next turn.

`TopicProgress.needs_assessed` switches new analyzed topics to this explicit inventory.
Coverage and local budget compilation no longer manufacture open needs from all missing
evidence. Blocked targets retain their source evidence across later answers, serialization
and objective versions. The latest-turn view and follow-up brief expose only executable open
targets, with unavailable boundaries provided separately. A partial answer can therefore
block private layer layouts while leaving pipeline checks askable. With no askable details,
the controller defers the topic without pretending its evidence is sufficient, then selects
another scope. Candidate-supplied new facts can still contribute coverage normally.

A repair cannot silently substitute a different target by reusing a `need_id`; it must
preserve the confirmed goal or request retargeting. No additional model adjudicator, retry
or timeout increase is introduced. Older contexts/legacy feedback keep the compatibility
path until explicit need classification is available. These additions use existing JSON
state storage and require no new database migration.

AI Security is disabled by default (`AI_SECURITY_ENABLED=false`) and is not part of this
review flow or its tests. The old module is retained for compatibility, without model calls
when disabled. No change to scoring/Shadow is made here.

Newly prepared questions carry the new review-profile version. Questions approved by the
unchanged previous strict gate remain compatible, with profile ownership/fingerprint checks
intact; this avoids regenerating an existing ready bank just to use the lighter gate. Other
stale versions fall back to normal generation rather than being silently approved.

## Compact continuation decisions

The real-time adapter now sends only the active objective and at most one explicitly
related objective. Current answers stay complete. The two latest answers in the current
thread, evidence supporting the selected objectives, and a small set of matching older
segments supply history; unrelated project evidence is not repeatedly transmitted.

Request-local `s`, `h`, and `c` aliases replace durable answer/criterion IDs. The model
returns criterion deltas rather than repeating objective-level coverage, per-criterion
coverage and the same missing-information prose. The server restores original IDs/quotes
and uses the existing completion projection and grounding validation. Invalid criterion
observations are isolated and reported without discarding valid dialogue analysis.

Need IDs never cross this model boundary. Exact existing targets retain their registered
IDs, and changed targets are registered separately. Initial broad question goals are not
presented as already classified open details. Only assessed open needs and durable blocked
boundaries are supplied as that inventory. `limits` and `relations` are required outputs;
omitting these critical checks is not treated as proof of no limitation or contradiction.
An exclusive changed claim without an explicit correction remains a dispute. Annotated
segment IDs can be restored only if both the alias and its attached exact quote match source.

Cache admission keeps the latest question with its answer, up to two relevant earlier
answers, same-project question texts and current unavailable details. The question supplies
the scope of a limitation: unknown private model internals do not invalidate an ROI question.
It returns a boolean and, on rejection, a short reason code. Verbose reason text is truncated
locally instead of failing an otherwise valid verdict. Preparation version hashes and
pre-reviewed content compatibility are unchanged.

No additional API call or retry is introduced. Analysis remains at the existing 20-second
protection and cache checks at 10 seconds. Transient failure still preserves prior evidence
and uses the normal continuation/recovery path. Capability grading and Shadow remain separate.

An explicit, source-grounded private-layer/channel boundary also constrains paraphrased
internal-fusion needs locally. A tensor-operation/concatenation/DiT-internal target is blocked
using the original current source, or omitted when the boundary is from an earlier answer.
Input-contract and validation details stay askable. Lack of online statistics does not activate
this private-model rule. This prevents a fast but inconsistent model response from routing the
writer straight back to the unavailable implementation; it adds no model or review request.
