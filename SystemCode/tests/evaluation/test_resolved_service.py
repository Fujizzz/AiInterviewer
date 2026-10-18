import asyncio
from threading import Event

import pytest

from agents.model_calls import current_model_call
from app.providers.llm import LLMError
from evaluation.analyzer import ConversationAnalysis
from evaluation.extractor import EvidenceExtraction
from evaluation.resolution import ResolutionDraft
from evaluation.service import EvaluationService


def proposals(data, *, independence="new_episode"):
    return {
        "decisions": [
            {
                "evidence_id": item["evidence_id"],
                "relation": "new",
                "independence": independence,
                "concise_rationale": "Concrete incident details.",
            }
            for item in data["current_evidence"]
        ]
    }


def model_for(analysis_payload, extraction_payload, resolver):
    def model(prompt, data, schema):
        if schema is ConversationAnalysis:
            return schema(**analysis_payload)
        if schema is EvidenceExtraction:
            return schema(**extraction_payload)
        return resolver(prompt, data, schema)

    return model


async def test_three_stage_service_publishes_only_resolved_unscored_evidence(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    operations = []

    def resolver(prompt, data, schema):
        assert schema is ResolutionDraft
        operations.append(current_model_call.get().operation)
        value = proposals(data)
        # Different atomic facts from one event share an independence group.
        value["decisions"][1].update(
            independence="same_episode",
            same_episode_as=[value["decisions"][0]["evidence_id"]],
        )
        value["decisions"].reverse()  # Provider order is not authority for graph chronology.
        return schema(**value)

    service = EvaluationService(
        model_for(analysis_payload, extraction_payload, None),
        resolver_llm=resolver,
    )
    first = await service.evaluate_resolved(phase_two_input)
    extraction_payload["evidence"].reverse()
    second = await service.evaluate_resolved(phase_two_input)
    assert first == second
    result = first.evaluation
    assert result.status == "completed" and result.analysis.thread_complete
    assert len({item.independence_group_id for item in result.evidence_items}) == 1
    assert result.score_snapshot is None and result.assessments == ()
    assert first.resolution.history.sources[0].evidence.independence_group_id is None
    assert operations == ["evaluation_resolver"] * 2


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("provider", "model_error"),
        ("json", "invalid_output"),
        ("schema", "invalid_output"),
        ("timeout", "timeout"),
        ("invalid_reference", "invalid_relations"),
    ],
)
async def test_resolver_failure_discards_analysis_and_entire_batch(
    phase_two_input,
    analysis_payload,
    extraction_payload,
    failure,
    reason,
):
    def resolver(prompt, data, schema):
        if failure == "provider":
            raise LLMError("Sensitive provider body")
        if failure == "json":
            raise LLMError("Sensitive invalid JSON", code="invalid_json")
        if failure == "schema":
            return {"decisions": [{"score": 5}]}
        if failure == "timeout":
            raise TimeoutError()
        value = proposals(data)
        value["decisions"][0].update(
            independence="same_episode",
            same_episode_as=["invented"],
        )
        return schema(**value)

    before = phase_two_input.model_dump_json()
    result = await EvaluationService(
        model_for(
            analysis_payload,
            extraction_payload,
            resolver,
        )
    ).evaluate_resolved(phase_two_input)
    assert result.resolution is None
    assert result.evaluation.status == "failed"
    assert result.evaluation.reason_codes == (f"resolver_{reason}",)
    assert not result.evaluation.evidence_items and not result.evaluation.assessments
    assert not result.evaluation.analysis.thread_complete
    assert result.evaluation.score_snapshot is None
    assert "Sensitive" not in result.model_dump_json()
    assert phase_two_input.model_dump_json() == before


async def test_uncertain_independence_does_not_complete_topic(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    model = model_for(
        analysis_payload,
        extraction_payload,
        lambda prompt, data, schema: schema(
            **proposals(data, independence="unresolved"),
        ),
    )
    result = await EvaluationService(model).evaluate_resolved(phase_two_input)
    assert result.evaluation.status == "completed"
    assert not result.evaluation.analysis.thread_complete
    assert all(state.status == "insufficient" for state in result.resolution.states)


async def test_resolver_deadline_discards_late_results(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    release, finished = Event(), Event()
    scopes = []

    def resolver(prompt, data, schema):
        scopes.append(current_model_call.get())
        try:
            release.wait(timeout=2)
        finally:
            finished.set()
        return schema(**proposals(data))

    service = EvaluationService(
        model_for(analysis_payload, extraction_payload, resolver),
        resolver_timeout_seconds=0.05,
    )
    try:
        result = await asyncio.wait_for(service.evaluate_resolved(phase_two_input), timeout=1)
        assert result.evaluation.reason_codes == ("resolver_timeout",)
        serialized = result.model_dump_json()
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1)
    assert scopes[0].abandoned and result.model_dump_json() == serialized


async def test_resolver_cancellation_propagates(
    phase_two_input, analysis_payload, extraction_payload
):
    def resolver(prompt, data, schema):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await EvaluationService(
            model_for(
                analysis_payload,
                extraction_payload,
                resolver,
            )
        ).evaluate_resolved(phase_two_input)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_resolver_deadline_is_configuration_error(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        EvaluationService(None, resolver_timeout_seconds=timeout)


async def test_prior_stage_failure_never_calls_resolver(phase_two_input):
    def failed(prompt, data, schema):
        assert schema is ConversationAnalysis
        raise LLMError("Unavailable")

    result = await EvaluationService(failed).evaluate_resolved(phase_two_input)
    assert result.resolution is None
    assert result.evaluation.reason_codes == ("analyzer_model_error",)
