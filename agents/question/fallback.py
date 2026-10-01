"""Contextual fallback questions using resume facts, never internal diagnostics."""

import re

from shared.contracts import CandidateProject, PlannedQuestion

_FOCUSES = {
    "Clarify personal responsibility": "What was your own responsibility in this work?",
    "Identify one personally handled task": "What is one concrete task you handled in this work?",
    "Describe one implementation step": (
        "Could you describe one implementation step you completed in this work?"
    ),
}


def _resume_label(value: str, *, words: int, chars: int) -> str:
    text = " ".join(value.split()).strip(' "“”')
    if re.search(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", text, re.I):
        return ""
    if any(marker in text.casefold() for marker in ("ignore previous", "system prompt")):
        return ""
    text = text.replace("?", "").replace("？", "")
    if len(text) <= chars and len(text.split()) <= words:
        return text
    # Prefer a complete clause, then explicitly abbreviate at a word boundary.
    clause = re.split(r"[;；。]|[.!] (?=[A-Z])|: | — | – ", text)[0]
    if clause != text and len(clause) <= chars and len(clause.split()) <= words:
        return clause
    abbreviated = " ".join(text.split()[:words])
    if len(abbreviated) > chars:
        abbreviated = abbreviated[:chars]
        if " " in abbreviated:
            abbreviated = abbreviated.rsplit(" ", 1)[0]
    return abbreviated.rstrip(",，:：;； ") + "…"


class FallbackQuestionPolicy:
    def prepare_plan(self, plan: PlannedQuestion, interview) -> PlannedQuestion:
        """Choose a modest recovery target, never infer it from diagnostic keywords.

        Record the target we really ask, rather than retaining an abandoned model goal.
        Routing, IDs and budget fields remain the controller's responsibility.
        """
        entries = [
            entry
            for entry in interview.question_history
            if plan.dialogue_action in {"clarify", "probe"}
            and entry.question.thread_id == plan.thread_id
            and entry.question.project_id == plan.project_id
        ]
        focus = "Clarify personal responsibility"
        if entries:
            last = entries[-1]
            focus = "Identify one personally handled task"
            if last.feedback.analysis.answer_scope == "concrete":
                focus = "Describe one implementation step"
        goal = f"{focus}: {plan.topic}"
        return plan.model_copy(update={"information_goal": goal, "intent": goal})

    def apply(
        self,
        question_plan: PlannedQuestion,
        *,
        project: CandidateProject | None = None,
        generic: bool = False,
    ) -> PlannedQuestion:
        name = _resume_label(project.name, words=30, chars=200) if project else ""
        context = f'Let\'s discuss your project "{name}". ' if name else ""
        # Only cite a topic that really belongs to the selected resume project.
        facts = (
            [c.text for c in project.claims] + project.technologies + project.metrics
            if project
            else []
        )
        topic = (
            _resume_label(question_plan.topic, words=35, chars=240)
            if not generic and question_plan.topic in facts
            else ""
        )
        if topic and topic.casefold() != name.casefold():
            context += f'Your resume mentions "{topic}". '

        focus = question_plan.information_goal.split(":", 1)[0]
        text = _FOCUSES.get(focus, _FOCUSES["Clarify personal responsibility"])
        if not context:
            context = "Thinking about a project from your resume, "
            text = text[0].lower() + text[1:]
        return question_plan.model_copy(update={"text": context + text})
