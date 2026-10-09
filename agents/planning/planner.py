"""Separate semantic interview planning, evidence coverage and budget compilation."""

from collections import Counter

from agents.model_calls import run_model_call, safe_error_details
from agents.planning.allocation import BudgetAllocator, PlanConstraintError
from agents.planning.coverage import conflicts_for_coverage
from agents.planning.needs import replace_objective, sync_needs
from agents.planning.objectives import (
    evidence_criteria,
    fallback_order,
    prepare_objectives,
    seed_entry_need,
)
from agents.planning.pace import round_cost
from agents.tracing import emit_trace
from shared.contracts.planning import (
    PlanProposal,
    PlanRevision,
    TopicPreference,
    TopicProgress,
)


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
    return [
        item
        for item in context.plan.topics
        if context.topic_progress.get(item.topic_key, TopicProgress()).status
        in {"pending", "active"}
        and remaining_allocation(context, item) > 0
    ]


def planning_view(context):
    projects = {}
    for item in execution_topics(context):
        project = projects.setdefault(
            item.project_id,
            {
                "project_id": item.project_id,
                "budget_seconds": 0,
                "expected_questions": 0,
            },
        )
        project["budget_seconds"] += remaining_allocation(context, item)
        progress = context.topic_progress.get(item.topic_key, TopicProgress())
        project["expected_questions"] += max(0, item.expected_questions - progress.questions_asked)
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
        self.llm, self.settings, self.sync_clock = llm, settings, sync_clock
        self.allocator = BudgetAllocator(settings)

    def _eligible(self, context):
        topics = catalog(context)
        counts = Counter()
        for key, progress in context.topic_progress.items():
            counts[topics.get(key, {}).get("project_id")] += progress.questions_asked
        if context.state.question_index >= context.plan.max_questions:
            return {}
        return {
            key: item
            for key, item in topics.items()
            if context.topic_progress.get(key, TopicProgress()).status
            not in {"completed", "skipped"}
            and context.topic_progress.get(key, TopicProgress()).coverage_status != "sufficient"
            and context.topic_progress.get(key, TopicProgress()).questions_asked
            < context.plan.max_questions_per_topic
            and counts[item["project_id"]] < context.plan.max_questions_per_project
        }

    def _compile(self, proposal, context, eligible):
        prepare_objectives(proposal, {t.topic_key: t for t in context.plan.topics})
        return self.allocator.compile(
            proposal,
            context,
            eligible,
            owners={k: v["project_id"] for k, v in catalog(context).items()},
        )

    def _fallback(self, context, eligible):
        existing = {item.topic_key: item for item in context.plan.topics}
        accepted = (
            context.plan_history[-1].proposal
            if context.plan_history and context.plan_history[-1].proposal
            else None
        )
        preferences = {item.topic_key: item for item in accepted.topics} if accepted else {}
        # Preserve accepted semantic objectives when a replan fails. Initial fallback
        # chooses a bounded subset instead of attempting every resume claim.
        order = [key for key in (preferences or existing) if key in eligible]
        # A finished narrow request without any diagnosed gap is not a usable
        # follow-up scope. Retain its unassessed record, but admit fresh goals.
        latest = context.question_history[-1] if context.question_history else None
        if latest and latest.feedback.analysis.thread_complete:
            order = [
                key
                for key in order
                if key not in context.used_topic_keys
                or context.topic_progress[key].missing_information
            ]
        if not context.plan.version or not order:
            order += [key for key in fallback_order(context, eligible) if key not in order]
            capacity = max(
                1,
                (context.state.remaining_seconds - context.plan.closing_seconds)
                // (2 * round_cost(context, self.settings)),
            )
            order = order[:capacity]
        topics = []
        for key in order:
            if key in preferences:
                topics.append(preferences[key].model_copy(deep=True))
                continue
            previous = existing.get(key)
            label = eligible[key]["label"].replace("?", "").replace("？", "")[:250]
            topics.append(
                TopicPreference(
                    project_id=eligible[key]["project_id"],
                    topic_key=key,
                    objective=previous.objective
                    if previous
                    else f"Assess the implementation mechanism and validation of {label}"[:400],
                    completion_criteria=previous.completion_criteria
                    if previous
                    else evidence_criteria("standard"),
                    depth="standard",
                )
            )
        return PlanProposal(
            base_plan_version=context.plan.version,
            topics=topics,
            reason="Retain accepted objectives within current time and safety capacity",
        )

    async def revise(self, context, trigger, *, speculative=False):
        base_version = context.plan.version
        eligible = self._eligible(context)
        latest = context.question_history[-1] if context.question_history else None
        payload = {
            "trigger": trigger,
            "base_plan_version": base_version,
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
            "latest_analysis": latest.feedback.analysis.model_dump()
            if latest and getattr(latest.feedback, "analysis_status", "valid") == "valid"
            else None,
            "analysis_status": getattr(latest.feedback, "analysis_status", "valid")
            if latest
            else None,
        }
        local_only = base_version > 0 and (
            context.state.remaining_seconds - context.plan.closing_seconds
            < self.settings.planning.timeout_seconds
            + max(
                self.settings.planning.minimum_question_seconds, context.estimated_question_seconds
            )
        )
        emit_trace(
            "planning.local_compilation" if local_only else "planning.requested",
            trigger=trigger,
            plan_version=context.plan.version,
            remaining_seconds=context.state.remaining_seconds,
            payload=payload,
            reason_code="PLANNER_COST_NOT_JUSTIFIED" if local_only else trigger,
        )
        fallback, raw_proposal = False, None
        try:
            if self.llm is None and not local_only:
                raise PlanConstraintError("NO_PLANNER_PROVIDER")
            raw = (
                self._fallback(context, eligible)
                if local_only
                else await run_model_call(
                    lambda: self.llm.generate_structured(
                        prompt_name=self.prompt_name,
                        payload=payload,
                        response_model=PlanProposal,
                    ),
                    operation="interview_planner",
                    timeout_seconds=min(
                        self.settings.planning.timeout_seconds,
                        max(1, context.state.remaining_seconds),
                    ),
                )
            )
            raw_proposal = raw.model_dump() if hasattr(raw, "model_dump") else raw
            proposal = PlanProposal.model_validate(raw_proposal)
            existing_objectives = {t.topic_key: t for t in context.plan.topics}
            for preference in proposal.topics:
                previous = existing_objectives.get(preference.topic_key)
                if previous and preference.objective_change == "preserve":
                    preference.objective = previous.objective
                    preference.completion_criteria = previous.completion_criteria
            legacy = isinstance(raw_proposal, dict) and "reserve_seconds" in raw_proposal
            if legacy:
                proposal.base_plan_version = base_version
            if proposal.base_plan_version != base_version or context.plan.version != base_version:
                raise PlanConstraintError("STALE_OR_MISSING_PLAN_VERSION")
            if self.sync_clock:
                self.sync_clock(context)
            draft, adjustments = self._compile(proposal, context, eligible)
            if isinstance(raw_proposal, dict) and "reserve_seconds" in raw_proposal:
                adjustments.insert(
                    0,
                    {
                        "action": "legacy_budget_recompiled",
                        "requested_seconds": sum(
                            t["budget_seconds"] for t in raw_proposal["topics"]
                        )
                        + raw_proposal["reserve_seconds"]
                        + raw_proposal["closing_seconds"],
                        "remaining_seconds": context.state.remaining_seconds,
                        "reason": "MODEL_SECONDS_ARE_PREFERENCES_NOT_AUTHORITY",
                        "base_plan_version": base_version,
                    },
                )
        except Exception as error:
            if self.sync_clock:
                self.sync_clock(context)
            fallback = True
            emit_trace(
                "planning.fallback",
                trigger=trigger,
                proposal=raw_proposal,
                remaining_seconds=context.state.remaining_seconds,
                reason_code=getattr(error, "code", "INVALID_PLANNER_OUTPUT"),
                topic_key=getattr(error, "topic_key", None),
                **safe_error_details(error),
            )
            proposal = self._fallback(context, eligible)
            draft, adjustments = self._compile(
                proposal, context, eligible if proposal.topics else {}
            )
        self.accept(
            context,
            proposal,
            trigger,
            fallback=fallback,
            local_only=local_only,
            raw_proposal=raw_proposal,
            speculative=speculative,
        )

    def accept(
        self,
        context,
        proposal,
        trigger,
        *,
        fallback=False,
        local_only=False,
        raw_proposal=None,
        speculative=False,
    ):
        """Compile model preferences against current facts/time; never restore a snapshot."""
        base_version = context.plan.version
        latest = context.question_history[-1] if context.question_history else None
        eligible = self._eligible(context)
        proposal = proposal.model_copy(deep=True)
        proposal.topics = [t for t in proposal.topics if t.topic_key in eligible]
        existing = {t.topic_key: t for t in context.plan.topics}
        for t in proposal.topics:
            if t.topic_key in existing and t.objective_change == "preserve":
                t.objective = existing[t.topic_key].objective
                t.completion_criteria = existing[t.topic_key].completion_criteria
        if self.sync_clock:
            self.sync_clock(context)
        draft, adjustments = self._compile(proposal, context, eligible)
        prior = context.plan_history[-1].proposal if context.plan_history else None
        before = (
            {item.topic_key: (index, item.model_dump()) for index, item in enumerate(prior.topics)}
            if prior
            else {}
        )
        after = {
            item.topic_key: (index, item.model_dump()) for index, item in enumerate(proposal.topics)
        }
        changes = [
            {
                "topic_key": key,
                "action": "add" if key not in before else "defer" if key not in after else "update",
                "before": before.get(key),
                "after": after.get(key),
            }
            for key in dict.fromkeys([*before, *after])
            if before.get(key) != after.get(key)
        ]
        allocated = {item.topic_key for item in draft.topics}
        existing = {item.topic_key: item for item in context.plan.topics}
        retained = []
        for item in context.plan.topics:
            progress = context.topic_progress[item.topic_key]
            if item.topic_key not in allocated:
                if progress.status in {"pending", "active"}:
                    progress.status, progress.reason = "deferred", "REMOVED_BY_REPLAN"
                retained.append(item)
        adjusted = []
        for item in draft.topics:
            progress = context.topic_progress.setdefault(item.topic_key, TopicProgress())
            progress.objective_id = progress.objective_id or item.topic_key
            previous = existing.get(item.topic_key)
            preference = next(t for t in proposal.topics if t.topic_key == item.topic_key)
            if previous and preference.objective_change == "replace":
                replace_objective(progress)
            if not previous:
                seed_entry_need(progress, item.topic_key, eligible[item.topic_key]["label"])
            if progress.status == "deferred":
                progress.status, progress.reason = "pending", "RESUMED_BY_PLAN"
            adjusted.append(
                item.model_copy(
                    update={
                        "budget_seconds": item.budget_seconds + progress.elapsed_seconds,
                        "expected_questions": item.expected_questions + progress.questions_asked,
                    }
                )
            )
        context.plan.topics = adjusted + retained
        context.plan.reserve_seconds, context.plan.closing_seconds = (
            draft.reserve_seconds,
            draft.closing_seconds,
        )
        context.plan.version += 1
        context.last_replan_question_index = context.state.question_index
        revision = PlanRevision(
            version=context.plan.version,
            elapsed_seconds=context.state.elapsed_seconds,
            trigger=trigger,
            reason=proposal.reason,
            answer_id=latest.answer.answer_id if latest and latest.answer else None,
            fallback_used=fallback,
            topics=context.plan.topics,
            reserve_seconds=draft.reserve_seconds,
            closing_seconds=draft.closing_seconds,
            proposal=proposal,
            adjustments=adjustments,
            remaining_at_compile=context.state.remaining_seconds,
            base_plan_version=base_version,
            semantic_changes=changes,
            source="fallback" if fallback else "local_compilation" if local_only else "model",
        )
        context.plan_history.append(revision.model_copy(deep=True))
        emit_trace(
            "planning.proposal_compiled" if speculative else "planning.compiled",
            trigger=trigger,
            original_proposal=raw_proposal,
            proposal=proposal.model_dump(),
            adjustments=adjustments,
            remaining_seconds=context.state.remaining_seconds,
            allocated_seconds=sum(t.budget_seconds for t in draft.topics),
            reserve_seconds=draft.reserve_seconds,
            closing_seconds=draft.closing_seconds,
        )
        emit_trace(
            "planning.proposal_revised" if speculative else "planning.revised",
            revision=revision.model_dump(mode="json"),
        )

    def feedback(self, context, question, feedback, elapsed_seconds):
        progress = context.topic_progress.get(question.topic_key)
        if progress is None:
            return
        if elapsed_seconds > 0:
            context.estimated_question_seconds = 0.7 * context.estimated_question_seconds + 0.3 * (
                elapsed_seconds + context.last_question_generation_seconds
            )
        if getattr(feedback, "analysis_status", "valid") != "valid":
            return
        analysis = feedback.analysis
        latest = context.question_history[-1] if context.question_history else None
        answer = (
            latest.answer
            if latest and latest.question.question_id == question.question_id
            else None
        )
        grounded = answer is not None and bool(answer.text.strip())
        progress.objective_id = progress.objective_id or question.topic_key
        terminal = analysis.status in {"explicit_unknown", "refusal"}
        for need in progress.information_needs:
            if need.need_id == question.need_id and grounded:
                if answer.answer_id not in need.source_answer_ids:
                    need.source_answer_ids.append(answer.answer_id)
                if terminal:
                    need.status = "blocked"
                elif analysis.thread_complete and need.target not in progress.missing_information:
                    need.status = "satisfied"
        # A corrected claim cannot remain the basis of an objective completion.
        # Fresh coverage below may establish the goal again using the new answer.
        for item in context.plan.topics:
            if not grounded or item.project_id != question.project_id:
                continue
            target = context.topic_progress.get(item.topic_key)
            if target is None:
                continue
            retained = [
                record
                for record in target.coverage_evidence
                if not any(
                    relation.kind in {"supersedes", "disputes"}
                    and relation.current_quote in answer.text
                    and relation.earlier_answer_id == record["answer_id"]
                    and any(
                        relation.earlier_quote in quote or quote in relation.earlier_quote
                        for quote in record["supporting_quotes"]
                    )
                    for relation in analysis.answer_relations
                )
            ]
            if len(retained) != len(target.coverage_evidence):
                target.coverage_evidence = retained
                target.evidence_answer_ids = list(
                    dict.fromkeys(record["answer_id"] for record in retained)
                )
                target.coverage_status = "partial"
                target.missing_information = list(
                    dict.fromkeys(
                        [
                            *target.missing_information,
                            "Reassess corrected evidence: " + item.objective,
                        ]
                    )
                )
                sync_needs(target, item.topic_key, source_answer_id=answer.answer_id)
                if target.status == "completed":
                    target.status, target.reason = "deferred", "EVIDENCE_CORRECTED"
        # A completed narrow question says nothing about the broader agenda criteria.
        # Coverage is updated only from separately grounded objective observations.
        if grounded and not terminal and feedback.objective_coverage_status == "valid":
            known = {
                (context.topic_progress[item.topic_key].objective_id or item.topic_key): item
                for item in context.plan.topics
                if item.project_id == question.project_id
                and item.topic_key in context.topic_progress
            }
            for update in feedback.objective_coverage:
                item = known.get(update.objective_id)
                if (
                    item is None
                    or update.answer_id != answer.answer_id
                    or not update.supporting_quotes
                    or any(
                        not quote.strip() or quote not in answer.text
                        for quote in update.supporting_quotes
                    )
                ):
                    continue
                target = context.topic_progress[item.topic_key]
                if target.status == "skipped":
                    continue  # Candidate boundaries cannot be reopened by an evaluation.
                missing = list(dict.fromkeys(update.missing_information))
                if item.topic_key == question.topic_key:
                    missing = list(
                        dict.fromkeys(
                            [
                                *missing,
                                *conflicts_for_coverage(
                                    analysis, update.supporting_quotes, target.coverage_evidence
                                ),
                            ]
                        )
                    )
                sufficient = update.coverage_status == "sufficient" and not missing
                target.coverage_status = "sufficient" if sufficient else "partial"
                target.missing_information = missing
                sync_needs(target, item.topic_key, source_answer_id=answer.answer_id)
                target.evidence_answer_ids = list(
                    dict.fromkeys(
                        [
                            *target.evidence_answer_ids,
                            answer.answer_id,
                        ]
                    )
                )
                proof = {
                    "answer_id": answer.answer_id,
                    "supporting_quotes": list(update.supporting_quotes),
                    "supporting_segment_ids": list(update.supporting_segment_ids),
                }
                target.coverage_evidence = [
                    record
                    for record in target.coverage_evidence
                    if record["answer_id"] != answer.answer_id
                ] + [proof]
                if sufficient:
                    target.status, target.reason = "completed", "OBJECTIVE_COMPLETED"
                elif target.status == "completed":
                    target.status, target.reason = "deferred", "OBJECTIVE_REQUIRES_REVIEW"
        no_information = (
            context.active_thread
            and context.active_thread.no_information_count
            >= self.settings.probe.max_no_information_answers
        )
        if terminal:
            progress.status, progress.reason = "skipped", "CANDIDATE_STOPPED"
        elif no_information and progress.coverage_status != "sufficient":
            progress.status, progress.reason = (
                "skipped",
                "CANDIDATE_STOPPED" if terminal else "NO_NEW_INFORMATION",
            )

    async def review(self, context, *, defer=None):
        if not context.plan.planning_enabled or not context.question_history:
            return
        active = context.active_thread
        progress = context.topic_progress.get(active.topic_key) if active else None
        item = next(
            (t for t in context.plan.topics if active and t.topic_key == active.topic_key), None
        )
        latest = context.question_history[-1].feedback
        valid_analysis = getattr(latest, "analysis_status", "valid") == "valid"
        trigger = None
        if progress and progress.status in {"completed", "skipped"}:
            trigger = "TOPIC_FINISHED"
        elif (
            item
            and progress
            and remaining_allocation(context, item) < round_cost(context, self.settings)
        ):
            trigger = "ALLOCATION_REACHED"
        elif valid_analysis and latest.analysis.contradictions:
            trigger = "CONTRADICTION_FOUND"
        if (
            sum(remaining_allocation(context, t) for t in execution_topics(context))
            > context.state.remaining_seconds - context.plan.closing_seconds
        ):
            trigger = "TIME_DRIFT"
        from agents.policies.dialogue_controller import DialogueController

        controller = DialogueController(context, self.settings)
        can_continue = (
            active
            and controller.followup_block() is None
            and (not latest.analysis.thread_complete or (progress and progress.missing_information))
        )
        exhausted = (
            not controller.available_topics() and not can_continue and bool(self._eligible(context))
        )
        if exhausted:
            trigger = trigger or "AGENDA_EXHAUSTED"
        if trigger and (
            exhausted
            or context.state.question_index - context.last_replan_question_index
            >= self.settings.planning.replan_cooldown_questions
        ):
            if defer is None:
                await self.revise(context, trigger)
            else:
                defer(context, trigger)
                if exhausted or trigger == "TIME_DRIFT":
                    self.accept(
                        context,
                        self._fallback(context, self._eligible(context)),
                        trigger,
                        local_only=True,
                    )
        for topic in context.plan.topics:
            progress = context.topic_progress[topic.topic_key]
            if progress.status in {"pending", "active"} and remaining_allocation(
                context, topic
            ) < round_cost(context, self.settings):
                progress.status, progress.reason = "deferred", "TOPIC_TIME_EXHAUSTED"
