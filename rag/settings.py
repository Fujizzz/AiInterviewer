"""Environment-driven settings of the RAGPort adapter.

Every variable is optional; unset means the default shown. The database location follows
rag.index.config.get_dsn (RAG_DATABASE_URL, else DATABASE_URL, else POSTGRES_*, with rag/.env as
a local-development fallback).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .retrieval.access import ROLE_NAMESPACES

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{key} must be a boolean (true/false), got {raw!r}")


def _number(env: Mapping[str, str], key: str, default: float, cast: type) -> float:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return cast(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a number, got {raw!r}") from error


@dataclass(frozen=True)
class RAGSettings:
    """Adapter configuration. See rag/README.md for the meaning of each field."""

    database_url: str | None = None  # RAG_DATABASE_URL (None => rag.index.config.get_dsn())
    role: str = "question_planner"  # RAG_ROLE
    rerank: bool = False  # RAG_RERANK
    pool_min: int = 1  # RAG_POOL_MIN
    pool_max: int = 4  # RAG_POOL_MAX
    pool_timeout_seconds: float = 1.5  # RAG_POOL_TIMEOUT_SECONDS (wait for a free connection)
    connect_timeout_seconds: float = 3.0  # RAG_CONNECT_TIMEOUT_SECONDS
    device: str | None = None  # RAG_DEVICE (cpu | mps | cuda; None => auto)
    filter_difficulty: bool = True  # RAG_FILTER_DIFFICULTY (question source only)
    filter_competency: bool = False  # RAG_FILTER_COMPETENCY
    drop_low_confidence: bool = False  # RAG_DROP_LOW_CONFIDENCE

    def __post_init__(self) -> None:
        if self.role not in ROLE_NAMESPACES:
            raise ValueError(f"unknown role {self.role!r}; known: {sorted(ROLE_NAMESPACES)}")
        if not 1 <= self.pool_min <= self.pool_max:
            raise ValueError("require 1 <= pool_min <= pool_max")
        if self.pool_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RAGSettings:
        env = os.environ if env is None else env
        default = cls()
        return cls(
            database_url=env.get("RAG_DATABASE_URL") or None,
            role=(env.get("RAG_ROLE") or default.role).strip(),
            rerank=_bool(env, "RAG_RERANK", default.rerank),
            pool_min=int(_number(env, "RAG_POOL_MIN", default.pool_min, int)),
            pool_max=int(_number(env, "RAG_POOL_MAX", default.pool_max, int)),
            pool_timeout_seconds=float(
                _number(env, "RAG_POOL_TIMEOUT_SECONDS", default.pool_timeout_seconds, float)
            ),
            connect_timeout_seconds=float(
                _number(env, "RAG_CONNECT_TIMEOUT_SECONDS", default.connect_timeout_seconds, float)
            ),
            device=(env.get("RAG_DEVICE") or "").strip() or None,
            filter_difficulty=_bool(env, "RAG_FILTER_DIFFICULTY", default.filter_difficulty),
            filter_competency=_bool(env, "RAG_FILTER_COMPETENCY", default.filter_competency),
            drop_low_confidence=_bool(env, "RAG_DROP_LOW_CONFIDENCE", default.drop_low_confidence),
        )
