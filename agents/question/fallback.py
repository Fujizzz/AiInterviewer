"""Contextual fallback questions using resume facts, never internal diagnostics."""

import re

from shared.contracts import CandidateProject, PlannedQuestion


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
    def apply(
        self,
        question_plan: PlannedQuestion,
        *,
        project: CandidateProject | None = None,
        generic: bool = False,
    ) -> PlannedQuestion:
        goal = question_plan.information_goal.casefold()
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

        clarification = re.search(
            r"\b(which|what) project\b|\bwhat do you mean\b|哪个项目|什么项目|什么意思",
            question_plan.answer_excerpt,
            re.IGNORECASE,
        )
        if clarification or "which specific project" in goal:
            text = "For that work, which component did you personally implement?"
        elif "clarify the differing accounts" in goal or "clarify how the approach" in goal:
            text = (
                "What role did the approach you mentioned play in this work, "
                "specifically in the part you personally implemented?"
            )
        elif question_plan.dialogue_action in {"clarify", "probe"}:
            if any(word in goal for word in ("architecture", "which model", "架构", "哪个模型")):
                text = "Which model architecture did you use for this part of the work?"
            elif any(word in goal for word in ("baseline", "metric", "measure", "基线", "指标")):
                text = (
                    "How did you measure whether your implementation achieved its intended result?"
                )
            elif any(word in goal for word in ("component", "which part", "模块", "哪一部分")):
                text = "Which specific component did you personally implement, rather than reuse?"
            elif question_plan.dialogue_action == "clarify":
                text = (
                    "Could you describe a specific change you personally made "
                    "to this implementation?"
                )
            else:
                text = (
                    "Could you walk through one concrete implementation step "
                    "you personally completed?"
                )
        else:
            text = (
                "Could you describe the part you personally implemented, "
                "using one concrete example?"
            )
        if not context:
            context = "Thinking about a project from your resume, "
            text = text[0].lower() + text[1:]
        return question_plan.model_copy(update={"text": context + text})
