"""Reproduce JSON-mode finals rejected in the 2026-09-20 interview."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agents.model_calls import validation_issues
from agents.orchestrator import InterviewAgentService
from agents.question.react import QuestionAgentDecision
from app.adapters.llm import ProviderLLMAdapter
from app.providers.llm import OpenAILLM
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository
from tests.app.test_qwen_pdf import response


def final_output():
    return {
        "action": "final",
        "topic": "Reduced GPU memory usage",
        "project_id": "llm-serving",
        "text": "In your LLM Serving Platform project, what change reduced GPU memory usage?",
        "selection": {
            "dialogue_action": "new_topic",
            "project_id": "llm-serving",
            "topic_key": "llm-serving:claim:memory",
            "information_goal": "Explain your implementation of memory optimization",
            "decision_summary": "The resume mentions a concrete memory optimization.",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_provider_final_compatibility_and_actionable_repair(conflict):
    output = final_output()
    if conflict:
        output["project_id"] = "a-different-project"
    with (
        patch("app.providers.llm.load_dotenv"),
        patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "dashscope",
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_MODEL": "test-model",
                "OPENAI_TEMPERATURE": "0",
            },
            clear=True,
        ),
        patch("app.providers.llm.OpenAI") as client,
    ):
        create = client.return_value.chat.completions.create
        create.side_effect = [response(json.dumps(output)), response(json.dumps(final_output()))]
        repository = InMemoryRepository()
        result = await InterviewAgentService(
            repository=repository, llm=ProviderLLMAdapter(OpenAILLM())
        ).initialize_interview(pipeline_request())
        action = result.first_action
        assert action.decision_trace.details["generation_reason"] == (
            "LLM_REPAIRED" if conflict else "LLM_GENERATED"
        )
        assert action.question.project_id == "llm-serving"
        assert len(repository.questions) == 1
        assert create.call_count == (2 if conflict else 1)
        if conflict:
            payload = json.loads(create.call_args.kwargs["messages"][1]["content"])
            assert "$:conflicting_project_ids" in payload["repair_errors"]
            assert "a-different-project" not in str(payload["repair_errors"])


def test_final_canonicalizes_only_unused_tool_arguments():
    decision = QuestionAgentDecision.model_validate(final_output())
    assert decision.topic is None and decision.project_id is None and decision.limit is None
    assert decision.selection.project_id == "llm-serving"
    with pytest.raises(ValidationError):
        QuestionAgentDecision.model_validate({"action": "get_project", "topic": "invalid"})
    with pytest.raises(ValidationError):
        QuestionAgentDecision.model_validate({"action": "get_history"})


def test_validation_feedback_shows_fields_without_echoing_model_content():
    output = final_output()
    del output["selection"]["information_goal"]
    output["SECRET arbitrary extra field"] = "SECRET answer text"
    with pytest.raises(ValidationError) as captured:
        QuestionAgentDecision.model_validate(output)
    issues = validation_issues(captured.value, QuestionAgentDecision)
    assert "selection.information_goal:missing" in issues
    assert "*:extra_forbidden" in issues
    assert "SECRET" not in str(issues)


@pytest.mark.parametrize("action", ["clarify", "probe", "new_topic", "new_project"])
def test_explicit_matching_dialogue_action_is_canonicalized_to_final(action):
    output = final_output()
    output["action"] = action
    output["selection"]["dialogue_action"] = action
    decision = QuestionAgentDecision.model_validate(output)
    assert decision.action == "final"
    assert decision.selection.dialogue_action == action
    output["selection"]["dialogue_action"] = "probe" if action != "probe" else "clarify"
    with pytest.raises(ValidationError):
        QuestionAgentDecision.model_validate(output)
    del output["selection"]
    with pytest.raises(ValidationError):
        QuestionAgentDecision.model_validate(output)
