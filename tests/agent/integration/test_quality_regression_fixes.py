"""Historical failure mechanisms, using production gates and scripted verdicts.

These verify contract behavior; they do not measure live-model semantic accuracy.
"""

from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from agents.config import load_agent_settings
from agents.model_calls import ModelCall, QuestionCallBudget, current_model_call, question_call_budget, run_model_call
from agents.planning.needs import open_needs, replace_objective, sync_needs
from agents.planning.pace import round_cost
from agents.question.dialogue import DialogueSelection, resolve_selection
from agents.question.quality import GroundingAdjudication, QuestionQualityGate, QuestionQualityReview, ReviewEvidenceError
from agents.question.react import ReactQuestionAgent
from app.providers.llm import LLMError, OpenAILLM
from app.tracing import FileTrace
from shared.contracts.planning import TopicProgress
from tests.agent.integration.test_quality_completion import DRAFT, RepeatModel, scenario
from tests.agent.integration.test_interview_planning import PlannerLLM, setup
from tests.agent.integration.test_question_react import decision


@pytest.mark.asyncio
@pytest.mark.parametrize("need_id", ["roi-gap", ""])
async def test_synonymous_goal_and_omitted_id_keep_registered_roi_target(need_id):
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    topic = context.plan.topics[1]
    progress = context.topic_progress[topic.topic_key]
    progress.objective_id = topic.topic_key
    progress.information_needs = []  # Legacy saved context predates registered entry needs.
    progress.missing_information = ["Quantify the cost reduction after ROI prioritization"]
    progress.next_need_id = "roi-gap"
    sync_needs(progress, topic.topic_key, source_answer_id="roi-answer")
    selection = DialogueSelection(dialogue_action="new_topic", project_id=topic.project_id,
        topic_key=topic.topic_key, information_goal="Provide numerical evidence of ROI impact",
        decision_summary="Probe the measured impact", need_id=need_id)
    before = context.model_dump()
    planned = resolve_selection(selection, context, load_agent_settings())
    assert planned.need_id == "roi-gap"
    assert planned.information_goal == progress.missing_information[0]
    assert planned.answer_unit == progress.missing_information[0]
    assert context.model_dump() == before
    if not need_id:
        escaped = resolve_selection(selection.model_copy(update={"information_goal": "Explain the ROI formula instead"}), context, load_agent_settings())
        assert escaped.information_goal == planned.information_goal


def test_need_lifecycle_persists_and_cannot_reuse_replaced_objective_id():
    progress = TopicProgress(objective_id="roi", missing_information=["Measured impact"])
    sync_needs(progress, "roi", source_answer_id="a1")
    need_id = progress.next_need_id
    sync_needs(progress, "roi", source_answer_id="a2")
    assert progress.next_need_id == need_id and len(progress.information_needs) == 1
    restored = TopicProgress.model_validate(progress.model_dump())
    assert open_needs(restored, "roi")[0].source_answer_ids == ["a1", "a2"]
    replace_objective(restored)
    restored.missing_information = ["Measured impact"]
    sync_needs(restored, "roi")
    assert restored.next_need_id != need_id
    assert restored.information_needs[0].status == "superseded"
    restored.coverage_status, restored.missing_information = "sufficient", []
    sync_needs(restored, "roi")
    assert restored.next_need_id is None and not open_needs(restored, "roi")


@pytest.mark.asyncio
async def test_unknown_and_satisfied_need_ids_cannot_select_a_topic():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    topic = context.plan.topics[1]
    progress = context.topic_progress[topic.topic_key]
    progress.missing_information = ["Measured impact"]
    sync_needs(progress, topic.topic_key)
    closed_id = progress.next_need_id
    progress.coverage_status, progress.missing_information = "sufficient", []
    sync_needs(progress, topic.topic_key)
    # The scope remains pending for this contract test; the ID itself is closed.
    progress.coverage_status = "partial"
    for need_id in ("invented", closed_id):
        with pytest.raises(ValueError, match="UNKNOWN_INFORMATION_NEED"):
            resolve_selection(DialogueSelection(dialogue_action="new_topic", project_id=topic.project_id,
                topic_key=topic.topic_key, information_goal="Measured impact", decision_summary="Inspect impact",
                need_id=need_id), context, load_agent_settings())


@pytest.mark.asyncio
async def test_file_logging_preserves_dispute_and_allows_repeat_adjudication(tmp_path):
    _, context, target = await scenario()
    with FileTrace(tmp_path) as trace:
        result = await ReactQuestionAgent(RepeatModel(), load_agent_settings()).generate(target, "", interview=context)
    assert result.question.text == DRAFT and result.repairs_used == 1
    assert "审查证据冲突" in trace.path.read_text(encoding="utf-8")
    assert "Trace render error" not in trace.path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_renderer_failure_cannot_replace_review_evidence_error(tmp_path, monkeypatch):
    _, context, target = await scenario()
    target.text = DRAFT
    with FileTrace(tmp_path) as trace:
        monkeypatch.setattr(trace, "_render", Mock(side_effect=KeyError("unknown status")))
        with pytest.raises(ReviewEvidenceError) as error:
            await QuestionQualityGate(RepeatModel(), load_agent_settings()).review(target, context)
        assert error.value.disputed_review and error.value.code == "REVIEW_CONFLICT"


@pytest.mark.asyncio
async def test_narrower_repeat_conflict_enters_adjudication_with_valid_provenance():
    class NarrowRepeat(RepeatModel):
        async def generate_structured(self, **kwargs):
            response = await super().generate_structured(**kwargs)
            if isinstance(response, QuestionQualityReview):
                response.issues[0].repeat_relation = "narrower_unanswered_request"
            return response
    _, context, target = await scenario()
    model = NarrowRepeat()
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(target, "", interview=context)
    assert result.question.text == DRAFT
    assert model.calls.count("question_repeat_check_v1") == 1


class GroundedModel:
    def __init__(self, code, verdict="refuted", fabricated=False):
        self.code, self.verdict, self.fabricated = code, verdict, fabricated
        self.calls = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.calls.append(prompt_name)
        if response_model is QuestionQualityReview:
            return response_model(issues=[dict(code=self.code, instruction="Check this finding",
                question_quote=DRAFT, repair_action="remove_hint" if self.code == "ANSWER_HINT" else "remove_unfounded_premise")])
        if response_model is GroundingAdjudication:
            source = next(s for s in payload["sources"] if "PostgreSQL" in s["text"])
            return response_model(checks=[dict(issue_index=0, verdict=self.verdict,
                relation="established_context" if self.verdict == "refuted" else
                         "uncertain" if self.verdict == "uncertain" else
                         "suggested_answer" if self.code == "ANSWER_HINT" else "unestablished_experience",
                request_quote=DRAFT, sources=[dict(source_id=source["source_id"],
                    quote="fabricated experience" if self.fabricated else source["text"])],
                reason="Compare the claimed experience with the provided source")])
        return response_model.model_validate(decision(text=DRAFT))


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["ANSWER_HINT", "UNSUPPORTED_PREMISE"])
@pytest.mark.parametrize("verdict,fabricated", [("refuted", False), ("refuted", True), ("confirmed", False), ("uncertain", False)])
async def test_grounding_disputes_clear_only_evidenced_false_findings(code, verdict, fabricated):
    _, context, target = await scenario()
    context.candidate_profile.projects[0].description = "The shared state is stored in PostgreSQL."
    model = GroundedModel(code, verdict, fabricated)
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(target, "", interview=context)
    if verdict == "refuted" and not fabricated:
        assert result.question.text == DRAFT and not result.repaired
        assert result.question.information_goal == target.information_goal
        assert result.model_calls == 3
    else:
        assert result.question is None and code in result.blocking_issues
        assert result.model_calls <= 4


@pytest.mark.parametrize("operation", ["question", "question_quality", "question_repeat_check", "question_issue_check"])
def test_question_provider_has_no_nested_structural_retry(operation):
    class Probe(BaseModel):
        ok: bool
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(refusal=None, content='{"wrong":1}'))]))
    provider = OpenAILLM.__new__(OpenAILLM)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider.model, provider.options = "offline-test", {}
    provider.request_timeout = 30
    token = current_model_call.set(ModelCall(operation, "q1", 1, perf_counter() + 30))
    try:
        with pytest.raises(LLMError):
            provider._qwen("Return Probe", {}, Probe)
    finally:
        current_model_call.reset(token)
    assert create.call_count == 1


@pytest.mark.asyncio
async def test_generation_review_and_both_adjudications_share_call_capacity():
    factory = Mock(return_value=None)
    async def call():
        factory()
    token = question_call_budget.set(QuestionCallBudget(3))
    try:
        for operation in ("question", "question_quality", "question_repeat_check"):
            await run_model_call(call, operation=operation, timeout_seconds=1)
        with pytest.raises(RuntimeError, match="QUESTION_CALL_BUDGET_EXHAUSTED"):
            await run_model_call(call, operation="question_issue_check", timeout_seconds=1)
    finally:
        question_call_budget.reset(token)
    assert factory.call_count == 3


@pytest.mark.asyncio
async def test_observed_generation_is_part_of_the_compiled_round_cost():
    service, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    context.last_question_generation_seconds = 35
    from tests.agent.integration.test_interview_planning import feedback
    service._interview_planner.feedback(context, question, feedback(question), 85)
    assert context.estimated_question_seconds == 120  # 35 generation + 85 answer/evaluation
    context.last_question_generation_seconds = 45
    service._interview_planner.feedback(context, question, feedback(question), 155)
    assert round_cost(context, load_agent_settings()) == 144
    context.state.remaining_seconds = context.plan.closing_seconds + 143
    planner = service._interview_planner
    draft, adjustments = planner._compile(context.plan_history[-1].proposal, context, planner._eligible(context))
    assert not draft.topics
    assert any(a["reason"] == "INSUFFICIENT_ALLOCATION_TIME" for a in adjustments)


@pytest.mark.asyncio
async def test_explicit_objective_replacement_retires_old_need_in_real_replan():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = context.plan.topics[0]
    progress = context.topic_progress[first.topic_key]
    progress.missing_information = ["Explain the old implementation detail"]
    sync_needs(progress, first.topic_key, source_answer_id="a1")
    old_id = progress.next_need_id
    def script(payload, count):
        from shared.contracts.planning import PlanProposal
        return PlanProposal(base_plan_version=payload["base_plan_version"], reason="Replace the objective explicitly",
            topics=[dict(project_id=first.project_id, topic_key=first.topic_key,
                         objective="Assess measured impact", completion_criteria="Concrete measurement established",
                         objective_change="replace")])
    from agents.planning.planner import InterviewPlannerAgent
    await InterviewPlannerAgent(PlannerLLM(script), load_agent_settings()).revise(context, "OBJECTIVE_CHANGE")
    assert progress.objective_version == 2
    assert progress.next_need_id is None and not open_needs(progress, first.topic_key)
    retired = next(n for n in progress.information_needs if n.need_id == old_id)
    assert retired.status == "superseded"
    assert context.plan.topics[0].objective == "Assess measured impact"


@pytest.mark.asyncio
async def test_correcting_invalid_need_id_does_not_require_changing_valid_question_text():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    topic = context.plan.topics[1]
    progress = context.topic_progress[topic.topic_key]
    progress.missing_information = ["Measured improvement"]
    sync_needs(progress, topic.topic_key)
    selected = dict(dialogue_action="new_topic", project_id=topic.project_id,
                    topic_key=topic.topic_key, information_goal="Measured improvement",
                    decision_summary="Ask for the measured impact", need_id=progress.next_need_id)
    class CorrectingModel:
        generations = 0
        async def generate_structured(self, *, prompt_name, payload, response_model):
            if response_model is QuestionQualityReview:
                return response_model(issues=[])
            self.generations += 1
            return response_model.model_validate(decision(
                text="In this project, what measured improvement did you observe?",
                selection={**selected, "need_id": "invalid-need"} if self.generations == 1 else selected))
    model = CorrectingModel()
    target = result.first_action.question.model_copy(update={"question_id": "corrected-need", "intent_id": ""})
    generated = await ReactQuestionAgent(model, load_agent_settings()).generate(target, "", interview=context, autonomous=True)
    assert generated.stop_reason == "FINAL"
    assert generated.question.need_id == progress.next_need_id
    assert model.generations == 2
