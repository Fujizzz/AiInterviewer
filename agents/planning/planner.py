"""Plan objectives and remaining allocations; question wording stays in QuestionAgent."""

from collections import Counter

from agents.model_calls import run_model_call, safe_error_details
from agents.tracing import emit_trace
from shared.contracts.planning import PlanDraft, PlanRevision, TopicAllocation, TopicProgress


def catalog(context):
    from agents.policies.topic_selector import TopicSelector

    result = {
        topic.topic_key: {
            "project_id": project.project_id,
            "topic_key": topic.topic_key,
            "label": topic.topic,
        }
        for project in context.candidate_profile.projects
        for topic in TopicSelector().candidates(project)
    }
    if not result and not context.candidate_profile.projects:
        result["general:experience"] = {
            "project_id": None,
            "topic_key": "general:experience",
            "label": "personal technical experience",
        }
    return result


def remaining_allocation(context, item):
    progress = context.topic_progress.get(item.topic_key, TopicProgress())
    return max(0, item.budget_seconds - progress.elapsed_seconds)


def execution_topics(context):
    """Priority order, excluding exhausted or permanently closed items."""
    return [
        item
        for item in context.plan.topics
        if context.topic_progress.get(item.topic_key, TopicProgress()).status
        in {"pending", "active"}
        and remaining_allocation(context, item) > 0
    ]


def planning_view(context):
    projects = {}
    for item in context.plan.topics:
        project = projects.setdefault(
            item.project_id,
            {
                "project_id": item.project_id,
                "budget_seconds": 0,
                "expected_questions": 0,
            },
        )
        project["budget_seconds"] += item.budget_seconds
        project["expected_questions"] += item.expected_questions
    return {
        "version": context.plan.version,
        "remaining_seconds": context.state.remaining_seconds,
        "reserve_seconds": context.plan.reserve_seconds,
        "closing_seconds": context.plan.closing_seconds,
        "estimated_question_seconds": round(context.estimated_question_seconds),
        "projects": list(projects.values()),
        "topics": [
            {
                **item.model_dump(),
                "remaining_allocation_seconds": remaining_allocation(context, item),
                "progress": context.topic_progress.get(
                    item.topic_key, TopicProgress()
                ).model_dump(),
            }
            for item in context.plan.topics
        ],
    }


class InterviewPlannerAgent:
    prompt_name = "interview_planner_v1"

    def __init__(self, llm, settings, sync_clock=None):
        self.llm = llm
        self.settings = settings
        self.sync_clock = sync_clock

    def _eligible(self, context):
        project_counts = Counter()
        for key, progress in context.topic_progress.items():
            owner = catalog(context).get(key, {}).get("project_id")
            project_counts[owner] += progress.questions_asked
        return {
            key: item
            for key, item in catalog(context).items()
            if context.topic_progress.get(key, TopicProgress()).status
            not in {"completed", "skipped"}
            and context.topic_progress.get(key, TopicProgress()).questions_asked
            < context.plan.max_questions_per_topic
            and project_counts[item["project_id"]] < context.plan.max_questions_per_project
        }

    def _validate(self, draft, context, eligible):
        if len({item.topic_key for item in draft.topics}) != len(draft.topics):
            raise ValueError("Duplicate planned topic")
        if (
            sum(item.budget_seconds for item in draft.topics)
            + draft.reserve_seconds
            + draft.closing_seconds
            > context.state.remaining_seconds
        ):
            raise ValueError("Plan exceeds remaining time")
        if not draft.topics and eligible:
            raise ValueError("Planner must allocate an eligible topic")
        project_counts = Counter()
        for key, progress in context.topic_progress.items():
            project_counts[catalog(context).get(key, {}).get("project_id")] += (
                progress.questions_asked
            )
        total = context.state.question_index
        for item in draft.topics:
            if item.topic_key not in eligible:
                raise ValueError("Unknown or closed topic")
            if eligible[item.topic_key]["project_id"] != item.project_id:
                raise ValueError("Project/topic mismatch")
            if item.budget_seconds < self.settings.planning.minimum_question_seconds:
                raise ValueError("Topic allocation too short")
            asked = context.topic_progress.get(item.topic_key, TopicProgress()).questions_asked
            if asked + item.expected_questions > context.plan.max_questions_per_topic:
                raise ValueError("Topic safety ceiling")
            project_counts[item.project_id] += item.expected_questions
            total += item.expected_questions
        if total > context.plan.max_questions or any(
            count > context.plan.max_questions_per_project for count in project_counts.values()
        ):
            raise ValueError("Question safety ceiling")

    def _fallback(self, context, eligible):
        remaining = context.state.remaining_seconds
        minimum = self.settings.planning.minimum_question_seconds
        closing = min(120, remaining // 15)
        reserve = min(180, remaining // 10)
        usable = remaining - closing - reserve
        total_slots = context.plan.max_questions - context.state.question_index
        counts = Counter()
        for key, progress in context.topic_progress.items():
            counts[catalog(context).get(key, {}).get("project_id")] += progress.questions_asked
        # Preserve the current agenda's order, then consider previously unallocated topics.
        order = list(dict.fromkeys([t.topic_key for t in context.plan.topics] + list(eligible)))
        selected = []
        for key in order:
            if key not in eligible or len(selected) >= min(total_slots, usable // minimum):
                continue
            item = eligible[key]
            if counts[item["project_id"]] >= context.plan.max_questions_per_project:
                continue
            selected.append(item)
            counts[item["project_id"]] += 1
            # Estimate topic count from observed pace; future boundaries can revise it.
            if len(selected) >= max(
                1, usable // max(minimum, int(context.estimated_question_seconds))
            ):
                break
        topics = []
        for index, item in enumerate(selected):
            seconds = usable // len(selected) + (index < usable % len(selected))
            label = item["label"].replace("?", "").replace("？", "")[:250]
            topics.append(
                TopicAllocation(
                    project_id=item["project_id"],
                    topic_key=item["topic_key"],
                    objective=f"Assess personal implementation and decisions concerning {label}",
                    completion_criteria="Concrete personal actions and implementation rationale",
                    budget_seconds=seconds,
                    expected_questions=1,
                )
            )
        return PlanDraft(
            topics=topics,
            reserve_seconds=reserve,
            closing_seconds=closing,
            reason="Deterministic allocation within remaining time and safety ceilings",
        )

    async def revise(self, context, trigger):
        eligible = self._eligible(context)
        fallback = False
        try:
            if self.llm is None:
                raise ValueError("No planner provider")
            latest = context.question_history[-1] if context.question_history else None
            draft = await run_model_call(
                lambda: self.llm.generate_structured(
                    prompt_name=self.prompt_name,
                    payload={
                        "trigger": trigger,
                        "candidate_profile": context.candidate_profile.model_dump(mode="json"),
                        "job_profile": context.job_profile.model_dump(mode="json"),
                        "remaining_seconds": context.state.remaining_seconds,
                        "eligible_topics": list(eligible.values()),
                        "current_plan": planning_view(context),
                        "safety_guardrails": {
                            "max_questions": context.plan.max_questions,
                            "max_questions_per_project": context.plan.max_questions_per_project,
                            "max_questions_per_topic": context.plan.max_questions_per_topic,
                            "questions_already_asked": context.state.question_index,
                        },
                        "minimum_topic_seconds": self.settings.planning.minimum_question_seconds,
                        "latest_analysis": latest.feedback.analysis.model_dump()
                        if latest
                        else None,
                    },
                    response_model=PlanDraft,
                ),
                operation="interview_planner",
                timeout_seconds=min(
                    self.settings.planning.timeout_seconds, max(1, context.state.remaining_seconds)
                ),
            )
            draft = PlanDraft.model_validate(draft.model_dump())
            self._validate(draft, context, eligible)
            if self.sync_clock is not None:
                self.sync_clock(context)
                # A model call consumes real time. Remove that cost from reserves,
                # then from the lowest-priority future allocations before committing.
                excess = max(
                    0,
                    sum(t.budget_seconds for t in draft.topics)
                    + draft.reserve_seconds
                    + draft.closing_seconds
                    - context.state.remaining_seconds,
                )
                take = min(excess, draft.reserve_seconds)
                draft.reserve_seconds -= take
                excess -= take
                minimum = self.settings.planning.minimum_question_seconds
                for item in reversed(draft.topics):
                    take = min(excess, max(0, item.budget_seconds - minimum))
                    item.budget_seconds -= take
                    excess -= take
                while excess > 0 and draft.topics:
                    excess -= draft.topics.pop().budget_seconds
                if excess > 0:
                    draft.closing_seconds = max(0, draft.closing_seconds - excess)
        except Exception as error:
            emit_trace("planning.fallback", trigger=trigger, **safe_error_details(error))
            fallback = True
            if context.plan.version:
                # Failed replans leave allocations intact. Execution guards can still close
                # exhausted items and advance; an outage cannot grant extra time/questions.
                context.last_replan_question_index = context.state.question_index
                return
            draft = self._fallback(context, eligible)
            self._validate(draft, context, eligible)
        retained = [
            item
            for item in context.plan.topics
            if context.topic_progress.get(item.topic_key, TopicProgress()).status
            in {"completed", "skipped"}
        ]
        allocated = {item.topic_key for item in draft.topics}
        for item in context.plan.topics:
            progress = context.topic_progress[item.topic_key]
            if progress.status in {"pending", "active"} and item.topic_key not in allocated:
                progress.status, progress.reason = "skipped", "REMOVED_BY_REPLAN"
                retained.append(item)
        adjusted = []
        for item in draft.topics:
            progress = context.topic_progress.setdefault(item.topic_key, TopicProgress())
            adjusted.append(
                item.model_copy(
                    update={
                        "budget_seconds": item.budget_seconds + progress.elapsed_seconds,
                        "expected_questions": item.expected_questions + progress.questions_asked,
                    }
                )
            )
        context.plan.topics = adjusted + retained
        context.plan.reserve_seconds = draft.reserve_seconds
        context.plan.closing_seconds = draft.closing_seconds
        context.plan.version += 1
        context.last_replan_question_index = context.state.question_index
        latest = context.question_history[-1] if context.question_history else None
        revision = PlanRevision(
            version=context.plan.version,
            elapsed_seconds=context.state.elapsed_seconds,
            trigger=trigger,
            reason=draft.reason,
            answer_id=latest.answer.answer_id if latest and latest.answer else None,
            fallback_used=fallback,
            topics=context.plan.topics,
            reserve_seconds=draft.reserve_seconds,
            closing_seconds=draft.closing_seconds,
        )
        context.plan_history.append(revision.model_copy(deep=True))
        emit_trace("planning.revised", revision=revision.model_dump(mode="json"))

    def feedback(self, context, question, feedback, elapsed_seconds):
        progress = context.topic_progress.get(question.topic_key)
        if progress is None:
            return
        if elapsed_seconds > 0:
            context.estimated_question_seconds = (
                0.7 * context.estimated_question_seconds + 0.3 * elapsed_seconds
            )
        analysis = feedback.analysis
        terminal = analysis.status in {"explicit_unknown", "refusal"}
        completed = analysis.thread_complete and not (
            analysis.contradictions or analysis.uncertainties or analysis.missing_information
        )
        no_information = (
            context.active_thread
            and context.active_thread.no_information_count
            >= self.settings.probe.max_no_information_answers
        )
        if terminal or no_information or completed:
            progress.status = "skipped" if terminal or no_information else "completed"
            progress.reason = (
                "CANDIDATE_STOPPED"
                if terminal
                else ("NO_NEW_INFORMATION" if no_information else "OBJECTIVE_COMPLETED")
            )

    async def review(self, context):
        if not context.plan.planning_enabled or not context.question_history:
            return
        active = context.active_thread
        progress = context.topic_progress.get(active.topic_key) if active else None
        item = next(
            (t for t in context.plan.topics if active and t.topic_key == active.topic_key), None
        )
        latest = context.question_history[-1].feedback.analysis
        trigger = None
        if progress and progress.status in {"completed", "skipped"}:
            trigger = "TOPIC_FINISHED"
        elif (
            item
            and progress
            and (
                remaining_allocation(context, item)
                < self.settings.planning.minimum_question_seconds
                or progress.questions_asked >= item.expected_questions
            )
        ):
            trigger = "ALLOCATION_REACHED"
        elif latest.contradictions:
            trigger = "CONTRADICTION_FOUND"
        if sum(remaining_allocation(context, t) for t in execution_topics(context)) > (
            context.state.remaining_seconds - context.plan.closing_seconds
        ):
            trigger = "TIME_DRIFT"
        cooldown = self.settings.planning.replan_cooldown_questions
        exhausted_agenda = not execution_topics(context) and bool(self._eligible(context))
        if trigger and (
            exhausted_agenda
            or (context.state.question_index - context.last_replan_question_index >= cooldown)
        ):
            await self.revise(context, trigger)
        # Even during cooldown or planner failure, exhausted topics cannot monopolize time.
        for topic in context.plan.topics:
            progress = context.topic_progress[topic.topic_key]
            if progress.status in {"pending", "active"} and remaining_allocation(context, topic) < (
                self.settings.planning.minimum_question_seconds
            ):
                progress.status, progress.reason = "skipped", "TOPIC_TIME_EXHAUSTED"
