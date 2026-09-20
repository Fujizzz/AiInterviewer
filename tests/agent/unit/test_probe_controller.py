import pytest

from agents.domain.models import DialogueThread, InterviewHistoryEntry
from agents.policies import ProbeController
from shared.contracts import AnswerAnalysis, EvaluationFeedback, PlannedQuestion, QuestionType
from tests.agent.unit.test_competency_selector import context


def dialogue_context(status="partial", missing=None, contradictions=None):
    value = context()
    value.state.current_question_id = "q"
    value.active_thread = DialogueThread(
        thread_id="q", project_id="llm-serving", topic="memory", topic_key="memory"
    )
    value.question_history = [
        InterviewHistoryEntry(
            question=PlannedQuestion(
                question_id="q",
                topic="memory",
                difficulty=2,
                probe_depth=1,
                question_type=QuestionType.DESCRIPTION,
                intent="details",
            ),
            feedback=EvaluationFeedback(
                request_id="r",
                question_id="q",
                answer_relevance=0.5,
                evidence_strength=0.3,
                analysis=AnswerAnalysis(
                    status=status,
                    missing_information=missing or [],
                    contradictions=contradictions or [],
                ),
            ),
        )
    ]
    return value


def test_immediate_clarification_does_not_consult_competencies():
    value = dialogue_context(missing=["What did you personally implement"])
    decision = ProbeController().decide(context=value)
    assert decision.should_probe
    assert decision.dialogue_action == "clarify"
    assert decision.next_probe_depth == 2


@pytest.mark.parametrize("status", ["explicit_unknown", "refusal"])
def test_candidate_can_stop_thread(status):
    assert not ProbeController().decide(context=dialogue_context(status)).should_probe


@pytest.mark.parametrize("stop", ["limit", "no_information", "time", "same_goal"])
def test_followup_stops_at_conversation_limits(stop):
    value = dialogue_context(missing=["Which test"])
    if stop == "limit":
        value.active_thread.follow_up_count = value.plan.max_consecutive_probes
    elif stop == "no_information":
        value.active_thread.no_information_count = 2
    elif stop == "time":
        value.state.remaining_seconds = 30
    else:
        value.active_thread.goals = ["Which test"]
    assert not ProbeController().decide(context=value).should_probe


def test_specific_missing_detail_probes_substantive_answer():
    decision = ProbeController().decide(context=dialogue_context("substantive", ["Which baseline"]))
    assert decision.dialogue_action == "probe"
    assert decision.information_goal == "Which baseline"


def test_contradiction_clarifies_before_new_information():
    decision = ProbeController().decide(
        context=dialogue_context("substantive", ["Which baseline"], ["Earlier CPU, now GPU"])
    )
    assert decision.dialogue_action == "clarify"
    assert "CPU" in decision.information_goal
