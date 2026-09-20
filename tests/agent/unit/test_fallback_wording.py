import pytest

from agents.question.fallback import FallbackQuestionPolicy
from agents.question.validator import QuestionValidator
from shared.contracts import CandidateClaim, CandidateProject
from tests.agent.unit.test_question_pipeline import debugging_plan


@pytest.mark.parametrize(
    "action,goal",
    [
        ("new_topic", "Developed " + "a long resume claim " * 30),
        (
            "clarify",
            "Clarify the differing accounts: f329102d-d407-49e9-a41a-6b0d12895ca7: ViT conflicts",
        ),
        ("clarify", "Which specific transformer architecture was used?"),
        ("probe", "Explain " + "long internal analysis " * 40),
    ],
)
def test_fallback_is_complete_and_does_not_paste_diagnostics(action, goal):
    plan = debugging_plan().model_copy(
        update={"dialogue_action": action, "information_goal": goal, "topic": goal}
    )
    project = CandidateProject(project_id="p", name="Lip synchronization")
    question = FallbackQuestionPolicy().apply(plan, project=project)
    assert QuestionValidator().is_valid(question, plan)
    assert question.text.endswith("?") and question.text.count("?") == 1
    assert len(question.text.split()) <= 110
    assert "Lip synchronization" in question.text
    assert "f329102d" not in question.text
    assert "Developed" not in question.text
    assert "long internal analysis" not in question.text
    assert not question.text.endswith(("–pho?", "Transfor?"))


def test_question_validator_blocks_internal_answer_ids():
    plan = debugging_plan()
    question = plan.model_copy(
        update={"text": "What did you mean by answer f329102d-d407-49e9-a41a-6b0d12895ca7?"}
    )
    assert "INTERNAL_IDENTIFIER_LEAK" in QuestionValidator().validate(question, plan).errors


@pytest.mark.parametrize("clarification", [False, True])
def test_long_project_name_and_topic_are_explicit_even_when_candidate_asks_which(clarification):
    name = (
        "Research project: Deep learning for quantitative susceptibility mapping "
        "and QSM reconstruction using convolutional networks and attention mechanisms"
    )
    claim = "Designed QSM reconstruction algorithms combining convolutional networks with attention"
    project = CandidateProject(
        project_id="qsm", name=name, claims=[CandidateClaim(claim_id="qsm-model", text=claim)]
    )
    plan = debugging_plan().model_copy(
        update={
            "project_id": "qsm",
            "topic": claim,
            "dialogue_action": "clarify" if clarification else "new_project",
            "answer_excerpt": "which project" if clarification else "",
            "information_goal": "Which specific project the candidate worked on"
            if clarification
            else "Explain the candidate's own implementation",
        }
    )
    question = FallbackQuestionPolicy().apply(plan, project=project)
    assert name in question.text and claim in question.text
    assert QuestionValidator().is_valid(question, plan)
    assert question.text.count("?") == 1
    assert "this project" not in question.text
    assert "What specific change" not in question.text


def test_generic_retry_still_names_the_selected_project():
    plan = debugging_plan()
    project = CandidateProject(project_id="qsm", name="QSM reconstruction")
    question = FallbackQuestionPolicy().apply(plan, project=project, generic=True)
    assert "QSM reconstruction" in question.text
