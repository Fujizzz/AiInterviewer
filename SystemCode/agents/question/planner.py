"""Build a fixed conversational target without any competency routing."""

from uuid import uuid4

from shared.contracts import PlannedQuestion, QuestionType, RetrievalSource


class QuestionPlanner:
    prompt_name = "question_planner_v2"

    def plan(
        self,
        *,
        project,
        topic,
        probe,
        difficulty: int,
        thread=None,
        parent_question_id=None,
        answer_excerpt="",
    ) -> PlannedQuestion:
        question_id = str(uuid4())
        continuing = probe.should_probe
        goal = (
            probe.information_goal
            if continuing
            else f"Explain your own implementation of {topic.topic}"
        )
        return PlannedQuestion(
            question_id=question_id,
            project_id=project.project_id if project else None,
            topic=topic.topic,
            topic_key=topic.topic_key,
            difficulty=difficulty,
            probe_depth=probe.next_probe_depth if continuing else 1,
            question_type=QuestionType.IMPLEMENTATION if continuing else QuestionType.DESCRIPTION,
            intent=goal,
            information_goal=goal,
            dialogue_action=probe.dialogue_action if continuing else "new_topic",
            thread_id=thread.thread_id if continuing and thread else question_id,
            parent_question_id=parent_question_id if continuing else None,
            answer_excerpt=answer_excerpt if continuing else "",
            required_context_sources=[RetrievalSource.CANDIDATE],
        )
