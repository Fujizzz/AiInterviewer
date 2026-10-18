"""Versions, objective evidence and resumed needs are enforced by the controller."""

import pytest

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.planning.planner import InterviewPlannerAgent
from agents.policies.dialogue_controller import DialogueController
from agents.policies.probe_controller import ProbeController
from agents.question.quality import followup_brief
from shared.contracts import CandidateAnswer, ObjectiveCoverage
from shared.contracts.planning import PlanProposal
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup


@pytest.mark.asyncio
async def test_stale_native_proposal_is_rejected_but_old_semantics_survive():
    def script(payload, count):
        item = payload["eligible_topics"][0]
        return PlanProposal(
            topics=[
                {
                    "project_id": item["project_id"],
                    "topic_key": item["topic_key"],
                    "objective": "Inspect cache implementation",
                    "completion_criteria": "Explain storage",
                }
            ],
            base_plan_version=0,
            reason="Initial plan or deliberately stale revision",
        )

    service, repo, _, result = await setup(PlannerLLM(script))
    context = await repo.get_interview_context(result.interview_id)
    first = context.plan_history[0]
    assert not first.fallback_used
    await service._interview_planner.revise(context, "REVISIT_GAP")
    revised = context.plan_history[-1]
    assert revised.fallback_used and revised.base_plan_version == 1
    assert revised.proposal.topics == first.proposal.topics


@pytest.mark.asyncio
async def test_short_remaining_time_compiles_locally_without_a_model_call():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    context = await repo.get_interview_context(result.interview_id)
    context.state.remaining_seconds = 100
    clock.value += 800
    await service._interview_planner.revise(context, "TIME_DRIFT")
    assert len(llm.plans) == 1
    assert context.plan_history[-1].source == "local_compilation"
    assert not context.plan_history[-1].fallback_used


@pytest.mark.asyncio
async def test_semantic_revision_records_changes_without_claiming_coverage():
    def script(payload, count):
        item = payload["eligible_topics"][0]
        return PlanProposal(
            topics=[
                {
                    "project_id": item["project_id"],
                    "topic_key": item["topic_key"],
                    "objective": "Inspect cache implementation",
                    "completion_criteria": "Explain storage",
                    "depth": "standard" if count == 1 else "deep",
                }
            ],
            base_plan_version=payload["base_plan_version"],
            reason="Prioritize deeper evidence",
        )

    service, repo, _, result = await setup(PlannerLLM(script))
    context = await repo.get_interview_context(result.interview_id)
    await service._interview_planner.revise(context, "REVISIT_GAP")
    revision = context.plan_history[-1]
    assert not revision.fallback_used
    assert revision.semantic_changes[0]["before"][1]["depth"] == "standard"
    assert revision.semantic_changes[0]["after"][1]["depth"] == "deep"
    assert (
        context.topic_progress[result.first_action.question.topic_key].coverage_status
        != "sufficient"
    )


@pytest.mark.asyncio
async def test_broader_objective_gap_drives_followup_after_narrow_completion():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    fb = feedback(question, complete=True)
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="ownership",
        text="I built the state module.",
    )
    gap = "Explain durable storage and access"
    fb.objective_coverage_status = "valid"
    fb.objective_coverage = [
        ObjectiveCoverage(
            objective_id=question.topic_key,
            coverage_status="partial",
            missing_information=[gap],
            supporting_quotes=[answer.text],
            supporting_segment_ids=["ownership:s0"],
            answer_id=answer.answer_id,
        )
    ]
    context.question_history = [
        InterviewHistoryEntry(question=question, answer=answer, feedback=fb)
    ]
    context.active_thread.goals.append(gap)
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 20)
    probe = ProbeController().decide(context=context)
    assert probe.should_probe and probe.information_goal == gap
    assert followup_brief(context)["missing_information"] == [gap]
    assert not DialogueController(context).goal_already_asked(
        question.project_id,
        gap,
        topic_key=question.topic_key,
        allow_current_clarification=True,
    )
    context.topic_progress[question.topic_key].coverage_status = "sufficient"
    assert DialogueController(context).goal_already_asked(
        question.project_id,
        gap,
        topic_key=question.topic_key,
        allow_current_clarification=True,
    )


@pytest.mark.asyncio
async def test_resume_exception_cannot_unlock_a_different_topic_or_resolved_goal():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = result.first_action.question
    progress = context.topic_progress[first.topic_key]
    progress.status, progress.reason = "pending", "RESUMED_BY_PLAN"
    progress.coverage_status = "partial"
    progress.missing_information = [first.information_goal]
    controller = DialogueController(context)
    assert controller.goal_already_asked(
        first.project_id, first.information_goal, topic_key=context.plan.topics[1].topic_key
    )
    progress.missing_information = ["A genuinely different unresolved target"]
    assert controller.goal_already_asked(
        first.project_id, first.information_goal, topic_key=first.topic_key
    )


@pytest.mark.asyncio
async def test_corrected_evidence_retracts_objective_completion_until_new_support():
    from shared.contracts.agent_contracts import AnswerRelation

    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    progress = context.topic_progress[question.topic_key]
    progress.status, progress.coverage_status = "completed", "sufficient"
    progress.coverage_evidence = [
        {
            "answer_id": "old",
            "supporting_quotes": ["I stored state in PostgreSQL."],
            "supporting_segment_ids": ["old:s0"],
        }
    ]
    for criterion in progress.completion_requirements:
        criterion.coverage_status = "sufficient"
        criterion.evidence = [dict(progress.coverage_evidence[0])]
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="correction",
        text=(
            "Correction: I stored state in memory. "
            "I validated committed updates by reading the stored state back."
        ),
    )
    fb = feedback(question, complete=True)
    fb.analysis.answer_relations = [
        AnswerRelation(
            kind="supersedes",
            earlier_answer_id="old",
            earlier_quote="I stored state in PostgreSQL.",
            current_quote=answer.text,
            explanation="Candidate corrected storage mechanism",
        )
    ]
    context.question_history = [
        InterviewHistoryEntry(question=question, answer=answer, feedback=fb)
    ]
    planner = InterviewPlannerAgent(None, load_agent_settings())
    planner.feedback(context, question, fb, 10)
    assert progress.status == "deferred" and progress.coverage_status == "partial"
    assert progress.coverage_evidence == [] and progress.next_need_id
    assert all(
        c.coverage_status == "partial" and not c.evidence for c in progress.completion_requirements
    )
    fb.objective_coverage_status = "valid"
    fb.objective_coverage = [
        ObjectiveCoverage(
            objective_id=question.topic_key,
            coverage_status="sufficient",
            answer_id=answer.answer_id,
            supporting_quotes=[answer.text],
            supporting_segment_ids=["correction:s0"],
            criterion_coverage=[
                {
                    "criterion_id": criterion.criterion_id,
                    "coverage_status": "sufficient",
                    "answer_id": answer.answer_id,
                    "supporting_quotes": [answer.text],
                    "supporting_segment_ids": ["correction:s0", "correction:s1"],
                }
                for criterion in progress.completion_requirements
            ],
        )
    ]
    planner.feedback(context, question, fb, 0)
    assert progress.coverage_status == "sufficient"
    assert progress.coverage_evidence[0]["answer_id"] == "correction"
    assert all(c.coverage_status == "sufficient" for c in progress.completion_requirements)
