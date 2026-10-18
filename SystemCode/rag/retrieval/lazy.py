"""Thread-safe lazy singletons for heavy objects (embedding / rerank models).

Pure standard library on purpose: the adapter and its tests import this module without the
rag extra installed. The factories passed in are what pull in torch.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class LazySingleton(Generic[T]):
    """Build the value at most once, even when many threads ask at the same time.

    Double-checked locking: the fast path is a plain attribute read. If the factory raises, nothing
    is cached, so the next caller retries (a transient download/OOM failure is not permanent).
    """

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._value: T | None = None
        self._ready = False

    @property
    def loaded(self) -> bool:
        return self._ready

    def get(self) -> T:
        if self._ready:
            return self._value  # type: ignore[return-value]
        with self._lock:
            if not self._ready:
                self._value = self._factory()
                self._ready = True
        return self._value  # type: ignore[return-value]


class SerializedEmbedder:
    """Lazy, process-wide bge-m3 embedder whose `encode` calls never overlap.

    sentence-transformers inference is not guaranteed re-entrant on every device (MPS/CUDA
    streams), and a query embed is a few tens of milliseconds, so concurrent callers queue on a
    lock instead of sharing the model unsafely. `meta` loads the model on first access.
    """

    def __init__(self, loader: LazySingleton[Any]) -> None:
        self._loader = loader
        self._encode_lock = threading.Lock()

    @property
    def meta(self) -> dict:
        return self._loader.get().meta

    def encode(self, texts: list[str], batch_size: int | None = None) -> Any:
        model = self._loader.get()
        with self._encode_lock:
            return model.encode(texts, batch_size=batch_size)


class SerializedReranker:
    """Same idea for the optional cross-encoder."""

    def __init__(self, loader: LazySingleton[Any]) -> None:
        self._loader = loader
        self._lock = threading.Lock()

    def score(self, query: str, texts: list[str]) -> list[float]:
        model = self._loader.get()
        with self._lock:
            return model.score(query, texts)
