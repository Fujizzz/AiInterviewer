"""Uniform bounded calls for external Agent ports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agents.domain.errors import AgentError

ResultT = TypeVar("ResultT")


async def call_with_timeout(
    awaitable: Awaitable[ResultT],
    *,
    timeout_seconds: float,
    error_factory: Callable[[], AgentError],
) -> ResultT:
    """Await one external call and normalize timeout failures to a typed error."""

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except TimeoutError as error:
        raise error_factory() from error
