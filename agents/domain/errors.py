"""Small, stable error hierarchy for the Agent boundary."""


class AgentError(Exception):
    """Base class for errors raised by the Agent module."""

    code = "AGENT_ERROR"
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidAgentState(AgentError):
    code = "STATE_INVALID"


class StateConflictError(AgentError):
    code = "STATE_CONFLICT"
    retryable = True


class ContractVersionError(AgentError):
    code = "CONTRACT_VERSION_UNSUPPORTED"


class RepositoryUnavailable(AgentError):
    code = "REPOSITORY_UNAVAILABLE"
    retryable = True


class RAGTimeout(AgentError):
    code = "RAG_TIMEOUT"
    retryable = True


class LLMTimeout(AgentError):
    code = "LLM_TIMEOUT"
    retryable = True
