"""Responsibilities: Verify explicit interview safety suspension, restoration and result integrity.
Implementation: Exercise real ASGI, transactions and history with deterministic business output;
fail immediately if disabled mode creates a safety reviewer. No external model is used.
Related Modules: agent_safety, agent_records, config.settings and agent_history control activation.
Declaration Index:
- SecurityActivationTests: Isolated database tests for explicit disabled-mode I/O contracts.
- SecurityActivationTests.setUp: Install a business fixture and forbid safety reviewer construction.
- SecurityActivationTests.test_disabled_round_trip_and_history: Complete all four outputs with no
  engine, verify actual disabled receipts, intact history and reactivation-time historical reads.
- SecurityActivationTests.test_disabled_input_omits_scan_budget: Process protocol-valid input above
  the safety scan budget without constructing a reviewer.
- SecurityActivationTests.test_disabled_ownership_and_integrity: Retain ownership, pending-state,
  commit-version and body-digest checks while semantic review is suspended.
- SecurityActivationTests.test_reactivation_rejects_disabled_receipt: Refuse unreviewed new commits
  after reactivation rather than relabeling them allow.
- SecurityActivationTests.test_reactivation_restores_denial: Verify a newly enabled real gateway
  invokes its injected reviewer and stops denied output.
- SecurityActivationTests.test_mode_is_fixed_per_connection: Capture explicit configuration on
  construction, ignoring injected reviewers while disabled.
- SecurityActivationTests.test_configuration_is_strict: Validate absent/true/false and reject
  malformed activation values in isolated settings imports.
Variable Index:
None
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase, override_settings

from ai_security.errors import SecurityContextChanged
from interviews.agent_models import AgentRequest
from interviews.agent_records import complete_request, reserve_request
from interviews.agent_safety import (
    InterviewIOGateway,
    IOSafetyError,
    make_output_receipt,
    verified_response,
)
from interviews.agent_socket import Prepare

from .agent_fixtures import ANSWER, RESUME, FixtureBehaviorReviewer, FixtureLLM
from .test_agent_progress import collect_until, connect, disconnect, read, send_command
from .test_agent_safety import ScriptedReviewer


@override_settings(AI_SECURITY_ENABLED=False)
class SecurityActivationTests(TransactionTestCase):
    """Functionality: Verify development suspension without external model calls.
    Logic: Keep real gateway, protocol, database and history; replace only business models.
    Constraints: These tests prove activation wiring, not semantic detection or provider behavior.
    """

    def setUp(self):
        """Inputs: Test lifecycle. Outputs: Registered patch cleanup and synthetic business model.
        Logic: Forbid safety factory calls; supply deterministic business outputs. Constraints:
        No production data or credentials are used; inherited database isolation remains active.
        """
        super().setUp()
        self.llm = FixtureLLM()
        for name, options in (
            ("interviews.agent_session.BackendLLM", {"return_value": self.llm}),
            (
                "interviews.agent_safety.create_behavior_reviewer",
                {"side_effect": AssertionError("disabled mode created a safety reviewer")},
            ),
        ):
            patcher = patch(name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_disabled_round_trip_and_history(self):
        """Inputs: Synthetic resume/answer. Outputs: Verified prepared/question/assessment/finished
        events, stored disabled receipts and readable history. Logic: Run one complete interview
        with engine construction forbidden, then reactivate only for historical reads. Constraints:
        No assertions infer semantic safety from unchecked outputs; no raw context is exposed.
        """
        with patch("interviews.agent_safety.BehaviorEngine") as engine:
            comm = await connect()
            rid = await send_command(comm, "prepare", resume_text=RESUME)
            self.assertEqual((await collect_until(comm, "prepared", rid))[-1]["type"], "prepared")
            rid = await send_command(comm, "start", resume_text=RESUME, max_questions=1)
            first = (await collect_until(comm, "question", rid))[-1]
            rid = await send_command(
                comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
            )
            events = await collect_until(comm, "finished", rid)
            self.assertIn("assessment", [event["type"] for event in events])
            self.assertEqual((await comm.receive_output())["code"], 1000)
            await comm.wait()
            engine.assert_not_called()
        rows = [row async for row in AgentRequest.objects.filter(status="succeeded")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row.response["_security"]["status"] == "disabled" for row in rows))
        base = f"/api/agent-interviews/{first['interview_id']}/"
        with override_settings(AI_SECURITY_ENABLED=True):
            detail = (await self.async_client.get(base)).json()
            self.assertEqual(detail["final_report"], events[-1]["result"]["final_report"])
            saved = (await self.async_client.get(f"{base}requests/{rid}/")).json()
            self.assertEqual(saved["security_review_status"], "disabled")
            self.assertNotIn("_security", saved["response"])

    async def test_disabled_input_omits_scan_budget(self):
        """Process 100,001 characters under the unchanged protocol frame limit. Verify business
        execution and disabled persistence, proving the safety scan budget is explicitly suspended.
        """
        comm = await connect()
        rid = await send_command(comm, "prepare", resume_text="x" * 100001)
        await collect_until(comm, "prepared", rid)
        await disconnect(comm)
        self.assertTrue(self.llm.calls)
        self.assertEqual(
            (await AgentRequest.objects.aget(id=rid)).response["_security"]["status"], "disabled"
        )

    async def test_disabled_ownership_and_integrity(self):
        """Use an owned pending request and synthetic body; reject wrong ownership, expired state
        and tampered digest. Constraints: No semantic reviewer or mock database participates.
        """
        iid, rid = uuid4(), uuid4()
        command = Prepare(type="prepare", request_id=rid, resume_text=RESUME)
        await reserve_request(iid, command)
        gateway = InterviewIOGateway(iid, owner_id=987654, connection_id="activation-test")
        with self.assertRaises(IOSafetyError):
            await gateway.bind_input(command)
        payload = {"type": "prepared", "candidate_profile": {"name": "synthetic-person"}}
        receipt = make_output_receipt(payload, rid, 1, review_status="disabled")
        with self.assertRaises(SecurityContextChanged):
            await complete_request(iid, rid, payload, receipt=receipt)
        receipt = make_output_receipt(payload, rid, 0, review_status="disabled")
        with self.assertRaises(ValueError):
            await complete_request(iid, rid, {**payload, "extra": "tampered"}, receipt=receipt)
        await complete_request(iid, rid, payload, receipt=receipt)
        row = await AgentRequest.objects.aget(id=rid)
        self.assertEqual(verified_response(row), payload)
        row.response["candidate_profile"]["name"] = "altered-person"
        self.assertIsNone(verified_response(row))
        row.response["_security"]["status"] = "unknown"
        self.assertIsNone(verified_response(row))
        await gateway.aclose()

    async def test_reactivation_rejects_disabled_receipt(self):
        """Inputs: Valid unreviewed receipt for a pending request. Logic: Enable review before
        completion; verify rejection preserves running state. Constraints: No retroactive allow.
        """
        iid, rid = uuid4(), uuid4()
        await reserve_request(iid, Prepare(type="prepare", request_id=rid, resume_text=RESUME))
        payload = {"type": "prepared", "candidate_profile": {}}
        with override_settings(AI_SECURITY_ENABLED=True), self.assertRaises(ValueError):
            await complete_request(
                iid,
                rid,
                payload,
                receipt=make_output_receipt(payload, rid, 0, review_status="disabled"),
            )
        self.assertEqual((await AgentRequest.objects.aget(id=rid)).status, "running")

    async def test_reactivation_restores_denial(self):
        """Use a newly enabled ASGI connection and injected denying reviewer. Verify one review,
        denial, closed connection and no stored output. Constraints: Semantic judgment is mocked;
        enabled-mode execution and failure semantics are real.
        """
        reviewer = ScriptedReviewer(mode="deny")
        with (
            override_settings(AI_SECURITY_ENABLED=True),
            patch("interviews.agent_safety.create_behavior_reviewer", return_value=reviewer),
        ):
            comm = await connect()
            rid = await send_command(comm, "prepare", resume_text=RESUME, progress_events=False)
            self.assertEqual((await read(comm))["type"], "started")
            self.assertEqual((await read(comm))["code"], "security_denied")
            self.assertEqual((await comm.receive_output())["code"], 1008)
            await comm.wait()
            self.assertEqual(len(reviewer.requests), 1)
            self.assertIsNone((await AgentRequest.objects.aget(id=rid)).response)

    async def test_mode_is_fixed_per_connection(self):
        """Inputs: Explicit settings changes and injected reviewer. Logic: Disabled construction
        ignores the port and opens no resources; enabled construction preserves its mode even if
        settings later change. Constraints: Real deployment changes require a process restart.
        """
        reviewer = FixtureBehaviorReviewer()
        disabled = InterviewIOGateway(
            uuid4(), owner_id=None, connection_id="disabled", reviewer=reviewer
        )
        self.assertIsNone(disabled.engine)
        self.assertIsNone(disabled._owned_reviewer)
        with override_settings(AI_SECURITY_ENABLED=True):
            enabled = InterviewIOGateway(
                uuid4(), owner_id=None, connection_id="enabled", reviewer=reviewer
            )
            self.assertFalse(disabled.enabled)
            self.assertIsNotNone(enabled.engine)
        self.assertTrue(enabled.enabled)
        with override_settings(AI_SECURITY_ENABLED="false"), self.assertRaises(ValueError):
            InterviewIOGateway(uuid4(), owner_id=None, connection_id="invalid")
        await disabled.aclose()
        await enabled.aclose()

    def test_configuration_is_strict(self):
        """Import actual settings in subprocesses with dotenv loading suppressed and synthetic
        Django secret. Verify true default and accepted literals; malformed values fail before
        startup. Constraints: Do not read/print local secrets or mutate caller environment.
        """
        root = Path(__file__).resolve().parents[3]
        program = (
            "import runpy; from unittest.mock import patch; "
            "p=patch('dotenv.load_dotenv'); p.start(); "
            "print(runpy.run_path('backend/config/settings.py')['AI_SECURITY_ENABLED'])"
        )
        for value, expected in (
            (None, "True"),
            ("true", "True"),
            ("false", "False"),
            ("FALSE", "False"),
            ("", None),
            ("0", None),
            ("typo", None),
        ):
            with self.subTest(value=value):
                env = dict(os.environ, DJANGO_SECRET_KEY="activation-test-only-key")
                env.pop("AI_SECURITY_ENABLED", None)
                if value is not None:
                    env["AI_SECURITY_ENABLED"] = value
                result = subprocess.run(
                    [sys.executable, "-c", program],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, expected is not None)
                if expected is not None:
                    self.assertEqual(result.stdout.strip(), expected)
