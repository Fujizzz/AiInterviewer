from agents.orchestrator import InterviewStageMachine, TerminationPolicy
from agents.policies import RedundancyPolicy
from shared.contracts import InterviewStage, PlannedQuestion, QuestionType
from tests.agent.factories import interview_plan, interview_state


def test_termination_when_time_is_exhausted() -> None:
    state = interview_state(remaining_seconds=0)

    assert TerminationPolicy().should_finish(state) is True


def test_stage_budget_exhaustion_triggers_transition() -> None:
    state = interview_state()
    plan = interview_plan()
    state.elapsed_seconds = plan.stages[0].budget_seconds

    stage_machine = InterviewStageMachine()

    assert stage_machine.should_transition(state, plan) is True
    assert stage_machine.next_stage(state, plan) == InterviewStage.FINISHED


def test_redundancy_key_uses_project_topic_and_goal() -> None:
    question = PlannedQuestion(
        question_id="q-1",
        topic="memory",
        difficulty=3,
        probe_depth=1,
        question_type=QuestionType.FAILURE_ANALYSIS,
        intent="test debugging",
    )
    same_key = question.model_copy(update={"question_id": "q-2", "text": "Different wording"})

    assert RedundancyPolicy().is_redundant(same_key, [question]) is True
