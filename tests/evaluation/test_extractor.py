import pytest
from pydantic import ValidationError

from evaluation.contracts import QuoteSpan
from evaluation.extractor import EvidenceDraft, EvidenceExtraction, EvidenceExtractor
from evaluation.ids import evidence_id
from evaluation.inputs import EvaluationInput
from evaluation.model_calls import EvaluationStageError
from shared.contracts import Competency


async def test_atomic_claims_multispans_and_program_owned_identity(
    phase_two_input, extraction_payload
):
    def model(prompt, data, schema):
        assert schema is EvidenceExtraction
        assert "ONE independently assessable claim" in prompt
        assert "untrusted data" in prompt
        assert "topic" not in data and "analysis" not in data
        assert data["competency_taxonomy"] == [item.value for item in Competency]
        return schema(**extraction_payload)

    items = await EvidenceExtractor(model).extract(phase_two_input)
    assert len(items) == 2
    assert len(items[0].quote_spans) == 2
    assert items[0].evidence_kind == "personal_action"
    assert items[1].evidence_kind == "outcome"
    assert items[1].ownership_scope == "unclear"
    for item in items:
        item.validate_answer(phase_two_input.answer.as_candidate_answer())
        assert item.answer_id == phase_two_input.answer.answer_id
        assert item.question_id == phase_two_input.question.question_id
        assert item.thread_id == phase_two_input.question.thread_id
        assert item.project_id == phase_two_input.question.project_id
        assert item.extraction_version == "extractor-1.1.0"
        assert item.relation == "new" and not item.related_evidence_ids
        assert item.independence_group_id is None


@pytest.mark.parametrize(
    "problem", ["question", "resume", "history", "offset", "overlap", "reverse"]
)
async def test_invalid_evidence_fails_whole_batch_without_repair(
    phase_two_input, extraction_payload, problem
):
    calls = []
    draft = extraction_payload["evidence"][1]
    if problem in {"question", "resume", "history"}:
        quote = {
            "question": phase_two_input.question.text,
            "resume": "Reduced GPU memory usage",
            "history": "I did not change the model weights.",
        }[problem]
        draft["quote_spans"] = [dict(quote=quote, segment_id="answer-current:s0")]
    elif problem == "offset":
        draft["quote_spans"][0]["segment_id"] = "historical-answer:s0"
    elif problem == "overlap":
        draft["quote_spans"] *= 2
    else:
        extraction_payload["evidence"][0]["quote_spans"].reverse()

    def model(prompt, data, schema):
        calls.append(data)
        return schema(**extraction_payload)

    with pytest.raises(EvaluationStageError, match="extractor_invalid_evidence"):
        await EvidenceExtractor(model).extract(phase_two_input)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("normalized_claim", "  "),
        ("score", 5),
        ("competency", "ownership"),
        ("evidence_id", "injected"),
        ("relation", "duplicate"),
        ("answer_id", "history"),
        ("quote_spans", []),
    ],
)
def test_draft_rejects_empty_claims_and_model_owned_metadata(extraction_payload, field, value):
    with pytest.raises(ValidationError):
        EvidenceDraft.model_validate({**extraction_payload["evidence"][0], field: value})


async def test_duplicate_items_are_rejected(phase_two_input, extraction_payload):
    extraction_payload["evidence"].append(extraction_payload["evidence"][0])
    with pytest.raises(EvaluationStageError, match="invalid_evidence"):
        await EvidenceExtractor(lambda prompt, data, schema: schema(**extraction_payload)).extract(
            phase_two_input
        )


async def test_unicode_whitespace_offsets_are_preserved(phase_two_request):
    text = "序🙂 我排查\n 日志。"
    quote = " 我排查\n"
    phase_two_request.answer.text = text
    draft = dict(
        quote_spans=[dict(quote=quote, segment_id="answer-current:s0")],
        normalized_claim="我排查日志",
        evidence_kind="personal_action",
        ownership_scope="personal",
        factuality="reported_experience",
        specificity="partial",
    )
    context = EvaluationInput.from_request(phase_two_request)
    items = await EvidenceExtractor(lambda prompt, data, schema: schema(evidence=[draft])).extract(
        context
    )
    assert "".join(s.quote for s in items[0].quote_spans) == quote
    assert items[0].quote_spans[0].char_start == 2
    assert items[0].quote_spans[-1].char_end == 7


async def test_repeated_quote_requires_unambiguous_source(phase_two_request):
    phase_two_request.answer.text = "I implemented a cache. I implemented a cache."
    draft = dict(
        quote_spans=[dict(quote="I implemented a cache.", segment_id="answer-current:s0")],
        normalized_claim="Implemented a cache",
        evidence_kind="personal_action",
        ownership_scope="personal",
        factuality="reported_experience",
        specificity="concrete",
    )
    with pytest.raises(EvaluationStageError, match="invalid_evidence"):
        await EvidenceExtractor(lambda p, d, s: s(evidence=[draft])).extract(
            EvaluationInput.from_request(phase_two_request)
        )


async def test_segment_identity_resolves_repeated_text_across_windows(phase_two_request):
    prefix = "I implemented a cache."
    phase_two_request.answer.text = prefix + " " * (599 - len(prefix)) + "\n" + prefix
    draft = dict(
        quote_spans=[dict(quote="I implemented a cache.", segment_id="answer-current:s1")],
        normalized_claim="Implemented a cache",
        evidence_kind="personal_action",
        ownership_scope="personal",
        factuality="reported_experience",
        specificity="concrete",
    )
    items = await EvidenceExtractor(lambda p, d, s: s(evidence=[draft])).extract(
        EvaluationInput.from_request(phase_two_request)
    )
    assert items[0].quote_spans[0].char_start == 600


def test_model_cannot_supply_offsets():
    with pytest.raises(ValidationError):
        EvidenceDraft.model_validate(
            dict(
                quote_spans=[dict(quote="action", segment_id="a:s0", char_start=0, char_end=6)],
                normalized_claim="action",
                evidence_kind="personal_action",
                ownership_scope="personal",
                factuality="reported_experience",
                specificity="concrete",
            )
        )


async def test_replay_and_item_order_do_not_change_evidence_ids(
    phase_two_input, extraction_payload
):
    extractor = EvidenceExtractor(lambda prompt, data, schema: schema(**extraction_payload))
    first = await extractor.extract(phase_two_input)
    extraction_payload["evidence"].reverse()
    replay = EvaluationInput.model_validate_json(phase_two_input.model_dump_json())
    replay = replay.model_copy(update={"request_id": "new-request"})
    second = await extractor.extract(replay)
    assert {item.evidence_id for item in first} == {item.evidence_id for item in second}
    assert first[0] == second[1]


def test_evidence_identity_v1_has_fixed_canonical_encoding():
    # Pin a public identity recipe, independent of Python hash randomization/dict ordering.
    value = evidence_id(
        answer_id="a",
        normalized_claim="个人动作",
        evidence_kind="personal_action",
        quote_spans=(QuoteSpan(quote="🙂", char_start=2, char_end=3),),
    )
    assert value == "evidence-v1-83740f1351c0efcdb5856bfc5ba984e283ad0c057bc7b19487a30f3007ec5b23"


@pytest.mark.parametrize("quote", ["yes", "OK", "CNN", "不知道", "拒绝回答"])
async def test_non_answer_fragment_cannot_be_laundered_into_evidence(
    phase_two_request, extraction_payload, quote
):
    phase_two_request.answer.text = quote + ". I implemented a cache."
    extraction_payload["evidence"] = [extraction_payload["evidence"][0]]
    extraction_payload["evidence"][0]["quote_spans"] = [
        dict(quote=quote, segment_id="answer-current:s0")
    ]
    with pytest.raises(EvaluationStageError, match="invalid_evidence"):
        await EvidenceExtractor(lambda prompt, data, schema: schema(**extraction_payload)).extract(
            EvaluationInput.from_request(phase_two_request)
        )


@pytest.mark.parametrize(
    "field", ["answer_id", "quote", "offsets", "normalized_claim", "evidence_kind"]
)
def test_each_identity_component_is_significant(field):
    args = dict(
        answer_id="a",
        quote_spans=(QuoteSpan(quote="abc", char_start=0, char_end=3),),
        normalized_claim="An action",
        evidence_kind="personal_action",
    )
    initial = evidence_id(**args)
    if field == "quote":
        args["quote_spans"] = (QuoteSpan(quote="def", char_start=0, char_end=3),)
    elif field == "offsets":
        args["quote_spans"] = (QuoteSpan(quote="abc", char_start=4, char_end=7),)
    else:
        args[field] = "different"
    assert evidence_id(**args) != initial
