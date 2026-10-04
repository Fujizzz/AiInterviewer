"""Responsibilities: verify real ASGI, input binding, output checking, database, and historical
public boundary closure.
Implementation: replace only business and safety models; retain actual state machine, gateway,
transaction, protocol, and HTTP history handling. Explicitly enable safety for this test class,
independently of a developer's temporary environment switch.
Related Modules: agent_safety/agent_socket/agent_records/agent_history; reuse existing deterministic
business test data.

Declaration Index:
- ScriptedReviewer: controlled review port, covers rejection, exception, cancellation, and full
  coverage verification.
- ScriptedReviewer.__init__: record target output, fault modes, and synchronization barrier.
- ScriptedReviewer.assess: record real review request, provide conclusion or wait for cancellation
  according to mode.
- ScriptedReviewer.aclose: Record explicit gateway lifecycle release without external resources.
- IOSafetyTests: closed-loop testing in isolated database, does not prove real model detection
  effectiveness.
- IOSafetyTests.setUp: inject explicit business/safety stand-ins, register cleanup.
- IOSafetyTests.terminal: read progress until business result or fixed error.
- IOSafetyTests.close_error: verify termination close and no subsequent output.
- IOSafetyTests.test_allowed_round_trip_and_sources: verify four outputs, full package review,
  source tagging, and historical consistency.
- IOSafetyTests.test_denial_at_each_output: reject at each output, do not save successful text, and
  prevent bypassing history.
- IOSafetyTests.test_finished_confidentiality_denial: reject a final report for confidentiality and
  keep its body unavailable in storage and history.
- IOSafetyTests.test_generated_narrative_still_requires_review: preserve generated narrative text
  in the checked proposal and stop publication when the reviewer denies it.
- IOSafetyTests.test_generated_narrative_still_requires_review.private_narrative: inject synthetic
  confidential report text while retaining deterministic business fixtures elsewhere.
- IOSafetyTests.test_private_job_field_stops_before_review: reject an unknown public job field at
  the gateway before final semantic review, delivery or persistence.
- IOSafetyTests.test_review_failures_stop_output: exceptions, timeouts, and incomplete results do
  not pass through, no retry.
- IOSafetyTests.test_real_deadline_stops_output: wait for real five-second budget, verify task
  cancellation and error closure.
- IOSafetyTests.test_cancel_or_disconnect_during_review: cancel/disconnect during pending review, no
  delayed output generated.
- IOSafetyTests.test_input_budget_precedes_business_model: input over budget rejected before any
  business model call.
- IOSafetyTests.test_ownership_and_commit_version: mismatched ownership cannot bind, expired
  credentials cannot submit.
- IOSafetyTests.test_history_requires_intact_receipt: unverified or tampered results cannot be
  publicly exposed via either history interface.
- IOSafetyTests.test_history_rejects_private_job_profiles: reject intact legacy receipts containing
  private or malformed job data while retaining public and missing-job historical responses.
- IOSafetyTests.test_progress_cannot_carry_model_text: fixed progress field cannot carry arbitrary
  text.

Variable Index:
None
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ai_security.errors import SecurityContextChanged
from app.reporting.final_report import ReportNarrative
from interviews.agent_models import AgentInterview, AgentRequest
from interviews.agent_records import complete_request, reserve_request
from interviews.agent_safety import (
    InterviewIOGateway,
    IOSafetyError,
    make_output_receipt,
    validate_progress,
    verified_response,
)
from interviews.agent_socket import Prepare
from shared.contracts.behavior import BehaviorAssessment

from .agent_fixtures import ANSWER, RESUME, FixtureBehaviorReviewer, FixtureLLM
from .test_agent_progress import connect, disconnect, send_command


class ScriptedReviewer:
    """Function: controllable review fault; logic: select result based on operation; constraint:
    simulate port, not real safety judgment.
    """

    def __init__(
        self, target=None, mode="allow", violated_requirement_id="TASK_SCOPE", denied_text=None
    ):
        """Input target, mode, violation ID and optional proposal marker; record requests and
        synchronization events without network calls. Marker matching only controls this synthetic
        test port and does not establish semantic detection quality.
        """
        self.target, self.mode = target, mode
        self.violated_requirement_id, self.denied_text = violated_requirement_id, denied_text
        self.requests = []
        self.entered, self.cancelled = asyncio.Event(), asyncio.Event()
        self.close_calls = 0

    async def aclose(self):
        """Functionality: Record test pool release. Inputs: Instance close counter. Outputs: None.
        Logic: Increment once per explicit gateway close. Constraints: No SDK/network resources;
        the fixed stand-in may be reused by other test connections and is not a real client pool.
        """
        self.close_calls += 1

    async def assess(self, request):
        """Input real request; record copy, return complete/incomplete conclusion or inject fault;
        wait mode ends only by cancellation.
        """
        self.requests.append(request.model_copy(deep=True))
        requirements = tuple(r.requirement_id for r in request.boundary.requirements)
        if self.target in (None, request.proposal.operation):
            if self.mode == "wait":
                self.entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
            if self.mode == "error":
                raise RuntimeError("private-provider-body")
            if self.mode == "timeout":
                raise TimeoutError("private-provider-body")
            if self.mode == "incomplete":
                requirements = requirements[:1]
            if self.mode == "uncertain":
                return BehaviorAssessment(
                    verdict="uncertain",
                    checked_requirement_ids=requirements,
                    violated_requirement_ids=(),
                )
            if self.mode == "deny" and (
                self.denied_text is None or self.denied_text in request.proposal.content.text
            ):
                return BehaviorAssessment(
                    verdict="noncompliant",
                    checked_requirement_ids=requirements,
                    violated_requirement_ids=(self.violated_requirement_id,),
                )
        return BehaviorAssessment(
            verdict="compliant", checked_requirement_ids=requirements, violated_requirement_ids=()
        )


@override_settings(AI_SECURITY_ENABLED=True)
class IOSafetyTests(TransactionTestCase):
    """Function: regression of real closed loop; logic: independent database and controllable port;
    constraint: no change to production model parameters or five-second budget.
    """

    def setUp(self):
        """No external input; construct stand-ins one-by-one and register patch cleanup; use real
        implementation outside port.
        """
        super().setUp()
        self.reviewer, self.llm = ScriptedReviewer(), FixtureLLM()
        for name, instance in (
            ("interviews.agent_session.BackendLLM", self.llm),
            ("interviews.agent_safety.create_behavior_reviewer", self.reviewer),
        ):
            patcher = patch(name, return_value=instance)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def terminal(self, comm, kind, *, timeout=3):
        """Input channel, expected type, and test wait time; skip fixed control messages, return
        error immediately for assertion.
        """
        for _ in range(50):
            packet = await comm.receive_output(timeout=timeout)
            self.assertEqual(packet["type"], "websocket.send")
            value = json.loads(packet["text"])
            if value["type"] in (kind, "error"):
                return value
            self.assertIn(value["type"], {"started", "progress", "assessment"})
        self.fail("missing terminal response")

    async def close_error(self, comm, response, code):
        """Input terminal error and expected code; check fixed message, close code, task end, and
        empty send queue.
        """
        self.assertEqual(response["type"], "error")
        self.assertEqual(response["code"], code)
        self.assertNotIn("private-provider-body", json.dumps(response))
        self.assertEqual(
            (await comm.receive_output())["code"], 1008 if code == "security_denied" else 1011
        )
        await comm.wait()
        self.assertTrue(comm.output_queue.empty())

    async def test_allowed_round_trip_and_sources(self):
        """Synthetic resume → two questions/evaluations → preliminary scoring → report; fully
        inspected text matches network/history, sources not confused. Public job data excludes
        scoring weights and orchestration fields while canonical state retains plans, evaluator
        control flags, weights and fixture scores.
        """
        comm = await connect()
        await send_command(comm, "prepare", resume_text=RESUME)
        self.assertEqual((await self.terminal(comm, "prepared"))["type"], "prepared")
        await send_command(comm, "start", resume_text=RESUME, max_questions=2)
        first = await self.terminal(comm, "question")
        await send_command(
            comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
        )
        second = await self.terminal(comm, "question")
        self.assertIsNotNone(second["last_evaluation"])
        rid = await send_command(
            comm, "answer", question_id=second["question"]["question_id"], answer_text=ANSWER
        )
        finished = await self.terminal(comm, "finished")
        self.assertEqual((await comm.receive_output())["code"], 1000)
        await comm.wait()
        self.assertEqual(
            [r.proposal.operation for r in self.reviewer.requests],
            [
                "publish_prepared",
                "publish_question",
                "publish_question",
                "publish_assessment",
                "publish_finished",
            ],
        )
        question_check = self.reviewer.requests[2]
        self.assertEqual(
            json.loads(question_check.proposal.content.text),
            {k: v for k, v in second.items() if k != "request_id"},
        )
        self.assertIn("user", [e.source for e in question_check.evidence])
        self.assertEqual(
            [e.source for e in self.reviewer.requests[1].evidence][:2], ["resume", "job"]
        )
        self.assertEqual(question_check.boundary.session_id, first["interview_id"])
        final_check = self.reviewer.requests[-1]
        reviewed_final = json.loads(final_check.proposal.content.text)
        self.assertEqual(reviewed_final, {k: v for k, v in finished.items() if k != "request_id"})
        self.assertEqual(
            set(reviewed_final["result"]),
            {
                "interview_id",
                "candidate_name",
                "candidate_profile",
                "job_profile",
                "question_history",
                "interview_state",
                "interview_finished",
                "final_report",
                "report_narrative_status",
            },
        )
        self.assertEqual(
            set(reviewed_final["result"]["interview_state"]),
            {
                "contract_version",
                "interview_id",
                "state_version",
                "status",
                "stage",
                "question_index",
                "remaining_seconds",
                "elapsed_seconds",
            },
        )
        public_history = reviewed_final["result"]["question_history"]
        self.assertEqual(len(public_history), 2)
        for entry in public_history:
            self.assertNotIn("thread_id", entry)
            self.assertNotIn("project_id", entry)
            self.assertEqual(
                set(entry["evaluation"]["analysis"]),
                {"status", "summary", "missing_information", "contradictions", "uncertainties"},
            )
            self.assertTrue(entry["evaluation"]["dimensions"])
            self.assertEqual(entry["evaluation"]["dimensions"][0]["rubric_level"], 3)
            self.assertEqual(entry["evaluation"]["dimensions"][0]["quote"], ANSWER)
            self.assertEqual(
                entry["evaluation"]["dimensions"][0]["rationale"],
                "Describes personal implementation",
            )
        self.assertNotIn("competency_importance", reviewed_final["result"]["job_profile"])
        self.assertNotIn("competency_importance", finished["result"]["job_profile"])
        internal = await AgentInterview.objects.aget(id=first["interview_id"])
        self.assertTrue(internal.context["job_profile"]["competency_importance"])
        self.assertTrue(internal.context["plan"])
        self.assertTrue(internal.context["plan_history"])
        self.assertTrue(internal.context["topic_progress"])
        self.assertTrue(
            internal.context["question_history"][0]["feedback"]["analysis"]["new_information"]
        )
        self.assertEqual(
            finished["result"]["job_profile"],
            {
                key: value
                for key, value in internal.context["job_profile"].items()
                if key != "competency_importance"
            },
        )
        self.assertEqual(internal.context["state"]["competencies"]["ownership"]["score"], 3.0)
        self.assertEqual(finished["result"]["final_report"]["overall_score"], 3.0)
        self.assertEqual(
            finished["result"]["final_report"]["competencies"]["ownership"]["score"], 3.0
        )
        base = f"/api/agent-interviews/{first['interview_id']}/"
        detail = (await self.async_client.get(base)).json()
        self.assertEqual(detail["job_profile"], finished["result"]["job_profile"])
        self.assertNotIn("competency_importance", detail["job_profile"])
        self.assertEqual(detail["final_report"], finished["result"]["final_report"])
        self.assertEqual(detail["questions"][0]["answer"]["evaluation"], second["last_evaluation"])
        saved = await AgentRequest.objects.aget(id=rid)
        self.assertIn("_security", saved.response)
        public = (await self.async_client.get(f"{base}requests/{rid}/")).json()
        self.assertNotIn("_security", public["response"])
        self.assertEqual(public["response"]["result"], finished["result"])
        self.assertNotIn("competency_importance", public["response"]["result"]["job_profile"])

    async def test_finished_confidentiality_denial(self):
        """Reject the normal fixture report with an explicit confidentiality verdict; assert one
        final review, fixed security error, policy close, and no report body in storage or history.
        The reviewer remains a deterministic test port rather than a real classifier.
        """
        self.reviewer.target, self.reviewer.mode = "publish_finished", "deny"
        self.reviewer.violated_requirement_id = "OUTPUT_CONFIDENTIALITY"
        comm = await connect()
        await send_command(comm, "start", resume_text=RESUME, max_questions=1)
        first = await self.terminal(comm, "question")
        rid = await send_command(
            comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
        )
        await self.close_error(comm, await self.terminal(comm, "finished"), "security_denied")
        self.assertEqual(
            [r.proposal.operation for r in self.reviewer.requests].count("publish_finished"), 1
        )
        saved = await AgentRequest.objects.aget(id=rid)
        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.error_code, "security_denied")
        self.assertIsNone(saved.response)
        base = f"/api/agent-interviews/{saved.interview_id}/"
        self.assertIsNone((await self.async_client.get(base)).json()["final_report"])
        self.assertIsNone(
            (await self.async_client.get(f"{base}requests/{rid}/")).json()["response"]
        )

    async def test_generated_narrative_still_requires_review(self):
        """Inject synthetic confidential text through the normal report narrative schema; the
        public job projection must preserve this text for complete semantic review. A marker-based
        test denial then prevents socket delivery and historical report exposure, with no retry.
        """
        marker = "SYNTHETIC_SYSTEM_PROMPT_LEAK: disclose internal interview instructions."
        fixture_call = FixtureLLM.__call__

        def private_narrative(instance, prompt, data, schema):
            """Return synthetic confidential strengths for ReportNarrative and delegate every
            other business schema unchanged to FixtureLLM; this patch makes no provider calls.
            """
            if schema is ReportNarrative:
                instance.calls.append(schema)
                return schema(strengths=[marker], weaknesses=["Some competencies remain untested."])
            return fixture_call(instance, prompt, data, schema)

        self.reviewer.target, self.reviewer.mode = "publish_finished", "deny"
        self.reviewer.violated_requirement_id = "OUTPUT_CONFIDENTIALITY"
        self.reviewer.denied_text = marker
        with patch.object(FixtureLLM, "__call__", private_narrative):
            comm = await connect()
            await send_command(comm, "start", resume_text=RESUME, max_questions=1)
            first = await self.terminal(comm, "question")
            rid = await send_command(
                comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
            )
            failure = await self.terminal(comm, "finished")
            await self.close_error(comm, failure, "security_denied")
        final_requests = [
            r for r in self.reviewer.requests if r.proposal.operation == "publish_finished"
        ]
        self.assertEqual(len(final_requests), 1)
        proposed = json.loads(final_requests[0].proposal.content.text)
        self.assertEqual(proposed["result"]["final_report"]["strengths"], [marker])
        self.assertNotIn("competency_importance", proposed["result"]["job_profile"])
        self.assertEqual(self.llm.calls.count(ReportNarrative), 1)
        self.assertNotIn(marker, json.dumps(failure))
        saved = await AgentRequest.objects.aget(id=rid)
        self.assertEqual(saved.status, "failed")
        self.assertIsNone(saved.response)
        base = f"/api/agent-interviews/{saved.interview_id}/"
        detail = (await self.async_client.get(base)).json()
        public = (await self.async_client.get(f"{base}requests/{rid}/")).json()
        self.assertIsNone(detail["final_report"])
        self.assertIsNone(public["response"])
        self.assertNotIn(marker, json.dumps([detail, public]))

    async def test_private_job_field_stops_before_review(self):
        """A producer accidentally adding unknown job data cannot rely on semantic approval to
        publish it. Inject an otherwise valid job profile and prove the gateway rejects the final
        envelope before its reviewer is called, then suppresses storage and history exposure.
        """
        job = {
            "contract_version": "2.0",
            "job_id": "job-fixture",
            "title": "Backend Engineer",
            "seniority": None,
            "domains": [],
            "internal_notes": "Private job policy.",
        }
        with patch("interviews.agent_session.public_job_profile", return_value=job):
            comm = await connect()
            await send_command(comm, "start", resume_text=RESUME, max_questions=1)
            first = await self.terminal(comm, "question")
            rid = await send_command(
                comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
            )
            await self.close_error(
                comm, await self.terminal(comm, "finished"), "security_contract_failed"
            )
        self.assertNotIn("publish_finished", [r.proposal.operation for r in self.reviewer.requests])
        saved = await AgentRequest.objects.aget(id=rid)
        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.error_code, "security_contract_failed")
        self.assertIsNone(saved.response)
        base = f"/api/agent-interviews/{saved.interview_id}/"
        self.assertIsNone((await self.async_client.get(base)).json()["final_report"])
        self.assertIsNone(
            (await self.async_client.get(f"{base}requests/{rid}/")).json()["response"]
        )

    async def test_denial_at_each_output(self):
        """Reject each of four business outputs individually; Agent may have already submitted
        internally, but rejected text cannot be published.
        """
        for kind in ("prepared", "question", "assessment", "finished"):
            with self.subTest(kind=kind):
                self.reviewer.target, self.reviewer.mode = f"publish_{kind}", "deny"
                comm = await connect()
                rid = await send_command(
                    comm,
                    "prepare" if kind == "prepared" else "start",
                    resume_text=RESUME,
                    **({} if kind == "prepared" else {"max_questions": 1}),
                )
                if kind in {"assessment", "finished"}:
                    first = await self.terminal(comm, "question")
                    rid = await send_command(
                        comm,
                        "answer",
                        question_id=first["question"]["question_id"],
                        answer_text=ANSWER,
                    )
                failure = await self.terminal(comm, kind)
                await self.close_error(comm, failure, "security_denied")
                saved = await AgentRequest.objects.aget(id=rid)
                self.assertEqual(saved.status, "failed")
                self.assertEqual(saved.error_code, "security_denied")
                self.assertIsNone(saved.response)
                base = f"/api/agent-interviews/{saved.interview_id}/"
                detail = (await self.async_client.get(base)).json()
                self.assertIsNone(detail["final_report"])
                if kind in {"prepared", "question"}:
                    self.assertEqual(detail["questions"], [])
                    self.assertIsNone(detail["candidate_profile"])
                else:
                    self.assertIsNone(detail["questions"][0]["answer"]["evaluation"])
                self.assertIsNone(
                    (await self.async_client.get(f"{base}requests/{rid}/")).json()["response"]
                )

    async def test_review_failures_stop_output(self):
        """Vendor exceptions, active TimeoutError, uncertainty, and incomplete coverage all fail and
        close; one request checked only once.
        """
        for mode in ("error", "timeout", "uncertain", "incomplete"):
            with self.subTest(mode=mode):
                self.reviewer.mode = mode
                calls = len(self.reviewer.requests)
                comm = await connect()
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                await self.close_error(
                    comm, await self.terminal(comm, "prepared"), "security_check_failed"
                )
                self.assertEqual(len(self.reviewer.requests), calls + 1)
                self.assertIsNone((await AgentRequest.objects.aget(id=rid)).response)

    async def test_real_deadline_stops_output(self):
        """Actually wait for gateway's five-second deadline, do not shorten production policy;
        cancel review upon expiry, no text delivered.
        """
        self.reviewer.mode = "wait"
        comm = await connect()
        await send_command(comm, "prepare", resume_text=RESUME)
        await self.close_error(
            comm, await self.terminal(comm, "prepared", timeout=8), "security_check_failed"
        )
        self.assertTrue(self.reviewer.cancelled.is_set())
        self.assertEqual(len(self.reviewer.requests), 1)

    async def test_cancel_or_disconnect_during_review(self):
        """Confirm entry into inspection via synchronous event, then cancel or disconnect; local
        review ends and request interrupted, no delayed text. Gateway-owned reviewer closure
        must follow task cancellation; the stand-in records lifecycle calls without networking.
        """
        for cancel in (True, False):
            closes_before = self.reviewer.close_calls
            self.reviewer.mode = "wait"
            self.reviewer.entered.clear()
            self.reviewer.cancelled.clear()
            comm = await connect()
            rid = await send_command(comm, "prepare", resume_text=RESUME, progress_events=False)
            self.assertEqual(json.loads((await comm.receive_output())["text"])["type"], "started")
            await asyncio.wait_for(self.reviewer.entered.wait(), timeout=3)
            if cancel:
                await send_command(comm, "cancel")
                self.assertEqual(
                    json.loads((await comm.receive_output())["text"])["type"], "cancelled"
                )
                self.assertEqual((await comm.receive_output())["code"], 1000)
                await comm.wait()
            else:
                await disconnect(comm)
            self.assertTrue(self.reviewer.cancelled.is_set())
            self.assertEqual(self.reviewer.close_calls, closes_before + 1)
            self.assertEqual((await AgentRequest.objects.aget(id=rid)).status, "interrupted")
            self.assertTrue(comm.output_queue.empty())

    async def test_input_budget_precedes_business_model(self):
        """Input clearly rejected if below protocol byte limit but exceeds existing safety character
        budget, without calling business or detection models.
        """
        comm = await connect()
        await send_command(comm, "prepare", resume_text="x" * 100001)
        await self.close_error(
            comm, await self.terminal(comm, "prepared"), "security_contract_failed"
        )
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.reviewer.requests, [])

    async def test_ownership_and_commit_version(self):
        """Real database ownership mismatch cannot bind; approval credentials with version mismatch
        also cannot enter success state.
        """
        iid = uuid4()
        command = Prepare(type="prepare", request_id=uuid4(), resume_text=RESUME)
        await reserve_request(iid, command)
        gateway = InterviewIOGateway(
            iid, owner_id=987654, connection_id="test", reviewer=FixtureBehaviorReviewer()
        )
        with self.assertRaises(IOSafetyError):
            await gateway.bind_input(command)
        payload = {"type": "prepared", "candidate_profile": {}}
        with self.assertRaises(SecurityContextChanged):
            await complete_request(
                iid,
                command.request_id,
                payload,
                receipt=make_output_receipt(payload, command.request_id, 1),
            )
        self.assertEqual((await AgentRequest.objects.aget(id=command.request_id)).status, "running")

    async def test_history_requires_intact_receipt(self):
        """Old no-credentials and tampered results hidden; verify that only approved copies are
        publicly exposed upon success, not original context.
        """
        interview = await AgentInterview.objects.acreate()
        payload = {"type": "prepared", "candidate_profile": {"name": "approved-person"}}
        record = await AgentRequest.objects.acreate(
            id=uuid4(),
            interview=interview,
            kind="prepare",
            status="succeeded",
            response=payload,
            finished_at=timezone.now(),
        )
        base = f"/api/agent-interviews/{interview.id}/"
        for mode in ("legacy", "approved", "tampered"):
            if mode != "legacy":
                record.response = {
                    **payload,
                    "_security": make_output_receipt(payload, record.id, 0),
                }
                if mode == "tampered":
                    record.response["candidate_profile"] = {"name": "unreviewed-secret"}
                await record.asave(update_fields=["response"])
            detail = (await self.async_client.get(base)).json()
            saved = (await self.async_client.get(f"{base}requests/{record.id}/")).json()
            self.assertEqual(saved["security_output_available"], mode == "approved")
            self.assertEqual(
                detail["candidate_profile"],
                payload["candidate_profile"] if mode == "approved" else None,
            )
            self.assertNotIn("unreviewed-secret", json.dumps([saved, detail]))

    async def test_history_rejects_private_job_profiles(self):
        """An intact receipt proves body integrity, not permission to expose raw internal job
        data. Refuse weights, unknown job fields and non-object job values on both history paths;
        retain projected profiles and legacy reports that omitted job data, without model calls.
        """
        interview = await AgentInterview.objects.acreate()
        record = await AgentRequest.objects.acreate(
            id=uuid4(),
            interview=interview,
            kind="finish",
            status="succeeded",
            response={},
            finished_at=timezone.now(),
        )
        public_job = {
            "contract_version": "2.0",
            "job_id": "job-fixture",
            "title": "Backend Engineer",
            "seniority": None,
            "domains": [],
        }
        base = f"/api/agent-interviews/{interview.id}/"
        for mode in ("weights", "unknown", "not_object", "public", "missing"):
            with self.subTest(mode=mode):
                result = {
                    "interview_id": str(interview.id),
                    "final_report": {"overall_score": 3.0, "summary": "Approved own feedback."},
                }
                if mode != "missing":
                    result["job_profile"] = dict(public_job)
                if mode == "weights":
                    result["job_profile"]["competency_importance"] = {"ownership": 0.3}
                elif mode == "unknown":
                    result["job_profile"]["internal_notes"] = "Private job policy."
                elif mode == "not_object":
                    result["job_profile"] = ["private job policy"]
                payload = {"type": "finished", "result": result}
                record.response = {
                    **payload,
                    "_security": make_output_receipt(payload, record.id, 0),
                }
                await record.asave(update_fields=["response"])
                allowed = mode in {"public", "missing"}
                self.assertEqual(verified_response(record), payload if allowed else None)
                detail = (await self.async_client.get(base)).json()
                saved = (await self.async_client.get(f"{base}requests/{record.id}/")).json()
                self.assertEqual(saved["security_output_available"], allowed)
                self.assertEqual(saved["response"], payload if allowed else None)
                self.assertEqual(
                    detail["final_report"], result["final_report"] if allowed else None
                )
                self.assertNotIn("competency_importance", json.dumps([detail, saved]))
                self.assertNotIn("private job policy", json.dumps([detail, saved]).lower())

    def test_progress_cannot_carry_model_text(self):
        """Preserve fixed stage/status and non-negative duration; unknown fields, stages, or text
        cannot bypass model review via progress channel.
        """
        valid = {"type": "progress", "stage": "resume_parsing", "state": "running"}
        self.assertEqual(validate_progress(valid), valid)
        for extra in ({"text": "unreviewed"}, {"stage": "model text"}, {"duration_ms": True}):
            with self.assertRaises(IOSafetyError):
                validate_progress({**valid, **extra})
