import asyncio
from threading import Event

import pytest

from agents.model_calls import current_model_call
from app.providers.llm import LLMError
from evaluation.analyzer import ConversationAnalysis
from evaluation.extractor import EvidenceExtraction
from evaluation.inputs import EvaluationInput
from evaluation.service import EvaluationService


async def test_separate_models_and_schemas_return_only_unscored_evidence(
    phase_two_input, analysis_payload, extraction_payload
):
    calls = []

    def analyzer(prompt, data, schema):
        assert schema is ConversationAnalysis
        calls.append((prompt, current_model_call.get().operation))
        return schema(**analysis_payload)

    def extractor(prompt, data, schema):
        assert schema is EvidenceExtraction
        calls.append((prompt, current_model_call.get().operation))
        return schema(**extraction_payload)

    result = await EvaluationService(analyzer, extractor_llm=extractor).evaluate(phase_two_input)
    assert result.status == "completed"
    assert result.analysis.thread_complete
    assert len(result.evidence_items) == 2
    assert result.assessments == ()
    assert result.score_snapshot is None
    assert calls[0][0] != calls[1][0]
    assert [call[1] for call in calls] == ["evaluation_analyzer", "evaluation_extractor"]


@pytest.mark.parametrize("stage", ["analyzer", "extractor"])
@pytest.mark.parametrize(
    "failure,reason",
    [
        ("provider", "model_error"),
        ("json", "invalid_output"),
        ("schema", "invalid_output"),
        ("timeout", "timeout"),
    ],
)
async def test_stage_failure_discards_evidence_and_completion(
    phase_two_input, analysis_payload, extraction_payload, stage, failure, reason
):
    original = phase_two_input.model_dump()
    calls = []

    def model(prompt, data, schema):
        current = "analyzer" if schema is ConversationAnalysis else "extractor"
        calls.append(current)
        if current == stage:
            if failure == "provider":
                raise LLMError("Sensitive provider body and candidate text")
            if failure == "json":
                raise LLMError("Invalid JSON", code="invalid_json")
            if failure == "timeout":
                raise TimeoutError("Late model")
            return schema.model_construct(unknown_field="invalid")
        return schema(**(analysis_payload if current == "analyzer" else extraction_payload))

    result = await EvaluationService(model).evaluate(phase_two_input)
    assert result.status == "failed"
    assert result.reason_codes == (f"{stage}_{reason}",)
    assert result.evidence_items == () and result.assessments == ()
    assert result.score_snapshot is None
    assert not result.analysis.thread_complete
    assert not result.analysis.contradictions
    assert "unassessed" in result.analysis.summary
    assert "Sensitive" not in result.model_dump_json()
    assert phase_two_input.model_dump() == original
    assert calls == (["analyzer"] if stage == "analyzer" else ["analyzer", "extractor"])


async def test_one_invalid_quote_discards_valid_siblings_and_successful_analysis(
    phase_two_input, analysis_payload, extraction_payload
):
    extraction_payload["evidence"][1]["quote_spans"] = [
        dict(quote="invented", segment_id="answer-current:s0")
    ]
    result = await EvaluationService(
        lambda prompt, data, schema: schema(
            **(analysis_payload if schema is ConversationAnalysis else extraction_payload)
        )
    ).evaluate(phase_two_input)
    assert result.status == "failed"
    assert result.reason_codes == ("extractor_invalid_evidence",)
    assert not result.evidence_items
    assert not result.analysis.thread_complete


@pytest.mark.parametrize(
    "answer,status,scope",
    [
        ("yes!", "non_answer", "none"),
        ("OK", "non_answer", "none"),
        ("是的。", "non_answer", "none"),
        ("   ", "non_answer", "none"),
        ("I don't know.", "explicit_unknown", "none"),
        ("不知道", "explicit_unknown", "none"),
        ("I prefer not to answer", "refusal", "none"),
        ("拒绝回答", "refusal", "none"),
        ("CNN", "substantive", "label_only"),
        ("C++", "substantive", "label_only"),
        ("background", "substantive", "label_only"),
    ],
)
async def test_literal_non_answers_override_overconfident_model(
    phase_two_request, phase_two_topic, analysis_payload, answer, status, scope
):
    phase_two_request.answer.text = answer
    calls = []

    def model(prompt, data, schema):
        calls.append(schema)
        return schema(**analysis_payload)

    result = await EvaluationService(model).evaluate(
        EvaluationInput.from_request(phase_two_request, topic=phase_two_topic)
    )
    assert result.analysis.status == status
    assert result.analysis.answer_scope == scope
    assert not result.analysis.thread_complete
    assert not result.evidence_items
    assert calls == [ConversationAnalysis]


@pytest.mark.parametrize(
    "status,scope",
    [
        ("explicit_unknown", "concrete"),
        ("refusal", "concrete"),
        ("non_answer", "concrete"),
        ("substantive", "label_only"),
        ("partial", "none"),
    ],
)
async def test_semantic_non_answers_skip_extraction(
    phase_two_input, analysis_payload, status, scope
):
    analysis_payload.update(status=status, answer_scope=scope)

    def model(prompt, data, schema):
        assert schema is ConversationAnalysis
        return schema(**analysis_payload)

    result = await EvaluationService(model).evaluate(phase_two_input)
    assert result.status == "completed"
    assert not result.evidence_items
    assert not result.analysis.thread_complete


async def test_empty_extraction_cannot_complete_topic(phase_two_input, analysis_payload):
    result = await EvaluationService(
        lambda prompt, data, schema: schema(
            **(analysis_payload if schema is ConversationAnalysis else {"evidence": []})
        )
    ).evaluate(phase_two_input)
    assert result.status == "completed"
    assert not result.evidence_items
    assert not result.analysis.thread_complete


@pytest.mark.parametrize("stage", ["analyzer", "extractor"])
async def test_actual_deadline_abandons_late_worker_without_publishing(
    phase_two_input, analysis_payload, extraction_payload, stage
):
    release, finished = Event(), Event()
    scopes = []

    def model(prompt, data, schema):
        current = "analyzer" if schema is ConversationAnalysis else "extractor"
        if current == stage:
            scopes.append(current_model_call.get())
            try:
                release.wait(timeout=2)
            finally:
                finished.set()
        return schema(**(analysis_payload if current == "analyzer" else extraction_payload))

    service = EvaluationService(model, **{f"{stage}_timeout_seconds": 0.05})
    try:
        result = await asyncio.wait_for(service.evaluate(phase_two_input), timeout=1)
        assert result.status == "failed"
        assert result.reason_codes == (f"{stage}_timeout",)
        serialized = result.model_dump_json()
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1)
    assert scopes and scopes[0].abandoned
    assert result.model_dump_json() == serialized
    assert not result.analysis.thread_complete and not result.evidence_items


@pytest.mark.parametrize("stage", ["analyzer", "extractor"])
async def test_cancellation_propagates(phase_two_input, analysis_payload, stage):
    def model(prompt, data, schema):
        current = "analyzer" if schema is ConversationAnalysis else "extractor"
        if current == stage:
            raise asyncio.CancelledError()
        return schema(**analysis_payload)

    with pytest.raises(asyncio.CancelledError):
        await EvaluationService(model).evaluate(phase_two_input)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_configuration_error(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        EvaluationService(None, analyzer_timeout_seconds=timeout)
