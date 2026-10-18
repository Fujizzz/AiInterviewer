"""Spawn-owned RAG models and database pool behind a nonblocking readiness-aware port."""

import asyncio
import logging
import multiprocessing
from concurrent.futures import Future, InvalidStateError
from threading import Event, Lock, Thread
from uuid import uuid4

from shared.contracts import RetrievalRequest, RetrievalResponse

logger = logging.getLogger(__name__)


def _worker_main(connection, factory=None):
    """Only the child imports the heavy retrieval stack or opens database connections."""
    adapter = None
    try:
        if factory is None:
            from rag.adapter import create_rag_adapter

            factory = create_rag_adapter
        adapter = factory(warmup=True)
        connection.send({"type": "ready"})
        asyncio.run(_serve(connection, adapter))
    except Exception as error:
        try:
            connection.send({"type": "failed", "error": type(error).__name__})
        except (OSError, EOFError):
            pass
    finally:
        if adapter is not None:
            adapter.close()
        connection.close()


async def _serve(connection, adapter):
    tasks, send_lock = set(), asyncio.Lock()

    async def retrieve(message):
        try:
            result = await adapter.retrieve(RetrievalRequest.model_validate(message["request"]))
            reply = {"type": "result", "id": message["id"], "response": result.model_dump()}
        except Exception as error:
            reply = {"type": "error", "id": message["id"], "error": type(error).__name__}
        async with send_lock:
            try:
                await asyncio.to_thread(connection.send, reply)
            except (OSError, EOFError):
                pass  # The application may have closed while a retrieval was finishing.

    try:
        while True:
            message = await asyncio.to_thread(connection.recv)
            if message["type"] == "close":
                break
            task = asyncio.create_task(retrieve(message))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ProcessRAG:
    """One child per application process; clients can span multiple request event loops."""

    def __init__(self, *, factory=None):
        context = multiprocessing.get_context("spawn")
        self._connection, child_connection = context.Pipe()
        self._lock, self._send_lock = Lock(), Lock()
        self._startup_done = Event()
        self._state = "starting"
        self._pending = {}
        self._process = context.Process(
            target=_worker_main, args=(child_connection, factory), name="interview-rag", daemon=True
        )
        try:
            self._process.start()
        except Exception:
            self._connection.close()
            raise
        finally:
            child_connection.close()
        self._listener = Thread(target=self._listen, name="rag-responses", daemon=True)
        self._listener.start()

    @property
    def is_ready(self):
        return self._state == "ready" and self._process.is_alive()

    def wait_ready(self):
        """Explicit preparation jobs may wait; interview startup must never call this."""
        self._startup_done.wait()
        return self.is_ready

    def _listen(self):
        try:
            while True:
                message = self._connection.recv()
                if message["type"] == "ready":
                    with self._lock:
                        if self._state == "starting":
                            self._state = "ready"
                            logger.info("Background RAG worker ready")
                    self._startup_done.set()
                elif message["type"] == "failed":
                    logger.warning("Background RAG unavailable: %s", message["error"])
                    break
                else:
                    with self._lock:
                        future = self._pending.pop(message["id"], None)
                    if future is not None:
                        try:
                            if message["type"] == "result":
                                future.set_result(
                                    RetrievalResponse.model_validate(message["response"])
                                )
                            else:
                                future.set_exception(RuntimeError(message["error"]))
                        except InvalidStateError:
                            pass
        except (OSError, EOFError):
            pass
        finally:
            self._fail_pending()

    def _fail_pending(self):
        with self._lock:
            if self._state != "closed":
                self._state = "failed"
            pending, self._pending = self._pending, {}
        self._startup_done.set()
        for future in pending.values():
            try:
                future.set_exception(RuntimeError("RAG worker unavailable"))
            except InvalidStateError:
                pass

    def _send(self, message):
        with self._send_lock:
            self._connection.send(message)

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        if not self.is_ready:
            raise RuntimeError("RAG worker is not ready")
        key, future = uuid4().hex, Future()
        with self._lock:
            if not self.is_ready:
                raise RuntimeError("RAG worker unavailable")
            self._pending[key] = future
        try:
            await asyncio.to_thread(
                self._send, {"type": "retrieve", "id": key, "request": request.model_dump()}
            )
            return await asyncio.wrap_future(future)
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def close(self):
        """Bound shutdown even during model loading; the child cannot delay server exit."""
        with self._lock:
            if self._state == "closed":
                return
            ready = self.is_ready
            self._state = "closed"
        self._fail_pending()
        if ready and self._send_lock.acquire(blocking=False):
            try:
                self._connection.send({"type": "close"})
            except (OSError, EOFError):
                pass
            finally:
                self._send_lock.release()
            self._process.join(timeout=1)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=1)
        self._connection.close()
        self._listener.join(timeout=1)
