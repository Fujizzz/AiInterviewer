"""Optional, execution-local diagnostic sink; no persistence dependency in the core."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

trace_sink: ContextVar[Callable[[str, dict[str, Any]], None] | None] = ContextVar(
    "interview_trace_sink", default=None
)


def emit_trace(event: str, **data: Any) -> None:
    sink = trace_sink.get()
    if sink is not None:
        sink(event, data)
