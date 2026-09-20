"""Shared call deadlines and sanitized diagnostics, including worker cancellation."""

import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from uuid import uuid4

from agents.tracing import emit_trace


@dataclass
class ModelCall:
    operation: str
    question_id: str | None
    step: int | None
    deadline: float
    call_id: str = field(default_factory=lambda: str(uuid4()))
    abandoned: bool = False


current_model_call: ContextVar[ModelCall | None] = ContextVar("model_call", default=None)


def validation_issues(error, schema):
    """Describe schema locations and codes without output values or exception messages."""
    fields = set()

    def collect(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema.model_json_schema())
    issues = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:8]:
        path = ".".join(str(part) if part in fields else "*" for part in item["loc"]) or "$"
        code = item["type"]
        if not re.fullmatch(r"[a-z_]+", code):
            code = "validation_error"
        issues.append(f"{path}:{code}")
    return issues


def safe_error_details(error):
    """Only allow diagnostic labels/numeric status; never log exception text or input."""
    cause = error.__cause__ or error
    name = type(cause).__name__
    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in {
        "timeout",
        "invalid_json",
        "incomplete_output",
        "refusal",
        "empty_output",
    }:
        if isinstance(cause, TimeoutError) or name == "APITimeoutError":
            code = "timeout"
        elif name in {"ValidationError", "JSONDecodeError"}:
            code = "invalid_json"
        elif name == "APIConnectionError":
            code = "connection"
        elif isinstance(getattr(cause, "status_code", None), int):
            code = "http_error"
        else:
            code = "model_error"
    return {
        "category": code,
        "error_type": type(error).__name__,
        "cause_type": name,
        "status_code": (
            cause.status_code if isinstance(getattr(cause, "status_code", None), int) else None
        ),
        "validation_issues": getattr(error, "validation_issues", []),
    }


async def run_model_call(
    factory, *, operation, question_id=None, step=None, timeout_seconds, turn_deadline=None
):
    """A factory delays worker creation until the execution-local context is installed."""
    started = perf_counter()
    deadline = started + timeout_seconds
    scope = "model_call"
    if turn_deadline is not None and turn_deadline <= deadline:
        deadline, scope = turn_deadline, "react_total"
    call = ModelCall(operation, question_id, step, deadline)
    token = current_model_call.set(call)
    details = dict(
        call_id=call.call_id,
        operation=operation,
        question_id=question_id,
        step=step,
        timeout_seconds=timeout_seconds,
        scope=scope,
    )
    try:
        async with asyncio.timeout(max(0, deadline - perf_counter())):
            result = await factory()
        emit_trace(
            "model.completed", **details, duration_ms=round((perf_counter() - started) * 1000)
        )
        return result
    except asyncio.CancelledError:
        call.abandoned = True
        emit_trace(
            "model.error",
            **{
                **details,
                "scope": "react_total"
                if turn_deadline is not None and perf_counter() >= turn_deadline
                else "cancelled",
            },
            category="timeout"
            if turn_deadline is not None and perf_counter() >= turn_deadline
            else "cancelled",
            error_type="CancelledError",
            cause_type="CancelledError",
            duration_ms=round((perf_counter() - started) * 1000),
        )
        raise
    except Exception as error:
        call.abandoned = True
        emit_trace(
            "model.error",
            **details,
            **safe_error_details(error),
            duration_ms=round((perf_counter() - started) * 1000),
        )
        raise
    finally:
        current_model_call.reset(token)
