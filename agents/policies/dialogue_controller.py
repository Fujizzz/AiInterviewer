"""One authority for project/topic budgets, closed topics and thread transitions.

Count committed questions from durable thread records, not the truncated answer
history. Opening another topic never resets a project's accumulated usage.
"""

import re

from agents.config import load_agent_settings
from agents.domain.models import DialogueThread
from agents.policies.topic_selector import TopicSelector


class DialogueController:
    def __init__(self, context, settings=None):
        self.context = context
        self.settings = settings or load_agent_settings()

    @property
    def threads(self):
        context = self.context
        values = [*context.closed_threads]
        if context.active_thread:
            values.append(context.active_thread)
        return list({thread.thread_id: thread for thread in values}.values())

    def project_questions(self, project_id):
        return sum(1 + t.follow_up_count for t in self.threads if t.project_id == project_id)

    def topic_questions(self, topic_key):
        return sum(1 + t.follow_up_count for t in self.threads if t.topic_key == topic_key)

    def project_exhausted(self, project_id):
        return self.project_questions(project_id) >= self.context.plan.max_questions_per_project

    def available_topics(self):
        context = self.context
        return {
            topic.topic_key: (project, topic)
            for project in context.candidate_profile.projects
            if not self.project_exhausted(project.project_id)
            for topic in TopicSelector().candidates(project)
            if topic.topic_key not in context.used_topic_keys
            and self.topic_questions(topic.topic_key) < context.plan.max_questions_per_topic
        }

    def followup_block(self):
        context = self.context
        active = context.active_thread
        latest = context.question_history[-1] if context.question_history else None
        if (
            not active
            or not latest
            or latest.question.question_id != context.state.current_question_id
        ):
            return "NO_CURRENT_ANSWER"
        if self.project_exhausted(active.project_id):
            return "PROJECT_QUESTION_LIMIT"
        if self.topic_questions(active.topic_key) >= context.plan.max_questions_per_topic:
            return "TOPIC_QUESTION_LIMIT"
        if latest.feedback.analysis.status in {"explicit_unknown", "refusal"}:
            return "CANDIDATE_STOPPED_THREAD"
        if active.no_information_count >= self.settings.probe.max_no_information_answers:
            return "NO_NEW_INFORMATION"
        if context.state.remaining_seconds <= self.settings.probe.minimum_remaining_seconds:
            return "TIME_LIMIT"
        return None

    @staticmethod
    def _goal_key(value):
        return " ".join(re.findall(r"\w+", value.casefold()))

    def goal_already_asked(self, project_id, goal):
        key = self._goal_key(goal)
        return any(
            self._goal_key(previous) == key
            for thread in self.threads
            if thread.project_id == project_id
            for previous in thread.goals
        )

    def budget_view(self, project_id, topic_key):
        return {
            "project_questions": self.project_questions(project_id),
            "project_limit": self.context.plan.max_questions_per_project,
            "topic_questions": self.topic_questions(topic_key),
            "topic_limit": self.context.plan.max_questions_per_topic,
        }

    def record_question(self, question, *, closed_reason):
        """Mutate only the pending context; repository commit makes it durable atomically."""
        context = self.context
        if self.project_exhausted(question.project_id):
            raise ValueError("PROJECT_QUESTION_LIMIT")
        continuing = question.dialogue_action in {"clarify", "probe"}
        if continuing:
            block = self.followup_block()
            if block:
                raise ValueError(block)
            active = context.active_thread
            if (question.thread_id, question.project_id, question.topic_key) != (
                active.thread_id,
                active.project_id,
                active.topic_key,
            ):
                raise ValueError("FOLLOWUP_MUST_KEEP_CURRENT_THREAD")
            active.follow_up_count += 1
        else:
            if question.topic_key in context.used_topic_keys:
                raise ValueError("UNKNOWN_OR_USED_TOPIC")
            if context.active_thread:
                context.active_thread.closed_reason = closed_reason
                context.closed_threads.append(context.active_thread)
            context.active_thread = DialogueThread(
                thread_id=question.thread_id,
                project_id=question.project_id,
                topic=question.topic,
                topic_key=question.topic_key,
            )
            context.used_topic_keys.append(question.topic_key)
            context.thread_difficulty = question.difficulty
            if question.project_id:
                counts = context.state.project_visit_count
                counts[question.project_id] = counts.get(question.project_id, 0) + 1
        context.active_thread.goals.append(question.information_goal)
