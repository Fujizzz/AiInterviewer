"""Test-only session composition: retain live answer decisions, omit scoring and reports.

Declaration Index:
- create_live_session: Select a question-only test session or the normal backend session.
- create_live_session.QuestionOnlySession: Keep production question handlers and persistence.
- create_live_session.QuestionOnlySession.__init__: Inject live analysis without background scoring.
- create_live_session.QuestionOnlySession._response: Finish the test without a score report.
"""


def create_live_session(*, include_evaluation=False, llm=None):
    """Compose test exclusions locally; production sessions and environment stay unchanged."""
    from interviews.agent_public_output import public_question_history
    from interviews.agent_session import AgentSession

    from app.adapters.assessment import RealtimeDecisionAdapter
    from shared.contracts import InterviewActionType

    if include_evaluation:
        return AgentSession(llm=llm)

    class QuestionOnlySession(AgentSession):
        def __init__(self):
            super().__init__(llm=llm)
            self.app.evaluation = RealtimeDecisionAdapter(self.app.llm, self.app.repository)

        async def _response(self):
            self.action = await self.app._advance_non_question_actions(
                self.service, self.interview_id, self.action
            )
            if self.action.type != InterviewActionType.FINISH:
                return await super()._response()
            context = await self.app.repository.get_interview_context(self.interview_id)
            return {
                "type": "finished",
                "result": {
                    "interview_id": self.interview_id,
                    "test_mode": "questions_only",
                    "interview_finished": context.state.status == "finished",
                    "question_history": public_question_history(self.history),
                    "evaluation_tested": False,
                    "report_generated": False,
                },
            }

    return QuestionOnlySession()
