"""Check evidence granularity without another semantic adjudication request."""

import pytest

from agents.domain.models import InterviewHistoryEntry
from agents.question.prepared import accepted_review_versions
from agents.question.quality import QuestionQualityGate, offer_only_quote
from app.settings import interview_settings
from tests.agent.integration.test_question_react import seed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quote,kind,overview,blocked",
    [
        ("I checked shapes, dtype, device and temporal alignment.", "summary_only", False, False),
        ("I can explain the external input-contract checks.", "offer_only", False, False),
        ("I can explain the external input-contract checks.", "concrete_answer", False, False),
        ("我可以解释输入接口的校验方法。", "concrete_answer", False, False),
        (
            "I asserted mask.shape[0] == video.shape[0] before inference.",
            "concrete_answer",
            False,
            True,
        ),
        ("我断言掩码帧数等于视频帧数，不相等时拒绝执行。", "concrete_answer", False, True),
        ("I checked shapes, dtype, device and temporal alignment.", "concrete_answer", True, True),
        ("I checked shapes, dtype, device and temporal alignment.", None, False, False),
    ],
)
async def test_answered_repeat_requires_evidence_at_the_requested_detail(
    quote, kind, overview, blocked
):
    repo, _, previous, feedback, answer = await seed()
    previous.text = "How did you validate the input contract?"
    answer.text = quote
    context = await repo.get_interview_context("pipeline-interview")
    context.question_history = [
        InterviewHistoryEntry(question=previous, feedback=feedback, answer=answer)
    ]
    candidate = previous.model_copy(
        update={
            "question_id": "specific-followup",
            "text": "Which input properties did you check?"
            if overview
            else "Describe one specific alignment assertion you implemented before inference.",
        }
    )

    class Reviewer:
        calls = 0

        async def generate_structured(self, *, response_model, **kwargs):
            self.calls += 1
            return response_model(
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote=candidate.text,
                        instruction="Avoid an already answered request",
                        comparison_question_id=previous.question_id,
                        comparison_answer_quote=quote,
                        repeat_relation="already_answered",
                        answer_coverage="direct",
                        answer_evidence_kind=kind,
                    )
                ]
            )

    model = Reviewer()
    review = await QuestionQualityGate(model, interview_settings()).review(candidate, context)
    assert bool(review.issues) is blocked
    assert model.calls == 1


@pytest.mark.parametrize(
    "text",
    [
        "I can explain the input checks. I asserted matching frame counts.",
        "I can explain the checks: I asserted matching frame counts.",
        "我可以解释输入检查。实际断言是两者帧数相等。",
        "I asserted matching frame counts.",
    ],
)
def test_offer_guard_does_not_erase_a_subsequent_actual_explanation(text):
    assert not offer_only_quote(text)


def test_review_prompt_change_preserves_the_existing_prepared_question_bank():
    assert "prepared-v4:9e1b10b390fe37e0" in accepted_review_versions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relation,coverage,kind",
    [
        (None, None, None),
        ("narrower_unanswered_request", "related", "summary_only"),
        ("already_answered", "related", "summary_only"),
    ],
)
async def test_unsubstantiated_repeat_label_is_a_warning_instead_of_review_failure(
    relation, coverage, kind
):
    repo, _, previous, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")

    class Reviewer:
        async def generate_structured(self, *, response_model, **kwargs):
            return response_model(
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote=previous.text,
                        instruction="The checklist omitted the assertion; this follow-up is valid",
                        repeat_relation=relation,
                        answer_coverage=coverage,
                        answer_evidence_kind=kind,
                    )
                ]
            )

    review = await QuestionQualityGate(Reviewer(), interview_settings()).review(previous, context)
    assert review.issues == []
