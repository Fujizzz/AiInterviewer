import asyncio
from threading import Event

import pytest

from agents.model_calls import current_model_call
from app.providers.llm import LLMError
from evaluation.judge import JudgeDraft, RubricJudge, bind_judgement, validate_judgement
from evaluation.model_calls import EvaluationStageError
from evaluation.rubric import load_rubric_pack
from tests.evaluation.resolver_helpers import assessment, decision, resolve, source
from tests.evaluation.scoring_helpers import draft_for


async def test_judge_sees_only_grounded_evidence_states_and_rubric():
    item = source("a")
    resolution = resolve((item, decision(item)))
    rubric = load_rubric_pack()
    draft = draft_for(resolution, assessment("a", item))

    def model(prompt, payload, schema):
        assert schema is JudgeDraft
        assert set(payload) == {"judge_version", "rubric", "evidence", "states", "conflicts"}
        assert payload["evidence"][0]["quote_spans"][0]["quote"] == item.turn.answer.text
        assert len(payload["rubric"]["rubrics"]) == 6
        assert current_model_call.get().operation == "evaluation_judge"
        assert "chain of thought" in prompt and "untrusted data" in prompt
        return draft

    first = await RubricJudge(model).judge(resolution, rubric=rubric)
    assert validate_judgement(first, resolution, rubric=rubric) == first
    assert len(first.assessments) == 21
    assert sum(a.decision == "included" for a in first.assessments) == 1
    assert first == bind_judgement(
        draft.model_copy(update={"assessments": tuple(reversed(draft.assessments))}),
        resolution,
        rubric=rubric,
    )


@pytest.mark.parametrize(
    "problem",
    [
        "evidence",
        "criterion",
        "anchor",
        "level",
        "score_without_evidence",
        "unscored_level",
        "missing_criterion",
        "duplicate_pair",
        "missing_source",
        "unknown_unmapped",
        "both_unmapped",
        "independent_episodes",
        "blank_rationale",
        "invented_score",
    ],
)
async def test_invalid_judge_output_fails_closed(problem):
    first = source("a")
    second = source("b", "In a second outage I profiled connection waits.")
    resolution = resolve((first, decision(first)), (second, decision(second)))
    rubric = load_rubric_pack()
    draft = draft_for(resolution, assessment("a", first)).model_dump(mode="json")
    value = draft["assessments"][0]
    if problem == "evidence":
        value["evidence_ids"] = ["invented"]
    elif problem == "criterion":
        value["criterion_id"] = "ownership.invented"
    elif problem == "anchor":
        value["matched_anchor_ids"] = ["ownership.personal_contribution.l3"]
    elif problem == "level":
        value["assigned_level"] = 5
    elif problem == "score_without_evidence":
        value["evidence_ids"] = []
    elif problem == "unscored_level":
        value["decision"] = "insufficient"
    elif problem == "missing_criterion":
        draft["assessments"].pop()
    elif problem == "duplicate_pair":
        draft["assessments"].append(value)
    elif problem == "missing_source":
        draft["unmapped_evidence"] = []
    elif problem == "unknown_unmapped":
        draft["unmapped_evidence"][0]["evidence_id"] = "invented"
    elif problem == "both_unmapped":
        draft["unmapped_evidence"].append(
            {**draft["unmapped_evidence"][0], "evidence_id": first.evidence.evidence_id}
        )
    elif problem == "independent_episodes":
        value["evidence_ids"].append(second.evidence.evidence_id)
        draft["unmapped_evidence"] = []
    elif problem == "blank_rationale":
        value["concise_rationale"] = " "
    else:
        value["overall_score"] = 5
    with pytest.raises(EvaluationStageError, match="judge_invalid_"):
        await RubricJudge(lambda *_: draft).judge(resolution, rubric=rubric)


async def test_empty_history_returns_all_gaps_without_a_model_call():
    def unexpected(*args):
        pytest.fail("no evidence must not call the judge")

    result = await RubricJudge(unexpected).judge(resolve(), rubric=load_rubric_pack())
    assert len(result.assessments) == 21
    assert all(
        a.assigned_level is None and a.decision == "insufficient" for a in result.assessments
    )


async def test_forged_source_fails_before_judge_is_called():
    item = source("a")
    resolution = resolve((item, decision(item)))
    damaged_source = item.model_copy(
        update={
            "evidence": item.evidence.model_copy(
                update={
                    "quote_spans": (
                        item.evidence.quote_spans[0].model_copy(update={"quote": "invented"}),
                    ),
                }
            )
        }
    )
    resolution = resolution.model_copy(
        update={
            "history": resolution.history.model_copy(
                update={
                    "sources": (damaged_source,),
                }
            )
        }
    )
    with pytest.raises(EvaluationStageError, match="judge_invalid_evidence"):
        await RubricJudge(lambda *_: pytest.fail("invalid source reached model")).judge(
            resolution,
            rubric=load_rubric_pack(),
        )


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("error", "model_error"),
        ("json", "invalid_output"),
        ("timeout", "timeout"),
        ("cancel", None),
    ],
)
async def test_judge_provider_boundaries(kind, reason):
    item = source("a")

    def model(*args):
        if kind == "cancel":
            raise asyncio.CancelledError()
        if kind == "timeout":
            raise TimeoutError()
        raise LLMError("sensitive", code="invalid_json" if kind == "json" else "model_error")

    with pytest.raises(asyncio.CancelledError if kind == "cancel" else EvaluationStageError) as exc:
        await RubricJudge(model).judge(resolve((item, decision(item))), rubric=load_rubric_pack())
    if reason:
        assert str(exc.value) == f"judge_{reason}"


async def test_judge_deadline_rejects_late_output():
    release, finished = Event(), Event()
    item = source("a")
    resolution = resolve((item, decision(item)))
    scopes = []

    def model(*args):
        scopes.append(current_model_call.get())
        try:
            release.wait(timeout=2)
            return draft_for(resolution, assessment("a", item))
        finally:
            finished.set()

    try:
        with pytest.raises(EvaluationStageError, match="judge_timeout"):
            await asyncio.wait_for(
                RubricJudge(model, timeout_seconds=0.05).judge(
                    resolution,
                    rubric=load_rubric_pack(),
                ),
                timeout=1,
            )
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1)
    assert scopes[0].abandoned


@pytest.mark.parametrize("deadline", [0, -1, float("inf"), float("nan")])
def test_judge_requires_positive_finite_deadline(deadline):
    with pytest.raises(ValueError, match="finite and positive"):
        RubricJudge(None, timeout_seconds=deadline)
