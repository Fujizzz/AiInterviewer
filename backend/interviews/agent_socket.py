"""Responsibilities: Serve the text-interview WebSocket lifecycle, including input binding, complete
output review, request execution, and disconnection cleanup.

Implementation: Bind trusted state before business model runs; all model results pass through
security gateway before saving/sending; fixed control events handled independently.
Related Modules: agent_safety reviews output, agent_records saves validation records; does not
integrate business tool interception.
answer_mcp maps same connection MCP finish_current_answer to identical secure/persistent answer
flow.

Declaration Index:
- Command:
  Validates UUID and optional stage event subscription, rejects unknown fields and implicit
  conversion.
- Prepare:
  Pre-parses text or personal resume version, without starting question budget; already started
  connections cannot be re-prepared.
- Prepare.resume_source: Requires exactly one of text or version ID.
- Start:
  Validates interview duration and three-layer question count safety limits, default duration 30
  minutes.
- Start.exclusive_topic_budget:
  Rejects simultaneous specification of old follow-up limit and new topic total question limit.
- Start.resume_source: Requires exactly one of text or version ID.
- Answer:
  Answer must be linked to current question; old or repeated requests cannot trigger model call
  again.
- Cancel:
  Cancels entire interview for current connection, retains history but does not auto-recover.
- parse_command:
  Converts single JSON text into strict Prepare/Start/Answer/Skip/Finish/Discard/Cancel commands; no
  I/O.
- agent_socket:
  Manages ASGI lifecycle for one local-origin text interview, does not access practice database.
- agent_socket.emit:
  Encodes response dict into single JSON and sends to this connection; encoding or transmission
  exceptions propagated.
- agent_socket.reject:
  Sends business error or MCP JSON-RPC error and records associated ID; unknown business requests
  marked null.
- agent_socket.progress:
  Validates fixed stage events; score events must pass behavioral review before sending.
- agent_socket.progress.deliver_assessment: Binds reviewed score to request ID and sends, does not
  save final state prematurely.
- agent_socket.deliver_result: Saves reviewed content and credentials; MCP call wraps same content,
  does not alter security summary.
- agent_socket.run:
  Binds input, schedules Agent, checks complete result, saves and sends; security exceptions bypass
  business fallback path.

- Skip: Validate automatic unanswered closure against the current question.
- Finish: Validate explicit early end with an optional paired current transcript.
- Finish.paired_answer: Reject an incomplete question/text pair before any model operation.
- Discard: Validate deletion of the current connection interview without evaluation.

Variable Index:
- MAX_MESSAGE_BYTES:
  UTF-8 byte limit for single Agent JSON text, including resume or answer and command fields.
- logger:
  Console logging entrypoint for current module; context identifiers and exception handling methods
  detailed in respective functions.

Key State Explanations:
agent_socket internal session/safety belong to current connection; operation is unique business
task, receiver is receiving task.
interview_started distinguishes between prepared profile and started interview; progress_events only
controls event delivery, not strategy.
request_id links current response; seen records accepted executed request IDs. Command.request_id is
UUID.
mcp is MCP handshake status for this connection; rpc_ids distinguish tool calls to wrap final
state/error, tools still mapped to Answer.
transport_failed marks send-side exception, avoiding misreporting disconnection as resumable
business error.
Database primary key enables cross-connection deduplication; scope.user comes from session
authentication, history isolated by creator user.
Start’s question count parameter is safety upper limit, not determining time budget; Answer binds to
current question.
ASGI admission lease extends model reference to actual sync call completion; resource rejection not
passed to Agent to trigger fallback questioning.
"""

import asyncio
import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .access import websocket_allowed
from .agent_records import (
    DuplicateRequest,
    PendingRequest,
    complete_request,
    discard_interview,
    fail_request,
    interrupt_interview,
    reserve_request,
)
from .agent_safety import InterviewIOGateway, security_error_code, validate_progress
from .agent_session import AgentSession
from .answer_mcp import InterviewMCP, tool_result
from .api.resume_versions import resolve_resume_version

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 262144


class Command(BaseModel):
    """Input is UUID and progress_events (default False); latter only subscribes extra events, old
    client response sequence unchanged.
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: UUID
    progress_events: bool = False


class Prepare(Command):
    """Input is non-empty text or personal version ID; backend reads version before parsing, no plan
    created.
    """

    type: Literal["prepare"]
    resume_text: str | None = Field(default=None, min_length=1)
    resume_version_id: UUID | None = None

    @model_validator(mode="after")
    def resume_source(self):
        """Input is validated fields, output is command; requires exactly one of text or version ID,
        trusted command filled by backend after parsing.
        """
        if (self.resume_text is None) == (self.resume_version_id is None):
            raise ValueError("Provide resume_text OR resume_version_id")
        return self


class Start(Command):
    """Input is minutes duration and optional question count safety limit; defaults to 30 minutes
    and Agent-configured upper limit. keep_end_choice_open defaults False; browser opts in to
    retain its owned connection after a final report for a concurrently open end-choice dialog.
    """

    type: Literal["start"]
    resume_text: str | None = Field(default=None, min_length=1)
    resume_version_id: UUID | None = None
    duration_minutes: int = Field(default=30, ge=1)
    max_questions: int | None = Field(default=None, ge=1)
    max_follow_up_per_topic: int | None = Field(default=None, ge=0)
    max_questions_per_project: int | None = Field(default=None, ge=1)
    max_questions_per_topic: int | None = Field(default=None, ge=1)
    job_title: str = Field(default="General AI / Software Engineer", min_length=1)
    keep_end_choice_open: bool = False

    @model_validator(mode="after")
    def resume_source(self):
        """Input is validated fields, output is command; rejects missing or simultaneous
        text/version, preserves old text call behavior.
        """
        if (self.resume_text is None) == (self.resume_version_id is None):
            raise ValueError("Provide resume_text OR resume_version_id")
        return self

    @model_validator(mode="after")
    def exclusive_topic_budget(self):
        """Rejects simultaneous appearance of new and old topic limits; old parameters converted
        only once at configuration entry.
        """
        if self.max_follow_up_per_topic is not None and self.max_questions_per_topic is not None:
            raise ValueError("Use max_questions_per_topic OR max_follow_up_per_topic, not both")
        return self


class Answer(Command):
    """Answer must be linked to current question; old or repeated requests cannot trigger model call
    again.
    """

    type: Literal["answer"]
    question_id: str = Field(min_length=1)
    answer_text: str = Field(min_length=1)


class Cancel(Command):
    """Cancels entire interview for current connection, retains history but does not auto-recover or
    replay model calls.
    """

    type: Literal["cancel"]


class Skip(Command):
    """Functionality: Record automatic closure with no recognized speech.
    Logic: Bind an empty transcript to the current question; no fabricated answer or capability
    score.
    Constraints: Only empty answer_text is accepted, with normal request deduplication.
    """

    type: Literal["skip"]
    question_id: str = Field(min_length=1)
    answer_text: Literal[""] = ""


class Finish(Command):
    """Functionality: Request evaluation/reporting of an early-ended interview.
    Logic: Optional current answer is evaluated once, then the Agent commits FINISH directly.
    Constraints: Nonempty answer requires its current question ID; no client scoring input.
    """

    type: Literal["finish"]
    question_id: str | None = Field(default=None, min_length=1)
    answer_text: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def paired_answer(self):
        """Inputs: Parsed question/text pair. Outputs: Self or validation error.
        Logic: Require both or neither and reject whitespace-only answers before any paid calls.
        Constraints: Uses the existing strict command envelope and server question validation.
        """
        if (
            (self.answer_text is None) != (self.question_id is None)
            or self.answer_text is not None
            and not self.answer_text.strip()
        ):
            raise ValueError(
                "Provide both current question_id and nonempty answer_text, or neither"
            )
        return self


class Discard(Command):
    """Functionality: End and remove the current connection's interview from history.
    Logic: Socket cancels/awaits current work before deleting its owned session.
    Constraints: No evaluation/report model is invoked; resumes are preserved.
    """

    type: Literal["discard"]


def parse_command(raw):
    """Converts JSON into Prepare/Start/Answer/Skip/Finish/Discard/Cancel, without I/O.

    Precondition: Caller has checked message is text and within byte limit.
    Logic: Parse object and select type, then validate strictly via model for UUID, required fields,
    extra fields, and parameter range.
    Return: Corresponding Pydantic command instance; malformed JSON, unknown type, or field error
    propagated via exception.
    Exception object from this function may contain input info; protocol layer must convert to fixed
    message, not serialize exception directly.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    schema = {
        "prepare": Prepare,
        "start": Start,
        "answer": Answer,
        "cancel": Cancel,
        "skip": Skip,
        "finish": Finish,
        "discard": Discard,
    }.get(data.get("type"))
    if schema is None:
        raise ValueError("Unknown message type.")
    return schema.model_validate_json(raw)


async def agent_socket(scope, receive, send):
    """Manages ASGI lifecycle for one local-origin text interview, does not access practice
    database.

    Input: scope provides connection address, origin, and upstream-verified user; receive/send are
    ASGI async event callbacks.
    Logic: Handshake → validate command and input state → Agent → secure check complete result →
    save → send.
    Send started first, then schedule business coroutine, ensuring real stage events do not precede
    request receipt confirmation.
    State invariants: operation at most one; receiver continuously listens; seen only records
    accepted executed request IDs.
    General protocol errors preserve connection; security rejections close 1008, check
    failure/configuration/business/storage errors close 1011.
    Size overflow closes 1009, end/cancel closes 1000; check exceptions not sent back to Agent for
    retry or fallback answer generation.
    If receive and business task complete simultaneously, handle disconnection first; other commands
    judged by latest state after business result published.
    Exit: cancel and wait for local tasks, then request client close; in-flight sync model calls may
    continue.
    Return None; transport or cleanup exceptions propagated to ASGI server; this layer does not
    reconnect, queue, or retransmit billing requests.
    """
    if (await receive())["type"] != "websocket.connect":
        return
    if not websocket_allowed(scope):
        logger.warning("Agent handshake rejected: non-local peer or foreign origin")
        await send({"type": "websocket.close", "code": 1008})
        return
    connection_id = str(uuid4())
    session = None
    safety = None
    owner_id = getattr(scope.get("user"), "pk", None)
    mcp = InterviewMCP(owner_id)
    rpc_ids = set()
    operation = None
    receiver = None
    request_id = None
    seen = set()
    interview_started = False
    keep_end_choice_open = False
    transport_failed = False

    async def emit(data):
        """Encodes response dict into single JSON and sends to this connection; encoding or
        transmission exceptions propagated.
        """
        nonlocal transport_failed
        try:
            await send({"type": "websocket.send", "text": json.dumps(data, ensure_ascii=False)})
        except Exception:
            transport_failed = True
            raise

    async def reject(code, detail, rejected_id=None):
        """Sends business error or MCP JSON-RPC error and records ID; unknown business requests
        marked null.

        code/detail from fixed messages in protocol layer, cannot pass raw exception text containing
        candidate input.
        Sends only error, does not decide whether connection terminates; closing policy executed by
        calling branch.
        """
        logger.warning(
            "Agent rejected connection=%s request=%s code=%s", connection_id, rejected_id, code
        )
        if rejected_id in rpc_ids:
            await emit(
                {
                    "jsonrpc": "2.0",
                    "id": rejected_id,
                    "error": {"code": -32000, "message": detail, "data": {"code": code}},
                }
            )
        else:
            await emit({"type": "error", "request_id": rejected_id, "code": code, "detail": detail})

    async def progress(data):
        """Input is progress/score dictionary; progress allows only fixed metadata, score must be
        approved before sending; returns None.

        Callback located outside report model fallback; security failure directly terminates round;
        cancellation propagated, no buffer, continues sending.
        """

        async def deliver_assessment(payload, receipt):
            """Input is gateway-checked content and credentials; sends only content bound to request
            ID, does not treat score as final successful response.
            """
            await emit({**payload, "request_id": request_id})

        if data.get("type") == "assessment":
            await safety.publish(data, deliver_assessment)
        else:
            await emit({**validate_progress(data), "request_id": request_id})

    async def deliver_result(payload, receipt):
        """Input is approved content and credentials; verifies version, saves, then sends same
        content inside business or MCP wrapper.
        MCP result’s structuredContent/text both come from approved content; transmission failure
        does not retry or undo submission.
        """
        await complete_request(
            session.interview_id, request_id, payload, receipt=receipt, owner_id=owner_id
        )
        message = {**payload, "request_id": request_id}
        await emit(tool_result(request_id, message) if request_id in rpc_ids else message)
        return payload

    async def run(command):
        """Input is reserved command; binds backend input, runs Agent, checks and delivers output;
        returns delivered response.

        Security exceptions occur outside Agent’s automatic repair path; failure only saves limited
        error code, unverified results not exposed.
        Does not wait for model inside database transaction; cancellation marked interrupted in
        final cleanup.
        """
        try:
            command = await safety.bind_input(command)
            handler = {
                "prepare": session.prepare,
                "start": session.start,
                "answer": session.answer,
                "skip": session.answer,
                "finish": session.finish,
            }
            result = await handler[command.type](command)
            return await safety.publish(result, deliver_result)
        except Exception as exc:
            await fail_request(
                session.interview_id, command.request_id, error_code=security_error_code(exc)
            )
            raise

    await send({"type": "websocket.accept"})
    await emit(
        {
            "type": "hello",
            "connection_id": connection_id,
            "max_message_bytes": MAX_MESSAGE_BYTES,
            "seconds_per_question": 120,
            "capabilities": [
                "prepare",
                "progress",
                "assessment",
                "answer_completion_mcp",
                "early_finish",
                "discard",
                "skip",
            ],
        }
    )
    logger.info("Agent connected connection=%s", connection_id)
    try:
        # Receive and business computation each have one task; FIRST_COMPLETED allows response to
        # disconnection during model wait.
        receiver = asyncio.create_task(receive())
        while True:
            pending = {receiver} if operation is None else {receiver, operation}
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            # Disconnection takes priority, avoiding sending report after learning connection is
            # closed in same round.
            if receiver in done:
                event = receiver.result()
                if event["type"] == "websocket.disconnect":
                    return
            discard_requested = False
            if receiver in done:
                raw_control = event.get("text")
                if (
                    isinstance(raw_control, str)
                    and len(raw_control.encode("utf-8")) <= MAX_MESSAGE_BYTES
                ):
                    try:
                        discard_requested = isinstance(parse_command(raw_control), Discard)
                    except (ValueError, TypeError, ValidationError):
                        pass  # The ordinary parser below reports malformed controls.
            # A valid no-save choice takes precedence even if work failed in this same loop
            # iteration. The normal parsing/ownership/deletion path below still applies.
            if operation is not None and operation in done and not discard_requested:
                try:
                    result = operation.result()
                except Exception as exc:
                    if transport_failed:
                        raise
                    error_code = security_error_code(exc)
                    logger.error(
                        "Agent failed connection=%s request=%s exception=%s; "
                        "check model-call logs and backend configuration",
                        connection_id,
                        request_id,
                        type(exc).__name__,
                    )
                    await reject(
                        error_code,
                        "The response did not pass safety review or "
                        "review did not complete. This interview has "
                        "stopped."
                        if error_code.startswith("security_")
                        else (
                            "Interview processing failed. Check the backend "
                            "logs, model configuration, and resume content."
                        ),
                        request_id,
                    )
                    await send(
                        {
                            "type": "websocket.close",
                            "code": 1008 if error_code == "security_denied" else 1011,
                        }
                    )
                    return
                operation = None
                logger.info(
                    "Agent response connection=%s request=%s type=%s",
                    connection_id,
                    request_id,
                    result["type"],
                )
                if result["type"] == "finished" and not keep_end_choice_open:
                    await send({"type": "websocket.close", "code": 1000})
                    return
                if result["type"] == "finished":
                    # The browser normally closes after displaying the approved report. Keep
                    # this owned connection available for an end-choice dialog that was opened
                    # while the final request was running; Discard must still honor that choice.
                    interview_started = True
            if receiver not in done:
                continue
            receiver = asyncio.create_task(receive())
            raw = event.get("text")
            if raw is None:
                await reject("invalid_message", "The Agent accepts JSON text messages only.")
                continue
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await reject("size_limit", "The message exceeds the 256 KiB limit.")
                await send({"type": "websocket.close", "code": 1009})
                return
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "jsonrpc" in data:
                    try:
                        reply, normalized = mcp.handle(data)
                    except (ValueError, TypeError, AttributeError):
                        await emit(
                            {
                                "jsonrpc": "2.0",
                                "id": data.get("id"),
                                "error": {
                                    "code": -32602,
                                    "message": "Invalid MCP request or completion receipt.",
                                },
                            }
                        )
                        continue
                    if reply is not None:
                        await emit(reply)
                    if normalized is None:
                        continue
                    raw = json.dumps(normalized)
                    rpc_ids.add(normalized["request_id"])
                command = parse_command(raw)
            except (ValueError, TypeError, ValidationError):
                await reject(
                    "invalid_message",
                    ("Check the message type, UUID, required fields, and parameter types."),
                )
                continue
            incoming_id = str(command.request_id)
            if isinstance(command, Discard):
                if operation is not None:
                    operation.cancel()
                    outcome = (await asyncio.gather(operation, return_exceptions=True))[0]
                    if isinstance(outcome, Exception):
                        logger.warning(
                            "Agent discarded work failed connection=%s exception=%s",
                            connection_id,
                            type(outcome).__name__,
                        )
                    operation = None
                if session is not None:
                    try:
                        await discard_interview(session.interview_id, owner_id)
                    except Exception as exc:
                        logger.error(
                            "Agent discard failed interview=%s exception=%s; "
                            "inspect database deletion constraints",
                            session.interview_id,
                            type(exc).__name__,
                        )
                        await reject(
                            "storage_unavailable",
                            "Interview deletion failed. Check backend storage logs.",
                            incoming_id,
                        )
                        await send({"type": "websocket.close", "code": 1011})
                        return
                await emit({"type": "discarded", "request_id": incoming_id})
                await send({"type": "websocket.close", "code": 1000})
                return
            # Cancel the entire session without busy-state or deduplication restrictions; every exit
            # path cancels the active local task.
            if isinstance(command, Cancel):
                await emit({"type": "cancelled", "request_id": incoming_id})
                await send({"type": "websocket.close", "code": 1000})
                return
            if incoming_id in seen:
                await reject(
                    "duplicate_request",
                    ("This request_id has already been processed. Do not resend it."),
                    incoming_id,
                )
                continue
            if operation is not None:
                await reject("busy", "The previous request is still being processed.", incoming_id)
                continue
            if isinstance(command, (Prepare, Start)):
                if interview_started:
                    await reject(
                        "already_started",
                        ("Each connection can initialize only one interview."),
                        incoming_id,
                    )
                    continue
                try:
                    if session is None:
                        session = AgentSession()
                        safety = InterviewIOGateway(
                            session.interview_id, owner_id=owner_id, connection_id=connection_id
                        )
                        if hasattr(getattr(session, "llm", None), "capacity_lease"):
                            session.llm.capacity_lease = scope.get("interview.capacity_lease")
                except Exception as exc:
                    logger.error(
                        "Agent setup failed connection=%s exception=%s; "
                        "check repository-root .env provider, model, key and temperature",
                        connection_id,
                        type(exc).__name__,
                    )
                    await reject(
                        "configuration_error",
                        (
                            "Configure the provider, model, and API key in "
                            "the project-root .env file, then restart the "
                            "backend."
                        ),
                        incoming_id,
                    )
                    await send({"type": "websocket.close", "code": 1011})
                    return
                logger.info(
                    "Agent initialized connection=%s interview=%s",
                    connection_id,
                    session.interview_id,
                )
            else:
                if session is None or session.action is None:
                    await reject(
                        "not_started", "Send start first and wait for a question.", incoming_id
                    )
                    continue
                if (not isinstance(command, Finish) or command.question_id is not None) and (
                    session.action.question is None
                    or command.question_id != session.action.question.question_id
                ):
                    await reject(
                        "stale_question",
                        ("Answer the current question returned by the server."),
                        incoming_id,
                    )
                    continue
            try:
                resume_version_id = getattr(command, "resume_version_id", None)
                command = await resolve_resume_version(command, owner_id)
            except ValueError:
                await reject(
                    "resume_unavailable",
                    (
                        "The resume version does not exist, does not "
                        "belong to the current user, or has not finished "
                        "parsing."
                    ),
                    incoming_id,
                )
                continue
            try:
                await reserve_request(
                    session.interview_id,
                    command,
                    owner_id=getattr(scope.get("user"), "pk", None),
                    resume_version_id=resume_version_id,
                )
            except DuplicateRequest:
                await reject(
                    "duplicate_request",
                    ("This request_id has already been accepted. Do not resend it."),
                    incoming_id,
                )
                continue
            except PendingRequest:
                await reject(
                    "busy",
                    ("The interview has an unfinished request, so another request cannot start."),
                    incoming_id,
                )
                continue
            except Exception as exc:
                logger.error(
                    "Agent storage unavailable connection=%s exception=%s; "
                    "check migrations and database",
                    connection_id,
                    type(exc).__name__,
                )
                await reject(
                    "storage_unavailable",
                    ("The request was not executed. Check the database and migrations."),
                    incoming_id,
                )
                await send({"type": "websocket.close", "code": 1011})
                return
            if isinstance(command, Start):
                interview_started = True
                keep_end_choice_open = command.keep_end_choice_open
            work = run(command)
            seen.add(incoming_id)
            request_id = incoming_id
            session.emit_event = progress if command.progress_events else None
            try:
                await emit({"type": "started", "request_id": request_id, "operation": command.type})
            except (Exception, asyncio.CancelledError):
                # Coroutines that have not been scheduled cannot enter finally; close them
                # explicitly to avoid leaks when sending fails.
                work.close()
                raise
            operation = asyncio.create_task(work)
    finally:
        # Wait for asynchronous cancellation to take effect before closing model entry, avoiding
        # subsequent steps from using a client that is about to be released.
        tasks = [task for task in (operation, receiver) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if session is not None:
            try:
                await interrupt_interview(session.interview_id)
            except Exception as exc:
                logger.error(
                    "Agent cleanup storage failed interview=%s exception=%s; "
                    "pending requests require review",
                    session.interview_id,
                    type(exc).__name__,
                )
                raise
            finally:
                session.close()
        logger.info("Agent disconnected connection=%s", connection_id)
