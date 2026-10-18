"""Unavailable details must not become open again through coverage or wording repairs."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agents.planning.needs import apply_need_updates, open_needs, replace_objective, sync_needs
from agents.question.dialogue import DialogueSelection
from agents.question.quality import followup_brief
from agents.question.react import ReactQuestionAgent
from app.adapters.assessment import RealtimeDecisionAdapter
from shared.contracts import AnswerAnalysis, CandidateAnswer
from shared.contracts.planning import TopicProgress
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup
from tests.app.test_objective_coverage import evaluation_request, objective_repository

UNAVAILABLE = "Precise private layer layout and channel count"
ASKABLE = "Concrete input alignment checks and pipeline validation procedure"
QUOTE = "I do not know the private layer layout or channel count."


def update(target, status, answer_id="a1", quote=QUOTE):
    return dict(
        target=target,
        status=status,
        answer_id=answer_id,
        supporting_quotes=[quote],
        supporting_segment_ids=[f"{answer_id}:s0"],
    )


def mixed_analysis(answer_id="a1"):
    return AnswerAnalysis(
        status="partial",
        answer_scope="concrete",
        new_information=True,
        limitations=[QUOTE],
        missing_information=[UNAVAILABLE],
        needs_assessed=True,
        need_updates=[
            update(UNAVAILABLE, "blocked", answer_id),
            update(ASKABLE, "open", answer_id),
        ],
    )


def test_partial_answer_blocks_only_its_unavailable_detail_and_keeps_coverage_evidence():
    progress = TopicProgress(
        objective_id="video",
        coverage_status="partial",
        missing_information=[UNAVAILABLE],
        coverage_evidence=[dict(answer_id="old", supporting_quotes=["Integration work"])],
    )
    before = deepcopy(progress.coverage_evidence)
    apply_need_updates(
        progress, "video", mixed_analysis(), SimpleNamespace(answer_id="a1", text=QUOTE)
    )
    assert [n.target for n in open_needs(progress, "video")] == [ASKABLE]
    blocked = next(n for n in progress.information_needs if n.target == UNAVAILABLE)
    assert blocked.status == "blocked" and blocked.blocked_evidence[0]["supporting_quotes"] == [
        QUOTE
    ]
    assert progress.coverage_status == "partial" and progress.coverage_evidence == before
    assert progress.missing_information == [
        UNAVAILABLE
    ]  # Honest coverage gap, not an ask instruction.


def test_coverage_sync_restart_and_new_objective_cannot_reopen_a_blocked_detail():
    progress = TopicProgress(objective_id="video", missing_information=[UNAVAILABLE])
    apply_need_updates(
        progress, "video", mixed_analysis(), SimpleNamespace(answer_id="a1", text=QUOTE)
    )
    restored = TopicProgress.model_validate_json(progress.model_dump_json())
    for new_goal in (False, True):
        if new_goal:
            replace_objective(restored)
        restored.missing_information = [UNAVAILABLE]
        sync_needs(restored, "video")
        analysis = AnswerAnalysis(
            status="partial",
            needs_assessed=True,
            need_updates=[
                update(UNAVAILABLE, "open", "a2", "I can describe the validation checks.")
            ],
        )
        apply_need_updates(
            restored,
            "video",
            analysis,
            SimpleNamespace(answer_id="a2", text="I can describe the validation checks."),
        )
        assert not open_needs(restored, "video")
        assert restored.next_need_id is None
        assert any(n.blocked_evidence for n in restored.information_needs if n.status == "blocked")


def test_invalid_boundary_evidence_does_not_erase_previous_open_targets():
    progress = TopicProgress(objective_id="video", missing_information=[ASKABLE])
    sync_needs(progress, "video")
    before = progress.model_dump()
    apply_need_updates(
        progress,
        "video",
        mixed_analysis(),
        SimpleNamespace(answer_id="a1", text="I implemented input checks."),
    )
    assert progress.model_dump() == before


@pytest.mark.asyncio
async def test_service_and_writer_use_askable_inventory_instead_of_the_raw_missing_gap():
    service, repo, _, initialized = await setup(PlannerLLM())
    question = initialized.first_action.question
    answer = CandidateAnswer(
        interview_id=initialized.interview_id,
        question_id=question.question_id,
        answer_id="a1",
        text="I integrated the inputs. " + QUOTE,
    )
    fb = feedback(question)
    fb.analysis = mixed_analysis()
    action = await service.apply_evaluation_feedback(initialized.interview_id, fb, answer=answer)
    context = await repo.get_interview_context(initialized.interview_id)
    assert action.question.information_goal == ASKABLE
    assert action.question.need_id == context.topic_progress[question.topic_key].next_need_id
    assert followup_brief(context)["missing_information"] == [ASKABLE]
    latest = ReactQuestionAgent(None, service._settings)._history_entry(
        context.question_history[0], context
    )
    assert latest["analysis"]["missing_information"] == [ASKABLE]
    progress = context.topic_progress[question.topic_key]
    assert any(
        n.target == UNAVAILABLE and n.status == "blocked" for n in progress.information_needs
    )


@pytest.mark.asyncio
async def test_all_local_needs_unavailable_moves_on_without_finishing_interview():
    service, repo, _, initialized = await setup(PlannerLLM())
    question = initialized.first_action.question
    fb = feedback(question)
    fb.analysis = AnswerAnalysis(
        status="partial", needs_assessed=True, need_updates=[update(UNAVAILABLE, "blocked")]
    )
    answer = CandidateAnswer(
        interview_id=initialized.interview_id,
        question_id=question.question_id,
        answer_id="a1",
        text=QUOTE,
    )
    action = await service.apply_evaluation_feedback(initialized.interview_id, fb, answer=answer)
    assert action.question is not None and action.question.topic_key != question.topic_key
    context = await repo.get_interview_context(initialized.interview_id)
    assert context.state.status == "active"
    assert context.topic_progress[question.topic_key].coverage_status != "sufficient"


def test_repair_cannot_retarget_by_keeping_the_same_need_id():
    intent = SimpleNamespace(
        need_id="same",
        dialogue_action="probe",
        project_id="p",
        topic_key="video",
        information_goal=UNAVAILABLE,
        answer_unit=UNAVAILABLE,
    )
    selection = DialogueSelection(
        need_id="same",
        dialogue_action="probe",
        project_id="p",
        topic_key="video",
        information_goal=ASKABLE,
        answer_unit=ASKABLE,
        decision_summary="Ask a different target",
    )
    assert not ReactQuestionAgent._same_selection(selection, intent)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_realtime_model_returns_scoped_states_in_the_existing_call(invalid):
    req, repo = evaluation_request(), objective_repository()
    req.answer.text = "I integrated the input checks. " + QUOTE
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        return schema(
            relations=[],
            status="partial",
            scope="concrete",
            limits=["s1"],
            needs=[
                dict(
                    target=UNAVAILABLE,
                    status="blocked",
                    s=["invented:s0" if invalid else "s1"],
                ),
                dict(target=ASKABLE, status="open", s=["s0"]),
            ],
        )

    result = await RealtimeDecisionAdapter(model, repo).evaluate(req)
    assert len(calls) == 1
    if invalid:
        assert result.analysis_status == "unavailable" and not result.analysis.need_updates
    else:
        assert result.analysis.needs_assessed and result.analysis.status == "partial"
        assert result.analysis.need_updates[0].supporting_quotes == [QUOTE]
        assert result.analysis.need_updates[1].status == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("limitation", [QUOTE, "I have no online production statistics."])
async def test_recognized_limitations_are_persisted_even_if_model_omits_blocked_entries(limitation):
    req, repo = evaluation_request(), objective_repository()
    req.answer.text = "I can explain the input checks. " + limitation

    def model(prompt, data, schema):
        return schema(
            relations=[],
            status="partial",
            scope="concrete",
            limits=["s1"],
            needs=[dict(target=ASKABLE, status="open")],
        )

    result = await RealtimeDecisionAdapter(model, repo).evaluate(req)
    assert result.analysis_status == "valid"
    blocked = [n for n in result.analysis.need_updates if n.status == "blocked"]
    assert len(blocked) == 1 and blocked[0].supporting_quotes == [limitation]
    progress = TopicProgress(objective_id="state")
    apply_need_updates(progress, "state", result.analysis, req.answer)
    assert [n.target for n in open_needs(progress, "state")] == [ASKABLE]
