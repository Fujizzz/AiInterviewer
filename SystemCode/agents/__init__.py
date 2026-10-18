"""Adaptive interview Agent and deterministic orchestrator."""

__all__ = ["InterviewAgentService"]


def __getattr__(name):
    # Evaluation uses shared model-call utilities without loading the orchestrator.
    if name == "InterviewAgentService":
        from agents.orchestrator.service import InterviewAgentService

        return InterviewAgentService
    raise AttributeError(name)
