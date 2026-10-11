"""A disputed repeat spends the existing repair budget instead of changing the target."""

from unittest.mock import AsyncMock

import pytest

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.question.quality import QuestionQualityGate, QuestionQualityReview, RepeatAdjudication
from agents.question.react import QuestionAgentResult, ReactQuestionAgent
from tests.agent.integration.test_question_react import decision, seed

DRAFT = "How does your implementation store and access the shared state?"


class RepeatModel:
    def __init__(self, verdict="not_repeat", fabricated_quote=False):
        self.verdict = verdict
        self.fabricated_quote = fabricated_quote
        self.calls = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.calls.append(prompt_name)
        if response_model is RepeatAdjudication:
            prior = payload["comparisons"][0]
            return response_model(
                checks=[
                    {
                        "comparison_question_id": prior["question_id"],
                        "verdict": self.verdict,
                        "relation": "narrower_unanswered_request"
                        if self.verdict == "not_repeat"
                        else "already_answered"
                        if self.verdict == "repeat"
                        else "uncertain",
                        "current_request_quote": DRAFT,
                        "previous_request_quote": prior["text"],
                        "direct_answer_quote": "Invented database"
                        if self.fabricated_quote
                        else prior["answer"],
                        "reason": "Check the requested storage detail against the actual answer",
                        "answer_units": [
                            {
                                "request_quote": DRAFT,
                                "requested_fact": "How to store and access the shared state",
                                "missing_detail": (
                                    "How to store and access the shared state is not described"
                                ),
                                "new_detail_quote": "store and access the shared state",
                            }
                        ]
                        if self.verdict == "not_repeat"
                        else [],
                    }
                ]
            )
        if response_model is QuestionQualityReview:
            prior = payload["previous_questions"][0]
            return response_model(
                issues=[
                    {
                        "code": "SEMANTIC_REPEAT",
                        "instruction": "Resolve the remaining storage need",
                        "question_quote": DRAFT,
                        "repair_action": "narrow_unanswered_request",
                        "comparison_question_id": prior["question_id"],
                        "comparison_question_quote": prior["text"],
                        "comparison_answer_id": prior["answer_id"],
                        "comparison_answer_quote": prior["answer"],
                        "repeat_relation": "already_answered",
                        "actual_request": "State storage",
                    }
                ]
            )
        return response_model.model_validate(decision(text=DRAFT))


async def scenario():
    repo, service, question, feedback, answer = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    question.text = "How did you maintain consistency in the shared state?"
    answer.text = "I validated updates before committing them."
    context.question_history = [
        InterviewHistoryEntry(
            question=question,
            feedback=feedback,
            answer=answer,
        )
    ]
    target = question.model_copy(
        update={
            "question_id": "storage-followup",
            "information_goal": "Explain storage and access",
            "intent": "Explain storage and access",
            "text": None,
            "answer_unit": "mechanism",
        }
    )
    return service, context, target


@pytest.mark.asyncio
async def test_refuted_repeat_keeps_same_draft_and_intent_without_a_rewrite():
    _, context, target = await scenario()
    model = RepeatModel()
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(
        target,
        "",
        interview=context,
    )
    assert result.question.text == DRAFT
    assert result.question.information_goal == target.information_goal
    assert result.repairs_used == 1 and not result.repaired
    assert result.blocking_issues == []
    assert model.calls == ["question_react_v1", "question_quality_v1", "question_repeat_check_v1"]


@pytest.mark.asyncio
async def test_not_repeat_narrative_cannot_override_covered_answer_units():
    _, context, target = await scenario()
    target.text = DRAFT

    class CoveredUnits(RepeatModel):
        async def generate_structured(self, **kwargs):
            output = await super().generate_structured(**kwargs)
            if kwargs["response_model"] is RepeatAdjudication:
                output.checks[0].answer_units[0].missing_detail = ""
                output.checks[0].answer_units[0].previous_answer_quote = context.question_history[
                    0
                ].answer.text
            return output

    gate = QuestionQualityGate(CoveredUnits(), load_agent_settings())
    payload = gate.payload(target, context)
    disputed = (
        await RepeatModel().generate_structured(
            prompt_name="question_quality_v1", payload=payload, response_model=QuestionQualityReview
        )
    ).model_dump()
    result = await gate._adjudicate_repeat(payload, disputed, target, None, None)
    assert result.issues[0].code == "SEMANTIC_REPEAT"
    assert result.issues[0].repeat_relation == "already_answered"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict,fabricated", [("repeat", False), ("repeat", True), ("uncertain", False)]
)
async def test_confirmed_or_unresolved_repeat_never_silently_passes(verdict, fabricated):
    _, context, target = await scenario()
    model = RepeatModel(verdict, fabricated)
    settings = load_agent_settings()
    result = await ReactQuestionAgent(model, settings).generate(target, "", interview=context)
    assert result.question is None
    assert "SEMANTIC_REPEAT" in result.blocking_issues
    assert 1 <= result.repairs_used <= settings.retries.llm_generation_retries
    assert model.calls.count("question_repeat_check_v1") <= 2
    assert result.model_calls <= 2 * (settings.retries.llm_generation_retries + 1)


@pytest.mark.asyncio
async def test_unaccepted_model_route_fallback_keeps_controller_target():
    service, context, target = await scenario()
    service._question_agent = AsyncMock()
    service._question_agent.generate.return_value = QuestionAgentResult(
        stop_reason="INVALID_OUTPUT"
    )
    generated, reason = await service._generate_question(
        target,
        "",
        context.candidate_profile.projects[0],
        force_fallback=False,
        interview=context,
        previous_questions=[],
        agent_result=QuestionAgentResult(),
    )
    assert generated.information_goal == target.information_goal
    assert "storage and access" in generated.text
    assert "responsibility" not in generated.text
    assert reason == "POLICY_INTENT_FALLBACK_UNREVIEWED"
