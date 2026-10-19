"""Durable, bounded stage recovery; completed model responses are never requested twice."""

import asyncio
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from evaluation.model_calls import EvaluationStageError

stage_recovery = ContextVar("evaluation_recovery", default=None)
managed_evaluation_call = ContextVar("managed_evaluation_call", default=False)


@contextmanager
def recovery_scope(repository, job):
    token = stage_recovery.set((repository, job))
    try:
        yield
    finally:
        stage_recovery.reset(token)


def call_key(stage, prompt, payload, schema):
    value = json.dumps(
        [stage, prompt, payload, schema.model_json_schema()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def reserve_stage(journal, key, stage, *, recovery_used, recovery_limit=3):
    """Mutate a transaction-local journal before dispatch, including uncertain crash attempts."""
    entry = journal.setdefault(stage, {"key": key, "attempts": 0})
    if entry["key"] != key:
        raise EvaluationStageError(stage, "checkpoint_mismatch")
    if "result" in entry:
        return entry, recovery_used
    attempts = entry["attempts"]
    if attempts >= 2 or (attempts and recovery_used >= recovery_limit):
        raise EvaluationStageError(stage, "retry_budget_exhausted")
    entry["attempts"] += 1
    return entry, recovery_used + int(attempts > 0)


def is_transient(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, TimeoutError) or type(error).__name__ in {
            "APITimeoutError",
            "APIConnectionError",
        }:
            return True
        if getattr(error, "code", None) in {"timeout", "invalid_json", "incomplete_output"}:
            return True
        if getattr(error, "status_code", None) in {408, 429, 500, 502, 503, 504}:
            from agents.model_calls import unavailable_provider_reason

            return unavailable_provider_reason(error) is None
        error = error.__cause__ or error.__context__
    return False


async def recovered_call(call, *, stage, prompt, payload, schema):
    scope = stage_recovery.get()
    if scope is None:
        return await call()
    repository, job = scope
    key = call_key(stage, prompt, payload, schema)
    while True:
        entry = await repository.reserve_evaluation_stage(job, key, stage)
        if "result" in entry:
            return schema.model_validate(entry["result"])
        if entry["attempts"] > 1:
            await asyncio.sleep(1)
        token = managed_evaluation_call.set(True)
        try:
            result = await call()
            await repository.complete_evaluation_stage(
                job, key, stage, result.model_dump(mode="json")
            )
            return result
        except Exception as error:
            if not is_transient(error):
                raise
            # The next reservation enforces both persistent stage and interview budgets.
        finally:
            managed_evaluation_call.reset(token)
