"""Verify model choices drive committed dialogue, rather than a fixed policy plan."""

from copy import deepcopy

import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from agents.question.dialogue import DialogueSelection, resolve_selection
from shared.contracts import AnswerAnalysis, CandidateProject, EvaluationFeedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository


class ChoosingModel:
    def __init__(self):
        self.calls = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        # Scripted semantic pass; quality rejection is tested separately.
        if response_model.__name__ == "QuestionQualityReview":
            return response_model(issues=[])
        self.calls.append(deepcopy(payload))
        project = payload["dialogue_state"]["projects"][-1]
        if not payload["observations"]:
            return response_model(
                action="get_project",
                topic=None,
                limit=None,
                project_id=project["project_id"],
                text=None,
            )
        detail = payload["observations"][0]["result"]["project"]
        return response_model(
            action="final",
            topic=None,
            limit=None,
            text=f"What did you implement in {detail['name']}?",
            selection=DialogueSelection(
                dialogue_action="new_topic",
                project_id=project["project_id"],
                topic_key=project["topics"][0]["topic_key"],
                information_goal="Explain your implementation of the log parser",
                decision_summary="The resume describes a concrete parser implementation.",
            ),
        )


@pytest.mark.asyncio
async def test_model_selects_different_project_and_reads_details_on_demand():
    request = pipeline_request()
    request.candidate_profile.projects.append(
        CandidateProject(
            project_id="logs",
            name="Log Parser",
            description="PRIVATE_DETAIL_MARKER",
            technologies=["Python"],
        )
    )
    repository, model = InMemoryRepository(), ChoosingModel()
    response = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        request
    )
    question = response.first_action.question
    assert question.project_id == "logs"  # Old deterministic selector prefers llm-serving.
    assert response.first_action.decision_trace.details["decision_source"] == "model"
    assert "question_plan" not in model.calls[0]
    assert "context" not in model.calls[0]
    assert "PRIVATE_DETAIL_MARKER" not in str(model.calls[0])
    assert "PRIVATE_DETAIL_MARKER" in str(model.calls[1]["observations"])
    context = await repository.get_interview_context(request.interview_id)
    assert context.active_thread.project_id == "logs"
    assert context.active_thread.goals == ["Explain your implementation of the log parser"]
    assert context.used_topic_keys == [question.topic_key]


@pytest.mark.asyncio
async def test_model_can_continue_after_narrow_answer_without_missing_information_hint():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question

    class ProbeModel:
        async def generate_structured(self, *, prompt_name, payload, response_model):
            # Scripted semantic pass; quality rejection is tested separately.
            if response_model.__name__ == "QuestionQualityReview":
                return response_model(issues=[])
            thread = payload["dialogue_state"]["active_thread"]
            assert payload["latest_turn"]["analysis"]["missing_information"] == []
            return response_model(
                action="final",
                topic=None,
                limit=None,
                text="Which concrete implementation step did you personally complete?",
                selection=dict(
                    dialogue_action="probe",
                    project_id=thread["project_id"],
                    topic_key=thread["topic_key"],
                    information_goal="Explain the implementation step",
                    decision_summary="Task named; implementation remains unspecified.",
                ),
            )

    action = await InterviewAgentService(
        repository=repository, llm=ProbeModel()
    ).apply_evaluation_feedback(
        "pipeline-interview",
        EvaluationFeedback(
            request_id="r",
            question_id=question.question_id,
            answer_relevance=0.9,
            evidence_strength=0,
            analysis=AnswerAnalysis(
                status="substantive",
                answer_scope="label_only",
                thread_complete=False,
                new_information=True,
            ),
        ),
    )
    assert action.question.dialogue_action == "probe"
    assert action.question.parent_question_id == question.question_id
    assert action.decision_trace.details["decision_source"] == "model"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation,expected",
    [
        ("limit", "TOPIC_QUESTION_LIMIT"),
        ("refusal", "CANDIDATE_STOPPED_THREAD"),
        ("wrong_project", "FOLLOWUP_MUST_KEEP_CURRENT_THREAD"),
        ("repeat_goal", "REPEATED_INFORMATION_GOAL"),
        ("unknown_topic", "UNKNOWN_OR_USED_TOPIC"),
    ],
)
async def test_model_selection_must_pass_server_guards(violation, expected):
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    from agents.domain.models import InterviewHistoryEntry

    context = await repository.get_interview_context("pipeline-interview")
    context.question_history = [
        InterviewHistoryEntry(
            question=first,
            feedback=EvaluationFeedback(
                request_id="r",
                question_id=first.question_id,
                answer_relevance=0.5,
                evidence_strength=0,
                analysis=AnswerAnalysis(status="refusal" if violation == "refusal" else "partial"),
            ),
        )
    ]
    selection = DialogueSelection(
        dialogue_action="probe",
        project_id=first.project_id,
        topic_key=first.topic_key,
        information_goal="Explain one specific step",
        decision_summary="Implementation remains unknown.",
    )
    if violation == "limit":
        context.active_thread.follow_up_count = context.plan.max_consecutive_probes
    elif violation == "wrong_project":
        selection.project_id = "other"
    elif violation == "repeat_goal":
        selection.information_goal = context.active_thread.goals[0]
        context.question_history[-1].feedback.analysis.thread_complete = True
    elif violation == "unknown_topic":
        selection.dialogue_action = "new_topic"
        selection.topic_key = "invented"
    with pytest.raises(ValueError, match=expected):
        resolve_selection(selection, context, load_agent_settings())


@pytest.mark.asyncio
async def test_same_unresolved_goal_reaches_wording_review_without_renaming():
    from docs.examples.verify_question_quality import scenario

    first, context = await scenario()
    selection = DialogueSelection(
        dialogue_action="clarify",
        project_id=first.project_id,
        topic_key=first.topic_key,
        information_goal=first.information_goal,
        decision_summary="Narrow the vague answer to the personally handled component.",
    )
    result = resolve_selection(selection, context, load_agent_settings())
    assert result.information_goal == first.information_goal
    assert result.thread_id == first.thread_id
    assert result.parent_question_id == first.question_id


@pytest.mark.asyncio
async def test_invalid_model_choice_is_repaired_and_only_valid_choice_is_committed():
    class RepairModel(ChoosingModel):
        async def generate_structured(self, *, prompt_name, payload, response_model):
            # Scripted semantic pass; quality rejection is tested separately.
            if response_model.__name__ == "QuestionQualityReview":
                return response_model(issues=[])
            self.calls.append(deepcopy(payload))
            project = payload["dialogue_state"]["projects"][0]
            return response_model(
                action="final",
                topic=None,
                limit=None,
                text="What did you implement in the memory pipeline?",
                selection=dict(
                    dialogue_action="new_topic",
                    project_id=project["project_id"],
                    topic_key="invented"
                    if not payload["repair_errors"]
                    else project["topics"][0]["topic_key"],
                    information_goal="Explain the memory implementation",
                    decision_summary="Explore memory work.",
                ),
            )

    repository, model = InMemoryRepository(), RepairModel()
    action = (
        await InterviewAgentService(repository=repository, llm=model).initialize_interview(
            pipeline_request()
        )
    ).first_action
    assert model.calls[1]["repair_errors"] == ["UNKNOWN_OR_USED_TOPIC"]
    assert action.decision_trace.details["generation_reason"] == "LLM_REPAIRED"
    assert len(repository.questions) == 1
    assert action.question.topic_key != "invented"
