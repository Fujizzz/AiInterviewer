"""Responsibilities: Provide an explicitly selected offline ASGI entry point for integration tests.
Implementation: Reuse config.asgi and inject deterministic business and safety fixtures into the
current test process.
Related Modules: config.asgi supplies the application; agent_fixtures supplies the offline model and
reviewer doubles.
Declaration Index:
None
Variable Index:
None
"""

from config.asgi import application  # noqa: F401

from interviews import agent_safety, agent_session
from interviews.tests.agent_fixtures import FixtureBehaviorReviewer, FixtureLLM

agent_session.BackendLLM = FixtureLLM

agent_safety.create_behavior_reviewer = FixtureBehaviorReviewer
