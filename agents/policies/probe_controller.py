"""Decide whether to continue the current thread before selecting anything else."""

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import InterviewContext, ProbeDecision
from agents.policies.dialogue_controller import DialogueController


class ProbeController:
    def __init__(self, settings: AgentSettings | None = None):
        self._settings = settings or load_agent_settings()

    def decide(self, *, context: InterviewContext) -> ProbeDecision:
        controller = DialogueController(context, self._settings)
        thread = context.active_thread
        history = context.question_history
        reason = "FIRST_QUESTION"
        if (
            thread
            and history
            and history[-1].question.question_id == context.state.current_question_id
        ):
            analysis = history[-1].feedback.analysis
            reason = "THREAD_COMPLETE"
            block = controller.followup_block()
            if block:
                reason = block
            else:
                action, goal = "", ""
                if analysis.contradictions:
                    action = "clarify"
                    goal = "Clarify the differing accounts: " + analysis.contradictions[0]
                elif analysis.uncertainties:
                    action, goal = "clarify", "Clarify how the approach was used in your own work"
                elif analysis.status in {"partial", "non_answer"}:
                    action = "clarify"
                    goal = (
                        analysis.missing_information
                        or ["Give one concrete detail about what you personally did"]
                    )[0]
                elif analysis.missing_information:
                    action, goal = "probe", analysis.missing_information[0]
                elif not analysis.thread_complete:
                    action, goal = "probe", "Explain one concrete implementation step in this work"
                if action:
                    if controller.goal_already_asked(
                        thread.project_id, goal, allow_current_clarification=True
                    ):
                        reason = "INFORMATION_GOAL_ALREADY_ASKED"
                    else:
                        return ProbeDecision(
                            should_probe=True,
                            next_probe_depth=thread.follow_up_count + 2,
                            reason_code="CLARIFY_LAST_ANSWER"
                            if action == "clarify"
                            else "DEEPEN_LAST_ANSWER",
                            dialogue_action=action,
                            information_goal=goal,
                        )
        return ProbeDecision(should_probe=False, next_probe_depth=1, reason_code=reason)
