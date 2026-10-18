"""Exercise real spawned IPC with offline adapters, including cold-start and cancellation."""

import asyncio
import multiprocessing
import os
from functools import partial
from time import perf_counter

import pytest

from app.adapters import rag as runtime
from app.adapters.rag_process import ProcessRAG
from shared.contracts import RetrievalRequest, RetrievalResponse, RetrievalSource, RetrievedChunk


class ChildAdapter:
    def __init__(self, closed=None):
        self.closed = closed

    async def retrieve(self, request):
        if request.query == "slow":
            await asyncio.sleep(0.2)
        if request.query == "error":
            raise ValueError("synthetic request failure")
        return RetrievalResponse(
            request_id=request.request_id,
            chunks=[
                RetrievedChunk(
                    chunk_id=request.query,
                    source=request.source,
                    content=request.query,
                    metadata={"pid": os.getpid()},
                )
            ],
        )

    def close(self):
        if self.closed is not None:
            self.closed.set()


def child_factory(*, warmup, gate=None, closed=None, fail=False):
    assert warmup is True
    if gate is not None:
        gate.wait(15)
    if fail:
        raise RuntimeError("synthetic startup failure")
    return ChildAdapter(closed)


def request(query="fast"):
    return RetrievalRequest(
        request_id="reused-request-id",
        interview_id="offline",
        source=RetrievalSource.TECHNICAL,
        intent="test",
        difficulty=2,
        query=query,
    )


@pytest.mark.asyncio
async def test_shared_start_returns_before_warmup_and_reuses_the_same_worker(monkeypatch):
    runtime._close_shared_rag()
    context = multiprocessing.get_context("spawn")
    gate, closed = context.Event(), context.Event()
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(
        runtime,
        "ProcessRAG",
        partial(
            ProcessRAG,
            factory=partial(child_factory, gate=gate, closed=closed),
        ),
    )
    try:
        port = runtime.start_shared_rag()
        assert runtime.start_shared_rag() is port
        assert not port.is_ready
        waiting = asyncio.create_task(runtime.get_shared_rag())
        await asyncio.sleep(0.01)
        assert not waiting.done()
        with pytest.raises(RuntimeError, match="not ready"):
            await port.retrieve(request())
        gate.set()
        assert await asyncio.wait_for(waiting, 12) is port
        assert port.is_ready
        result = await asyncio.wait_for(port.retrieve(request()), 5)
        assert result.chunks[0].metadata["pid"] != os.getpid()
        await runtime.close_shared_rag()
        assert not port._process.is_alive()
        assert closed.is_set()
    finally:
        gate.set()
        runtime._close_shared_rag()


@pytest.mark.asyncio
async def test_cancelled_reply_cannot_corrupt_other_concurrent_requests():
    port = ProcessRAG(factory=child_factory)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(port.wait_ready), 12)
        slow = asyncio.create_task(port.retrieve(request("slow")))
        await asyncio.sleep(0.03)
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        results = await asyncio.wait_for(
            asyncio.gather(
                port.retrieve(request("first")),
                port.retrieve(request("second")),
            ),
            5,
        )
        assert [r.chunks[0].content for r in results] == ["first", "second"]
        with pytest.raises(RuntimeError, match="ValueError"):
            await port.retrieve(request("error"))
        await asyncio.sleep(0.25)
        assert (await port.retrieve(request("after-cancel"))).chunks[0].content == "after-cancel"
        assert port._pending == {}
    finally:
        port.close()


def test_proxy_survives_event_loop_restarts():
    port = ProcessRAG(factory=child_factory)
    try:
        assert port._startup_done.wait(12) and port.is_ready
        first = asyncio.run(port.retrieve(request("first-loop")))
        second = asyncio.run(port.retrieve(request("second-loop")))
        assert first.chunks[0].content == "first-loop"
        assert second.chunks[0].content == "second-loop"
    finally:
        port.close()


def test_shutdown_terminates_worker_even_during_cold_start():
    gate = multiprocessing.get_context("spawn").Event()
    port = ProcessRAG(factory=partial(child_factory, gate=gate))
    started = perf_counter()
    port.close()
    assert perf_counter() - started < 3
    assert not port.is_ready
    assert not port._process.is_alive()
    assert port._startup_done.is_set()
    port.close()


@pytest.mark.asyncio
async def test_startup_failure_and_worker_exit_fail_fast_without_retry():
    port = ProcessRAG(factory=partial(child_factory, fail=True))
    try:
        assert not await asyncio.wait_for(asyncio.to_thread(port.wait_ready), 12)
        with pytest.raises(RuntimeError, match="not ready"):
            await port.retrieve(request())
        assert not port.is_ready
    finally:
        port.close()


@pytest.mark.asyncio
async def test_worker_exit_releases_an_inflight_request():
    port = ProcessRAG(factory=child_factory)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(port.wait_ready), 12)
        pending = asyncio.create_task(port.retrieve(request("slow")))
        await asyncio.sleep(0.03)
        port._process.terminate()
        with pytest.raises(RuntimeError, match="unavailable"):
            await asyncio.wait_for(pending, 2)
        assert not port.is_ready
        assert port._pending == {}
    finally:
        port.close()
