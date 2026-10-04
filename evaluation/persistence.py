"""Append-only evaluation receipts committed with the Agent's state CAS.

Each receipt contains the immutable input, safe/legacy feedback and the complete
scoring record (sources, relation decisions, assessments, snapshots and trace).
The repository exposes reads and atomic append only, never replacement.
"""

from typing import Literal

from pydantic import Field

from evaluation.aggregation import ScoredEvaluation
from evaluation.contracts import EvaluationModel
from evaluation.inputs import EvaluationInput
from evaluation.resolution import ResolutionHistory
from shared.contracts import EvaluationFeedback


class EvaluationRecord(EvaluationModel):
    persistence_version: Literal["evaluation-ledger-1.0.0"] = "evaluation-ledger-1.0.0"
    mode: Literal["shadow", "formal"]
    base_state_version: int = Field(ge=0)
    input: EvaluationInput
    feedback: EvaluationFeedback
    scored: ScoredEvaluation


class EvaluatedFeedback(EvaluationFeedback):
    # A process-local envelope, deliberately absent from shared/UI serialization.
    evaluation_record: EvaluationRecord = Field(exclude=True, repr=False)


def scoring_history(interview_id, records):
    successful = [r for r in records if r.scored.aggregation is not None]
    if not successful:
        return ResolutionHistory(interview_id=interview_id), None
    latest = successful[-1].scored.aggregation
    return latest.inputs.history, latest.snapshot.snapshot_id


def failure_codes(processed_feedback_ids, records):
    """Failures/gaps remain blocking until explicitly reassessed in a later phase."""
    indexed = {r.input.request_id: r for r in records}
    return tuple(
        sorted(
            f"unassessed_feedback:{request_id}"
            for request_id in processed_feedback_ids
            if request_id not in indexed or indexed[request_id].scored.evaluation.status == "failed"
        )
    )


def input_for_request(request, context, records):
    """Build bounded model context from committed order, including rollout gaps."""
    history, _ = scoring_history(request.interview_id, records)
    turns = {
        entry.feedback.request_id: (entry.question, entry.answer)
        for entry in context.question_history
        if entry.answer is not None
    }
    turns.update({r.input.request_id: (r.input.question, r.input.answer) for r in records})
    topic = next(
        (
            t
            for t in context.plan.topics
            if (t.project_id, t.topic_key)
            == (request.question.project_id, request.question.topic_key)
        ),
        None,
    )
    return EvaluationInput.from_request(
        request,
        topic=topic,
        history=[turns[key] for key in context.processed_feedback_ids if key in turns],
        existing_evidence=[s.evidence for s in history.sources],
    )


def validate_record(record, *, stored, prior, question, answer, feedback):
    """Validate provenance and the complete historical prefix before publishing.

    Model revalidation and mathematical replay also reject model_copy/construct
    bypasses. The caller must execute this inside its atomic CAS boundary.
    """
    from agents.domain.errors import InvalidAgentState, StateConflictError
    from evaluation.aggregator import replay_aggregation
    from evaluation.inputs import AnswerSnapshot, QuestionSnapshot
    from evaluation.resolver import replay_resolution
    from shared.contracts import EvaluationRequest

    record = EvaluationRecord.model_validate(record.model_dump())
    if record.base_state_version != stored.state.state_version:
        raise StateConflictError("Evaluation base_state_version is stale; evaluate again")
    current = record.input
    result = record.scored.evaluation
    if (
        current.interview_id != stored.interview_id
        or current.request_id != feedback.request_id
        or current.question != QuestionSnapshot.from_question(question)
        or answer is None
        or current.answer != AnswerSnapshot.from_answer(answer)
        or record.feedback != EvaluationFeedback.model_validate(feedback.model_dump())
        or (result.interview_id, result.request_id, result.question_id, result.answer_id)
        != (current.interview_id, current.request_id, question.question_id, answer.answer_id)
    ):
        raise InvalidAgentState("Evaluation must match the committed question, answer and feedback")
    ids = [r.input.request_id for r in prior]
    if (
        len(ids) != len(set(ids))
        or [key for key in stored.processed_feedback_ids if key in ids] != ids
        or any(r.input.interview_id != stored.interview_id for r in prior)
    ):
        raise InvalidAgentState("Evaluation ledger is inconsistent with committed feedback")
    if current.request_id in ids or any(
        r.input.answer.answer_id == answer.answer_id for r in prior
    ):
        raise StateConflictError("Evaluation request or answer is already committed")
    expected_input = input_for_request(
        EvaluationRequest(
            interview_id=stored.interview_id,
            request_id=current.request_id,
            question=question,
            answer=answer,
        ),
        stored,
        prior,
    )
    if current != expected_input:
        raise InvalidAgentState("Evaluation must use the committed Planner and thread history")
    history, previous = scoring_history(stored.interview_id, prior)
    aggregation = record.scored.aggregation
    if aggregation is not None:
        inputs = aggregation.inputs
        sources = inputs.history.sources
        decisions = inputs.history.decisions
        size = len(history.sources)
        if (
            sources[:size] != history.sources
            or decisions[:size] != history.decisions
            or len(sources) < size
            or any(
                s.turn.question != current.question or s.turn.answer != current.answer
                for s in sources[size:]
            )
            or inputs.supersedes_snapshot_id != previous
            or inputs.reevaluation_reason != ("new_answer" if previous else None)
            or inputs.evaluation_failure_codes
            != failure_codes(stored.processed_feedback_ids, prior)
        ):
            raise InvalidAgentState("Evaluation must extend the complete committed ledger")
        try:
            replay_aggregation(aggregation)
        except ValueError as error:
            raise InvalidAgentState("Evaluation failed deterministic replay") from error
        resolution = replay_resolution(inputs.history)
        if record.scored.resolution != resolution:
            raise InvalidAgentState("Evaluation resolution does not match the ledger")
        current_ids = {s.evidence.evidence_id for s in sources[size:]}
        expected = tuple(e for e in resolution.evidence_items if e.evidence_id in current_ids)
        if result.evidence_items != expected:
            raise InvalidAgentState("Evaluation evidence must match the current ledger append")
    return record


def validate_turn_evaluation(request, stored, prior, question):
    """Repository boundary: no evaluation artifact can be attached to another turn."""
    from agents.domain.errors import InvalidAgentState

    record = request.evaluation_record
    if record is None:
        if request.new_context is not None and request.new_context.pending_evaluation is not None:
            raise InvalidAgentState("Evaluation receipt cannot be omitted from its feedback turn")
        return None
    if request.feedback_request_id != record.input.request_id or request.new_context is None:
        raise InvalidAgentState("Evaluation append requires its atomic feedback turn")
    entries = request.new_context.question_history
    if not entries or entries[-1].feedback.request_id != request.feedback_request_id:
        raise InvalidAgentState("Evaluation append requires the committed answer")
    entry = entries[-1]
    if (
        question is None
        or question != entry.question
        or stored.state.current_question_id != question.question_id
    ):
        raise InvalidAgentState("Evaluation question must be the current immutable question")
    return validate_record(
        record,
        stored=stored,
        prior=prior,
        question=question,
        answer=entry.answer,
        feedback=entry.feedback,
    )
