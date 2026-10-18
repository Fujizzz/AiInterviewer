"""Corrections and unavailable evaluations preserve canonical evidence and state."""

import pytest

from agents.domain.errors import InvalidAgentState
from agents.domain.models import InterviewHistoryEntry
from agents.evidence import apply_evidence
from agents.orchestrator import InterviewAgentService
from shared.contracts import AnswerAnalysis, CandidateAnswer, Competency, EvaluationFeedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository
from tests.app.test_dialogue_evaluation import dimension


async def initialized():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    context = await repository.get_interview_context("pipeline-interview")
    return repository, service, question, context


def answer_for(question, identifier, text):
    return CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id=identifier,
        text=text,
    )


def feedback_for(question, identifier, **kwargs):
    return EvaluationFeedback(
        request_id=identifier,
        question_id=question.question_id,
        answer_relevance=0.9,
        evidence_strength=0.7,
        analysis=kwargs.pop(
            "analysis",
            AnswerAnalysis(
                status="substantive",
                answer_scope="concrete",
                new_information=True,
            ),
        ),
        **kwargs,
    )


def add_earlier(context, question, answer, evidence):
    feedback = feedback_for(question, "feedback-" + answer.answer_id, dimensions=evidence)
    context.question_history.append(
        InterviewHistoryEntry(question=question, answer=answer, feedback=feedback)
    )
    apply_evidence(context, question, answer, feedback)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,status",
    [
        ("supersedes", "superseded"),
        ("disputes", "disputed"),
        ("clarifies", "active"),
    ],
)
async def test_grounded_relation_retires_only_matching_evidence_and_rebuilds_scores(kind, status):
    _, _, question, context = await initialized()
    earlier = answer_for(
        question, "earlier", "We stored state in a database. I measured p95 using load tests."
    )
    add_earlier(
        context,
        question,
        earlier,
        [
            dimension("technical_depth", "We stored state in a database.", level=2),
            dimension("evaluation", "I measured p95 using load tests.", level=4),
        ],
    )
    current = answer_for(question, "current", "I correct that: we stored state in memory.")
    feedback = feedback_for(
        question,
        "correction",
        analysis=AnswerAnalysis(
            status="substantive",
            answer_scope="concrete",
            new_information=True,
            answer_relations=[
                {
                    "kind": kind,
                    "earlier_answer_id": earlier.answer_id,
                    "earlier_quote": "We stored state in a database.",
                    "current_quote": "we stored state in memory.",
                    "explanation": "The candidate corrected the storage mechanism.",
                }
            ],
        ),
    )
    apply_evidence(context, question, current, feedback)

    technical, evaluation = context.evidence_records
    assert technical.status == status
    assert evaluation.status == "active"
    assert context.state.competencies[Competency.EVALUATION].score == 4
    technical_state = context.state.competencies[Competency.TECHNICAL_DEPTH]
    assert technical_state.score == (2 if kind == "clarifies" else None)
    assert technical_state.evidence_count == (1 if kind == "clarifies" else 0)
    assert context.answer_relations[0]["validation_status"] == "grounded"
    assert context.answer_relations[0]["current_answer_id"] == current.answer_id
    assert bool(technical.relation_id) == (kind != "clarifies")


@pytest.mark.asyncio
async def test_unresolved_conflict_freezes_both_values_but_keeps_unrelated_evidence():
    _, _, question, context = await initialized()
    old = answer_for(question, "old", "All frames share one ROI. I measured peak memory.")
    add_earlier(
        context,
        question,
        old,
        [
            dimension("technical_depth", "All frames share one ROI.", level=3),
            dimension("evaluation", "I measured peak memory.", level=3),
        ],
    )
    new = answer_for(question, "new", "Each frame has its own ROI.")
    feedback = feedback_for(
        question,
        "conflict",
        dimensions=[dimension("technical_depth", new.text, level=4)],
        analysis=AnswerAnalysis(
            status="substantive",
            answer_scope="concrete",
            new_information=True,
            answer_relations=[
                dict(
                    kind="disputes",
                    earlier_answer_id="old",
                    earlier_quote="All frames share one ROI.",
                    current_quote=new.text,
                    explanation="Same window, incompatible values.",
                )
            ],
        ),
    )
    apply_evidence(context, question, new, feedback)
    assert all(
        e.status == "disputed"
        for e in context.evidence_records
        if e.evidence.competency == Competency.TECHNICAL_DEPTH
    )
    assert context.state.competencies[Competency.TECHNICAL_DEPTH].score is None
    assert context.state.competencies[Competency.EVALUATION].score == 3


@pytest.mark.asyncio
async def test_correction_can_match_secondary_quote_and_new_evidence_scores_once():
    _, _, question, context = await initialized()
    first = "We bounded the worker queue."
    second = "We did not deduplicate requests."
    earlier = answer_for(question, "old", first + " " + second)
    evidence = dimension("technical_depth", first, level=2)
    evidence.source_quotes = [first, second]
    add_earlier(context, question, earlier, [evidence])
    current = answer_for(question, "new", "I correct that: request IDs prevented duplicates.")
    replacement = dimension("technical_depth", current.text, level=4)
    feedback = feedback_for(
        question,
        "correction",
        dimensions=[replacement],
        analysis=AnswerAnalysis(
            status="substantive",
            answer_scope="concrete",
            new_information=True,
            answer_relations=[
                {
                    "kind": "supersedes",
                    "earlier_answer_id": "old",
                    "earlier_quote": second,
                    "current_quote": current.text,
                    "explanation": "Explicit correction.",
                }
            ],
        ),
    )
    apply_evidence(context, question, current, feedback)
    assert len(context.evidence_records) == 2
    assert context.evidence_records[0].status == "superseded"
    assert context.evidence_records[1].status == "active"
    state = context.state.competencies[Competency.TECHNICAL_DEPTH]
    assert state.evidence_count == state.independent_evidence_count == 1
    assert state.score == 4
    assert state.coverage == pytest.approx(0.14)


@pytest.mark.asyncio
async def test_service_rejects_cross_project_correction_without_partial_commit():
    repository, service, question, context = await initialized()
    old_question = question.model_copy(update={"project_id": "other-project"})
    earlier = answer_for(old_question, "old", "We stored the shared state in a database.")
    context.question_history.append(
        InterviewHistoryEntry(
            question=old_question,
            answer=earlier,
            feedback=feedback_for(old_question, "old-feedback"),
        )
    )
    repository.contexts[context.interview_id] = context
    current = answer_for(question, "new", "I correct that: we stored it in memory.")
    feedback = feedback_for(
        question,
        "new-feedback",
        analysis=AnswerAnalysis(
            answer_relations=[
                {
                    "kind": "supersedes",
                    "earlier_answer_id": "old",
                    "earlier_quote": earlier.text,
                    "current_quote": current.text,
                    "explanation": "Invalid cross-project correction.",
                }
            ],
        ),
    )
    before = context.model_copy(deep=True)
    with pytest.raises(InvalidAgentState, match="same-project"):
        await service.apply_evaluation_feedback(context.interview_id, feedback, answer=current)
    assert await repository.get_interview_context(context.interview_id) == before


@pytest.mark.asyncio
async def test_unavailable_analysis_does_not_count_as_no_information_or_lower_difficulty():
    repository, service, question, context = await initialized()
    context.thread_difficulty = 4
    context.active_thread.no_information_count = 1
    question.difficulty = 4
    repository.questions[question.question_id] = question
    candidate_answer = answer_for(question, "unassessed", "I traced the lock ordering problem.")
    feedback = feedback_for(
        question,
        "failed-analysis",
        analysis_status="unavailable",
        assessment_status="unavailable",
        analysis=AnswerAnalysis(status="partial", new_information=False, thread_complete=True),
    )
    # Test the service's feedback transition before the next-topic route creates a new thread.
    updated = await service._context_after_feedback(
        context,
        feedback,
        elapsed_seconds=30,
        answer=candidate_answer,
    )
    assert updated.thread_difficulty == 4
    assert updated.active_thread.no_information_count == 1
    assert updated.unassessed_answer_ids == [candidate_answer.answer_id]
    assert updated.state.elapsed_seconds == context.state.elapsed_seconds + 30
    assert not updated.evidence_records


@pytest.mark.asyncio
async def test_unavailable_assessment_cannot_publish_evidence_or_phantom_evidence_ids():
    _, service, question, context = await initialized()
    candidate_answer = answer_for(question, "unassessed", "I traced the lock ordering problem.")
    feedback = feedback_for(
        question,
        "failed-assessment",
        assessment_status="unavailable",
        dimensions=[dimension("debugging", candidate_answer.text, level=4)],
        evidence_ids=["invalid-assessment-evidence"],
    )
    updated = await service._context_after_feedback(
        context,
        feedback,
        elapsed_seconds=0,
        answer=candidate_answer,
    )
    assert updated.evidence_records == []
    assert updated.state.evidence_ids == context.state.evidence_ids
    assert updated.unassessed_answer_ids == [candidate_answer.answer_id]
    assert updated.state.competencies[Competency.DEBUGGING].score is None


@pytest.mark.asyncio
async def test_replaying_correction_feedback_cannot_repeat_retirement_or_time():
    repository, service, question, context = await initialized()
    earlier = answer_for(question, "old", "We stored the state in a database.")
    add_earlier(context, question, earlier, [dimension("technical_depth", earlier.text, level=2)])
    repository.contexts[context.interview_id] = context
    current = answer_for(question, "new", "I correct that: we stored the state in memory.")
    feedback = feedback_for(
        question,
        "corrected-feedback",
        dimensions=[dimension("technical_depth", current.text, level=4)],
        analysis=AnswerAnalysis(
            status="substantive",
            answer_scope="concrete",
            new_information=True,
            answer_relations=[
                {
                    "kind": "supersedes",
                    "earlier_answer_id": "old",
                    "earlier_quote": earlier.text,
                    "current_quote": current.text,
                    "explanation": "Explicit correction.",
                }
            ],
        ),
    )
    first = await service.apply_evaluation_feedback(
        context.interview_id,
        feedback,
        answer=current,
        elapsed_seconds=60,
    )
    before = await repository.get_interview_context(context.interview_id)
    second = await InterviewAgentService(repository=repository).apply_evaluation_feedback(
        context.interview_id,
        feedback,
        answer=current,
        elapsed_seconds=60,
    )
    after = await repository.get_interview_context(context.interview_id)
    assert first == second
    assert before == after
    assert len(after.answer_relations) == 1
    assert after.state.elapsed_seconds == context.state.elapsed_seconds + 60
    assert after.state.competencies[Competency.TECHNICAL_DEPTH].score == 4


@pytest.mark.asyncio
async def test_unassessed_ledger_survives_restart_history_eviction_and_duplicate_feedback():
    repository, service, question, context = await initialized()
    candidate_answer = answer_for(question, "unassessed", "I traced the lock ordering problem.")
    feedback = feedback_for(
        question,
        "unavailable-feedback",
        analysis_status="unavailable",
        assessment_status="unavailable",
    )
    first = await service.apply_evaluation_feedback(
        context.interview_id,
        feedback,
        answer=candidate_answer,
        elapsed_seconds=30,
    )
    saved = await repository.get_interview_context(context.interview_id)
    assert saved.unassessed_answer_ids == [candidate_answer.answer_id]
    # A persistence round trip and short-history eviction cannot erase the system status.
    saved = type(saved).model_validate_json(saved.model_dump_json())
    saved.question_history = []
    repository.contexts[context.interview_id] = saved
    second = await InterviewAgentService(repository=repository).apply_evaluation_feedback(
        context.interview_id,
        feedback,
        answer=candidate_answer,
        elapsed_seconds=30,
    )
    after = await repository.get_interview_context(context.interview_id)
    assert second == first
    assert after == saved
    assert after.unassessed_answer_ids == [candidate_answer.answer_id]
    assert after.state.elapsed_seconds == context.state.elapsed_seconds + 30
