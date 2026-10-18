"""A missing answer is not evidence of invented experience; new assertions still block."""

import pytest

from agents.config import load_agent_settings
from agents.question.quality import QuestionQualityGate, ReviewEvidenceError
from tests.app.test_evaluation_components import request

SOURCE = "Used FFmpeg streaming Decode–Inference–Encode to control peak memory."


async def adjudicate(text, verdict, basis, assertion):
    question = request().question.model_copy(update={"text": text})
    payload = {
        "candidate_question": text,
        "previous_questions": [],
        "confirmed_intent": {"target": "Describe streaming memory control"},
        "grounding_sources": [{"source_id": "resume:pipeline", "text": SOURCE}],
    }
    disputed = {
        "issues": [
            dict(
                code="UNSUPPORTED_PREMISE",
                instruction="Missing implementation detail",
                question_quote=text,
                repair_action="remove_unfounded_premise",
            )
        ]
    }

    class Model:
        async def generate_structured(self, *, response_model, **kwargs):
            return response_model(
                checks=[
                    dict(
                        issue_index=0,
                        verdict=verdict,
                        relation="unestablished_experience"
                        if verdict == "confirmed"
                        else "established_context",
                        request_quote=text,
                        premise_basis=basis,
                        asserted_fact_quote=assertion,
                        sources=[dict(source_id="resume:pipeline", quote=SOURCE)],
                        reason="Separate established activity from requested implementation detail",
                    )
                ]
            )

    return await QuestionQualityGate(Model(), load_agent_settings())._adjudicate_grounding(
        payload, disputed, question, None, None
    )


@pytest.mark.asyncio
async def test_established_pipeline_can_be_asked_about_before_implementation_is_explained():
    result = await adjudicate(
        "How did you implement the FFmpeg streaming pipeline?", "refuted", "requested_detail", ""
    )
    assert not result.issues


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "basis,assertion,error",
    [
        ("requested_detail", "FFmpeg", "UNSUPPORTED_PREMISE_REQUIRES_NEW_ASSERTION"),
        (
            "new_assertion",
            "How did you implement FFmpeg?",
            "REQUEST_QUOTE_CANNOT_PROVE_UNSUPPORTED_PREMISE",
        ),
        ("new_assertion", "FFmpeg", "ESTABLISHED_FACT_CANNOT_PROVE_UNSUPPORTED_PREMISE"),
    ],
)
async def test_invalid_reviewer_confirmation_cannot_become_a_candidate_rejection(
    basis, assertion, error
):
    with pytest.raises(ReviewEvidenceError) as caught:
        await adjudicate("How did you implement FFmpeg?", "confirmed", basis, assertion)
    assert error in caught.value.errors


@pytest.mark.asyncio
async def test_an_actual_unestablished_mechanism_remains_blocking():
    result = await adjudicate(
        "How did your custom Kafka service achieve exactly-once encoding?",
        "confirmed",
        "new_assertion",
        "your custom Kafka service",
    )
    assert [issue.code for issue in result.issues] == ["UNSUPPORTED_PREMISE"]
