"""Namespace access control by agent role. THE single place to configure it.

Enforced inside the retrieval layer (every SQL statement is filtered on the
resolved namespaces and returned rows are re-checked); callers cannot widen
access by passing `intent.namespaces`.
"""

from __future__ import annotations

from collections.abc import Sequence

ROLE_NAMESPACES: dict[str, tuple[str, ...]] = {
    "question_planner": ("candidate", "job", "technical", "question"),
    "evaluator": ("rubric", "technical"),
    "recommender": ("job", "candidate"),
}

# Hard deny, applied after the role table so a config typo cannot expose it.
NEVER_RETRIEVABLE: frozenset[str] = frozenset({"test"})

KNOWN_NAMESPACES: frozenset[str] = frozenset(
    {"candidate", "job", "technical", "rubric", "question", "test"}
)


class NamespaceAccessError(PermissionError):
    """Role is unknown, or the intent asks for a namespace the role may not read."""


def allowed_for_role(role: str) -> tuple[str, ...]:
    if role not in ROLE_NAMESPACES:
        raise NamespaceAccessError(f"unknown role {role!r}; known roles: {sorted(ROLE_NAMESPACES)}")
    return tuple(ns for ns in ROLE_NAMESPACES[role] if ns not in NEVER_RETRIEVABLE)


def resolve_namespaces(role: str, requested: Sequence[str] | None = None) -> tuple[str, ...]:
    """Namespaces this call may read.

    requested=None  -> everything the role may read.
    requested=[...] -> must be an exact subset (names are case-sensitive: 'Technical' and 'TEST'
                       are not namespaces) of that; anything else raises NamespaceAccessError
                       (fail loud rather than silently returning a narrower result).
    """
    allowed = allowed_for_role(role)
    if requested is None:
        return allowed
    if isinstance(requested, str):
        requested = [requested]
    req = tuple(dict.fromkeys(requested))  # de-dupe, keep order
    if not req:
        raise NamespaceAccessError("intent.namespaces is empty; pass None to use the role default")
    denied = [n for n in req if n not in allowed]
    if denied:
        unknown = [n for n in denied if n not in KNOWN_NAMESPACES]
        extra = f"; unknown namespace names (case-sensitive): {unknown}" if unknown else ""
        raise NamespaceAccessError(
            f"role {role!r} may not read namespaces {denied} (allowed: {list(allowed)}){extra}"
        )
    return req


CANDIDATE_NAMESPACE = "candidate"


def scope_namespaces(namespaces: Sequence[str], candidate_id: str | None) -> tuple[str, ...]:
    """Fail-closed candidate scoping, applied after role resolution.

    The `candidate` namespace is only readable together with a concrete candidate_id; without one
    it is dropped (never widened to "all candidates"). May return an empty tuple -- callers must
    then return no results without touching the database.
    """
    if candidate_id and candidate_id.strip():
        return tuple(namespaces)
    return tuple(n for n in namespaces if n != CANDIDATE_NAMESPACE)
