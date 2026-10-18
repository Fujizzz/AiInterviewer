"""Compression keeps original proof and boundaries while omitting unrelated state."""

import json
from types import SimpleNamespace

import pytest

from app.adapters.decision import CompactAnswerDecision
from app.adapters.decision_context import DecisionContext
from app.adapters.evaluation import (
    InvalidEvaluationEvidence,
    agenda_objectives,
    answer_segments,
)
from shared.contracts.planning import InformationNeed, TopicProgress
from tests.app.test_objective_coverage import evaluation_request, objective_repository


def view_for(repo=None, request=None):
    repo, request = repo or objective_repository(), request or evaluation_request()
    return DecisionContext(
        request,
        repo.context,
        answer_segments(request.answer.answer_id, request.answer.text),
        agenda_objectives(repo.context, request.question.project_id),
        [],
    )


def test_short_aliases_restore_original_criterion_and_answer_ids():
    req = evaluation_request()
    req.answer.answer_id = "550e8400-e29b-41d4-a716-446655440000"
    view = view_for(request=req)
    result = view.expand(
        CompactAnswerDecision(
            limits=[],
            relations=[],
            status="substantive",
            scope="concrete",
            new=True,
            criteria=[dict(id="c0", status="sufficient", s=["s0"])],
        )
    )
    assert req.answer.answer_id not in json.dumps(view.payload)
    criterion = result.coverage[0].criteria[0]
    assert criterion.criterion_id.startswith("criterion:")
    assert criterion.segments == [req.answer.answer_id + ":s0"]
    assert result.coverage[0].objective_id == "state"


def test_input_keeps_complete_current_answer_and_relevant_old_proof_only():
    repo, req = objective_repository(), evaluation_request()
    req.answer.text = (
        "I implemented state fields. " * 150 + "I cannot provide private layer counts."
    )
    old_text = "I stored the version in the transaction record."
    unrelated = "UNRELATED_ARCHIVED_MEASUREMENTS"
    repo.context.question_history = [
        SimpleNamespace(
            question=req.question.model_copy(update={"thread_id": "old"}),
            answer=req.answer.model_copy(update={"answer_id": "old", "text": text}),
        )
        for text in [old_text, unrelated]
    ]
    repo.context.question_history[1].answer.answer_id = "unrelated"
    for key, text, aid in [("state", old_text, "old"), ("latency", unrelated, "unrelated")]:
        repo.context.topic_progress[key].coverage_evidence = [
            dict(
                answer_id=aid,
                supporting_quotes=[text],
                supporting_segment_ids=[aid + ":s0"],
            )
        ]
    view = view_for(repo, req)
    assert [s["text"] for s in view.payload["answer_segments"]] == [
        s["text"] for s in answer_segments(req.answer.answer_id, req.answer.text)
    ]
    body = json.dumps(view.payload)
    assert old_text in body and unrelated not in body
    assert "I cannot provide private layer counts." in body


@pytest.mark.parametrize("field", ["invented", "retarget"])
def test_unknown_or_retargeted_need_alias_cannot_change_registered_target(field):
    from pydantic import ValidationError

    repo = objective_repository()
    repo.context.topic_progress["state"] = TopicProgress(
        needs_assessed=True,
        information_needs=[
            InformationNeed(
                need_id="durable-need",
                objective_id="state",
                target="Input validation checks",
            )
        ],
    )
    view = view_for(repo)
    # IDs never cross the model boundary. A changed target receives a new ID.
    with pytest.raises(ValidationError):
        CompactAnswerDecision(
            limits=[],
            relations=[],
            status="partial",
            needs=[
                dict(
                    id="invented" if field == "invented" else "n0",
                    target="Private layer counts",
                    status="open",
                )
            ],
        )
    valid = view.expand(
        CompactAnswerDecision(
            limits=[],
            relations=[],
            status="partial",
            needs=[
                dict(
                    target="Input validation checks",
                    status="open",
                )
            ],
        )
    )
    assert valid.needs[0].need_id == "durable-need"
    changed = view.expand(
        CompactAnswerDecision(
            limits=[],
            relations=[],
            status="partial",
            needs=[
                dict(
                    target="Other detail",
                    status="open",
                )
            ],
        )
    )
    assert changed.needs[0].need_id == ""


def test_coverage_alias_failure_does_not_discard_valid_need_or_conversation():
    view = view_for()
    result = view.expand(
        CompactAnswerDecision(
            limits=[],
            relations=[],
            status="partial",
            scope="concrete",
            criteria=[dict(id="invented", status="sufficient", s=["s0"])],
            needs=[dict(target="Validation procedure", status="open")],
        )
    )
    assert result.analysis.status == "partial" and not result.coverage
    assert result.needs[0].target == "Validation procedure"
    assert view.issues == ["INVALID_COMPLETION_CRITERION_ID"]


def test_echoed_unassessed_criterion_is_a_noop_not_an_analysis_failure():
    view = view_for()
    result = view.expand(
        CompactAnswerDecision(
            status="partial",
            limits=[],
            relations=[],
            criteria=[dict(id="c0", status="unassessed")],
            needs=[dict(target="Validation procedure", status="open")],
        )
    )
    assert not result.coverage and not view.issues
    assert result.needs[0].target == "Validation procedure"


@pytest.mark.parametrize(
    "boundary",
    [
        "I can explain pipeline checks, but I do not have private layer layouts or channel counts.",
        "我负责管线接入，不掌握精确的内部层结构或通道数，可以解释输入契约和验证过程。",
    ],
)
def test_private_fusion_paraphrase_is_blocked_but_validation_remains_askable(boundary):
    req = evaluation_request()
    req.answer.text = boundary
    view = view_for(request=req)
    result = view.expand(
        CompactAnswerDecision(
            status="partial",
            scope="concrete",
            limits=[],
            relations=[],
            needs=[
                dict(target="Describe the tensor operation before the DiT blocks", status="open"),
                dict(target="Explain input contract and validation checks", status="open"),
            ],
        )
    )
    assert [n.status for n in result.needs] == ["blocked", "open"]
    assert result.needs[0].segments == ["current:s0"]
    assert result.analysis.limitation_segments == ["current:s0"]


def test_lack_of_online_numbers_does_not_block_an_implementation_detail():
    req = evaluation_request()
    req.answer.text = "I have no online statistics but can explain the tensor operation."
    result = view_for(request=req).expand(
        CompactAnswerDecision(
            status="substantive",
            limits=["s0"],
            relations=[],
            needs=[dict(target="Explain the tensor operation", status="open")],
        )
    )
    assert result.needs[0].status == "open"


def test_bad_limitation_reference_does_not_erase_valid_analysis_or_grounded_limits():
    view = view_for()
    result = view.expand(
        CompactAnswerDecision(
            status="partial",
            scope="concrete",
            complete=True,
            limits=["natural language instead of ID", "s0", "s0: invented explanation"],
            relations=[],
            criteria=[dict(id="c0", status="sufficient", s=["s0"])],
            needs=[dict(target="Validation procedure", status="open", s=["s0"])],
        )
    )
    assert result.coverage[0].criteria[0].segments == ["current:s0"]
    assert result.needs[0].target == "Validation procedure"
    assert result.analysis.limitation_segments == ["current:s0"]
    assert not result.analysis.complete
    assert view.issues.count("INVALID_LIMITATION_SEGMENT_OMITTED") == 2


def test_malformed_limit_does_not_turn_explicit_refusal_into_permission():
    view = view_for()
    result = view.expand(
        CompactAnswerDecision(
            status="refusal",
            limits=["I refuse"],
            relations=[],
        )
    )
    assert result.analysis.status == "refusal"
    assert not result.analysis.complete


def test_annotated_alias_requires_a_real_exact_quote_and_unassessed_need_is_not_an_open_fact():
    repo = objective_repository()
    repo.context.topic_progress["state"] = TopicProgress(
        information_needs=[
            InformationNeed(
                need_id="initial",
                objective_id="state",
                target="Broad implementation mechanism",
            )
        ]
    )
    view = view_for(repo)
    assert "information_needs" not in view.payload
    text = view.current["s0"]["text"]
    assert view._segments(["s0: " + text]) == ["current:s0"]
    with pytest.raises(InvalidEvaluationEvidence):
        view._segments(["s0: invented statement"])


@pytest.mark.parametrize("correction", [False, True])
def test_changed_fact_requires_explicit_correction_to_supersede(correction):
    repo, req = objective_repository(), evaluation_request()
    req.answer.text = (
        "I need to correct my earlier answer: each frame has its own ROI."
        if correction
        else "Each frame has its own ROI."
    )
    repo.context.question_history = [
        SimpleNamespace(
            question=req.question,
            answer=req.answer.model_copy(
                update={"answer_id": "old", "text": "The window shares one ROI."}
            ),
        )
    ]
    repo.context.topic_progress["state"].coverage_evidence = [
        dict(
            answer_id="old",
            supporting_quotes=["The window shares one ROI."],
            supporting_segment_ids=["old:s0"],
        )
    ]
    view = view_for(repo, req)
    result = view.expand(
        CompactAnswerDecision(
            limits=[],
            status="substantive",
            relations=[
                dict(
                    old="h0",
                    current="s0",
                    kind="supersedes",
                    compatibility="exclusive",
                )
            ],
        )
    )
    assert result.relations[0].kind == ("supersedes" if correction else "disputes")


def test_cache_admission_keeps_unknown_boundary_and_accepts_verbose_reason():
    from agents.question.compact import prepared_use_payload
    from agents.question.prepared import PreparedUseDecision

    repo, req = objective_repository(), evaluation_request()
    repo.context.introduction_answer = None
    repo.context.topic_progress["state"] = TopicProgress(
        information_needs=[
            InformationNeed(
                need_id="blocked",
                objective_id="state",
                target="Private layer counts",
                status="blocked",
            )
        ]
    )
    repo.context.question_history = [
        SimpleNamespace(
            question=req.question,
            answer=req.answer,
        )
    ]
    item = SimpleNamespace(
        text="Which checks did you use?", kind="opening", information_goal="Input validation"
    )
    payload = prepared_use_payload(item, req.question, repo.context)
    assert payload["latest_turn"]["answer"] == req.answer.text
    assert payload["unavailable_details"] == ["Private layer counts"]
    assert PreparedUseDecision(usable=True).reason == ""
    assert len(PreparedUseDecision(usable=True, reason="x" * 1000).reason) == 160
