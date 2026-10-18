"""Responsibilities: Exercise the real ASGI service with an explicitly injected offline model.
Implementation: Verify text interviews, persistence, progress ordering, and existing HTTP/WebSocket
contracts.
Related Modules: run_e2e starts the isolated server; agent_fixture_app supplies the test-only model
fixture.
Declaration Index:
- check_agent: Complete a two-question WebSocket interview and verify its persisted report and
  duplicate-request rejection.
- check_progress: Verify prepared-resume reuse, progress stages, and assessment ordering over a real
  socket.
- check_all: Run both Agent regression scenarios against the same isolated service.
Variable Index:
None
"""

import json
import math
from uuid import uuid4

from run_e2e import main, request
from websockets.sync.client import connect


def check_agent(base):
    """Functionality: Verify Agent interview persistence and durable request deduplication.
    Inputs: Base URL for the ASGI app explicitly configured with FixtureLLM.
    Outputs: None; assertions cover socket events, persisted answers/report, and duplicate
    rejection.
    Logic: Complete two questions over WebSocket, inspect records through HTTP, then reconnect with
    a used request UUID.
    Constraints: The injected model is synthetic and cannot validate real model scoring; the caller
    owns server and database cleanup.
    """
    with connect(base.replace("http://", "ws://") + "/ws/agent/", origin=base, proxy=None) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"
        request_id = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": request_id,
                    "max_questions": 2,
                    "resume_text": "Alex built a Python log pipeline and tested malformed records.",
                }
            )
        )
        answered = 0
        while True:
            started = json.loads(ws.recv(timeout=5))
            assert started["type"] == "started" and started["request_id"] == request_id
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == request_id
            if message["type"] == "finished":
                assert message["result"]["interview_finished"]
                assert len(message["result"]["question_history"]) == answered == 2
                assert message["result"]["interview_state"]["elapsed_seconds"] < 120
                assert math.isclose(message["result"]["final_report"]["overall_score"], 3.0)
                break
            assert message["type"] == "question"
            answered += 1
            request_id = str(uuid4())
            ws.send(
                json.dumps(
                    {
                        "type": "answer",
                        "request_id": request_id,
                        "question_id": message["question"]["question_id"],
                        "answer_text": (
                            "I implemented a bounded-memory parser and tested malformed records."
                        ),
                    }
                )
            )
    interview_id = message["result"]["interview_id"]
    detail = request(base, f"/api/agent-interviews/{interview_id}/")
    assert detail["status"] == "completed" and detail["can_resume"] is False
    assert len(detail["questions"]) == 2
    assert all(item["answer"]["committed_state_version"] for item in detail["questions"])
    assert detail["final_report"] == message["result"]["final_report"]
    saved = request(base, f"/api/agent-interviews/{interview_id}/requests/{request_id}/")
    assert saved["status"] == "succeeded"
    assert saved["response"]["result"] == message["result"]
    with connect(base.replace("http://", "ws://") + "/ws/agent/", origin=base, proxy=None) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": request_id,
                    "resume_text": "Duplicate request must not call the model.",
                }
            )
        )
        assert json.loads(ws.recv(timeout=5))["code"] == "duplicate_request"
    print(
        "PASS Agent persistence over real HTTP/WebSocket: report, answers and durable deduplication"
    )


def check_progress(base):
    """Functionality: Verify preparation reuse and progress ordering during a one-question
    interview.
    Inputs: Base URL for the explicitly configured offline ASGI service.
    Outputs: None; assertions check request IDs, progress stages, score, and final narrative state.
    Logic: Prepare a resume, start a session with the same text, answer once, and observe assessment
    before report generation.
    Constraints: Each command uses a unique UUID; socket timeouts are test failure bounds and do not
    change production settings.
    """
    resume = "Alex built a Python log pipeline and tested malformed records."
    with connect(base.replace("http://", "ws://") + "/ws/agent/", origin=base, proxy=None) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert "prepare" in hello["capabilities"]
        rid = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "prepare",
                    "request_id": rid,
                    "resume_text": resume,
                    "progress_events": True,
                }
            )
        )
        preparing = []
        while True:
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == rid
            assert message["type"] in {"started", "progress", "prepared"}
            preparing.append(message)
            if message["type"] == "prepared":
                break
        assert [m["stage"] for m in preparing if m["type"] == "progress"] == [
            "resume_parsing",
            "resume_parsing",
        ]
        rid = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": rid,
                    "resume_text": resume,
                    "max_questions": 1,
                    "progress_events": True,
                }
            )
        )
        early_score = None
        answered = False
        while True:
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == rid
            if message["type"] == "progress":
                assert message["stage"] != "resume_parsing"
                if message["stage"] == "report_generation":
                    assert early_score is not None
            elif message["type"] == "question":
                assert not answered
                answered = True
                rid = str(uuid4())
                ws.send(
                    json.dumps(
                        {
                            "type": "answer",
                            "request_id": rid,
                            "question_id": message["question"]["question_id"],
                            "answer_text": "I implemented and tested the parser.",
                            "progress_events": True,
                        }
                    )
                )
            elif message["type"] == "assessment":
                early_score = message["assessment"]["overall_score"]
                assert math.isclose(early_score, 3.0)
            elif message["type"] == "finished":
                assert answered and early_score is not None
                assert message["result"]["final_report"]["overall_score"] == early_score
                assert message["result"]["report_narrative_status"] == "completed"
                assert message["result"]["interview_state"]["elapsed_seconds"] < 120
                break
            else:
                assert message["type"] == "started", message
    print("PASS real WebSocket preparation reuse, progress and assessment before narrative")


def check_all(base):
    """Functionality: Run the Agent persistence and progress scenarios against one isolated server.
    Inputs: The server base URL supplied by run_e2e.
    Outputs: None; propagates any assertion or request error.
    Logic: Invoke both scenario checks sequentially.
    Constraints: Existing regression assertions remain active.
    """
    check_agent(base)
    check_progress(base)


if __name__ == "__main__":
    main("interviews.tests.agent_fixture_app:application", check_all)
