# Agent module

Owns interview planning and deterministic online decisions.

## Responsibilities

- competency, project and topic selection;
- difficulty and probe control;
- question planning, validation and fallback;
- state transitions and termination;
- RAG routing policy and bounded context assembly;
- atomic decision logging through `RepositoryPort`.

## Boundary

Other modules should call `InterviewAgentService` or implement a port from `agents/ports/`.
They should not import policy classes, domain persistence models or orchestrator internals.

The Agent module does not own HTTP routes, databases, resume parsing, evidence scoring or UI.
