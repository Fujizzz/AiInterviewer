"""Optional shared RAG subprocess; interactive startup never waits for model loading."""

import asyncio
import atexit
import logging
import os
from threading import Lock

from agents.ports import RAGPort
from app.adapters.rag_process import ProcessRAG

logger = logging.getLogger(__name__)
_lock = Lock()
_initialized = False
_adapter = None


def start_shared_rag() -> ProcessRAG | None:
    """Return a lazy port immediately and start at most one background worker."""
    global _initialized, _adapter
    with _lock:
        if _initialized:
            return _adapter
        _initialized = True
        if os.getenv("RAG_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return None
        try:
            _adapter = ProcessRAG()
        except Exception as error:
            logger.warning("Background RAG startup unavailable: %s", type(error).__name__)
        return _adapter


async def get_shared_rag() -> RAGPort | None:
    """Wait explicitly for preparation/smoke jobs, outside the interactive interview path."""
    adapter = start_shared_rag()
    if adapter is not None and await asyncio.to_thread(adapter.wait_ready):
        return adapter
    return None


def _close_shared_rag() -> None:
    global _initialized, _adapter
    with _lock:
        adapter = _adapter
        _adapter, _initialized = None, False
    if adapter is not None:
        adapter.close()


async def close_shared_rag() -> None:
    await asyncio.to_thread(_close_shared_rag)


atexit.register(_close_shared_rag)
