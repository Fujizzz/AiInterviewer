"""Responsibilities: Provide offline business/safety model fixtures and explicit test-only
persistence helpers.
Implementation: Validate business outputs with production contracts; the safety reviewer always
approves and only verifies wiring.
Related Modules: Agent protocol, persistence, and phase tests use these fixtures; test_agent_safety
covers rejection and failure behavior.
Declaration Index:
- FixtureBehaviorReviewer: Explicit offline reviewer that returns a complete approval result.
- FixtureBehaviorReviewer.assess: Produce an assessment covering every requested safety requirement.
- SafetyTestMixin: Isolate business regression tests from the external safety service.
- SafetyTestMixin.setUp: Patch the safety port for one test lifecycle and register cleanup.
- complete_fixture_request: Attach an explicit test receipt and persist an offline result for direct
  repository tests.
- FixtureLLM: Generate deterministic offline outputs validated by the real Pydantic schemas.
- FixtureLLM.__init__: Initialize per-instance call tracking and resource state without opening a
  model connection.
- FixtureLLM.__call__: Return schema-specific deterministic outputs and reject unknown schema
  requests.
- FixtureLLM.close: Mark fixture cleanup for lifecycle assertions.
Variable Index:
- RESUME: Synthetic resume text used only by offline tests.
- ANSWER: Synthetic answer text associated with the fixture project.

Constraints:
FixtureLLM.calls records requested schemas and closed records cleanup. Fixture results do not
establish real model capability and are not used by the production path.
"""

from unittest.mock import patch

from agents.question.react import QuestionAgentDecision
from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative
from interviews.agent_models import AgentInterview
from interviews.agent_records import complete_request
from interviews.agent_safety import make_output_receipt
from shared.contracts.behavior import BehaviorAssessment
from tests.agent.mocks.dialogue_output import plan_for, selection_for
from tests.evaluation.port_helpers import SCHEMAS, evaluation_output

RESUME = "Alex built a Python log analysis pipeline, tested malformed records with pytest."
ANSWER = "I implemented a bounded-memory parser and tested malformed records separately."


class FixtureBehaviorReviewer:
    """Functionality: Isolate business regressions from the external behavior-review model.
    Inputs: A behavior assessment request.
    Outputs: A compliant result covering every boundary requirement.
    Logic: Return a deterministic approval without changing the request.
    Constraints: This fixture verifies integration wiring only and cannot establish real safety
    detection quality.
    """

    async def assess(self, request):
        """Functionality: Return an approval covering all requested behavior requirements.
        Inputs: A production-shaped behavior review request.
        Outputs: A compliant BehaviorAssessment with all requirement identifiers checked.
        Logic: Build the response from the request boundary without external calls.
        Constraints: Does not modify the request or access a database; approval is synthetic.
        """
        return BehaviorAssessment(
            verdict="compliant",
            checked_requirement_ids=tuple(r.requirement_id for r in request.boundary.requirements),
            violated_requirement_ids=(),
        )


class SafetyTestMixin:
    """Functionality: Provide an explicit offline safety reviewer to legacy business regression
    tests.
    Inputs: The test framework lifecycle.
    Outputs: A patched safety port registered for restoration after the test.
    Logic: Start a unittest patch in setUp and register its stop method as cleanup.
    Constraints: Production entry points do not load this mixin.
    """

    def setUp(self):
        """Functionality: Patch the behavior-review port for this test lifecycle.
        Inputs: The inherited test setup state.
        Outputs: None; registers patch cleanup with unittest.
        Logic: Run the parent setup, start the fixture reviewer patch, and register its stop
        callback.
        Constraints: The patch is scoped to the test and is always restored by cleanup.
        """
        super().setUp()
        patcher = patch("interviews.agent_safety.create_behavior_reviewer", FixtureBehaviorReviewer)
        patcher.start()
        self.addCleanup(patcher.stop)


async def complete_fixture_request(interview_id, request_id, result):
    """Functionality: Persist a direct Agent-test result with an explicit synthetic approval
    receipt.
    Inputs: Interview identifier, request identifier, and the offline business result.
    Outputs: Completes after the request record is updated.
    Logic: Load interview ownership/version state, create a test receipt, and call the existing
    completion helper.
    Constraints: This bypass is test-only and does not claim a real safety review; gateway checks
    are tested separately.
    """
    record = await AgentInterview.objects.aget(id=interview_id)
    await complete_request(
        interview_id,
        request_id,
        result,
        owner_id=record.owner_id,
        receipt=make_output_receipt(result, request_id, record.state_version),
    )


class FixtureLLM:
    """Functionality: Provide deterministic schema-valid model outputs for offline tests.
    Inputs: The per-call prompt, data, and requested Pydantic schema.
    Outputs: A schema instance for supported requests.
    Logic: Record requested schemas and build synthetic results for the Agent pipeline.
    Constraints: Unknown schema requests fail immediately and no model connection is created.
    """

    def __init__(self, *, interview_id=None):
        """Functionality: Initialize per-instance fixture call and cleanup state.
        Inputs: An optional interview identifier accepted for interface compatibility.
        Outputs: Empty call history and an open fixture state.
        Logic: Initialize local attributes only.
        Constraints: No external model connection is opened.
        """
        self.calls = []
        self.closed = False

    def __call__(self, prompt, data, schema):
        """Functionality: Return deterministic fixture data for supported production schemas.
        Inputs: Prompt text, request data, and the requested output schema.
        Outputs: An instance validated against the requested schema.
        Logic: Record the schema, construct a schema-specific synthetic payload, and validate it.
        Constraints: Unsupported schemas raise AssertionError; the fixture does not evaluate
        semantic quality.
        """
        # Scripted semantic pass; this fixture does not evaluate question quality.
        if schema.__name__ == "QuestionQualityReview":
            return schema(issues=[])
        self.calls.append(schema)
        if schema in SCHEMAS:
            return evaluation_output(prompt, data, schema)
        if schema is ResumeExtraction:
            output = {
                "candidate_name": "Alex",
                "skills": ["Python", "pytest"],
                "projects": [
                    {
                        "name": "Log Analysis Pipeline",
                        "domain": "data engineering",
                        "description": RESUME,
                        "technologies": ["Python", "pytest"],
                        "claims": ["Handled malformed records separately."],
                        "metrics": [],
                    }
                ],
            }
        elif schema in (GeneratedText, QuestionAgentDecision):
            selection = selection_for(data) if "dialogue_state" in data else None
            plan = plan_for(data, selection) if selection else data["question_plan"]
            topic = str(plan["topic"]).rstrip(".,;:")
            output = {"text": f"What did you personally implement for {topic}, and why?"}
        elif schema is AnswerEvidence:
            output = {
                "answer_relevance": 0.9,
                "evidence_strength": 0.8,
                "analysis": {
                    "status": "substantive",
                    "new_information": True,
                    "thread_complete": True,
                },
                "dimensions": [
                    {
                        "competency": "ownership",
                        "observation": "supported",
                        "quote": data["answer"],
                        "fact": data["answer"],
                        "rationale": "Describes personal implementation",
                        "rubric_level": 3,
                        "strength": 0.8,
                    }
                ],
            }
        elif schema is ReportNarrative:
            output = {
                "strengths": ["Explained implementation."],
                "weaknesses": ["Some competencies remain untested."],
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema.__name__}")
        if schema is QuestionAgentDecision:
            output.update(action="final", topic=None, limit=None, selection=selection)
        return schema.model_validate(output)

    def close(self):
        """Functionality: Mark fixture cleanup for connection-lifecycle assertions.
        Inputs: The fixture instance state.
        Outputs: None; sets closed to True.
        Logic: Update only the local cleanup marker.
        Constraints: No external resource is released because no connection was opened.
        """
        self.closed = True
