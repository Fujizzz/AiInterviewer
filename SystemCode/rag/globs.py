"""Tiny glob matcher used for the declarative include/exclude lists in
sources.yaml. Semantics: `*` matches within one path segment, `**` matches
across segments (including zero), `?` one char, `{a,b}` alternation.
Patterns are matched against the full relative path (git) or URL path
(crawler)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


def _expand_braces(pattern: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(_expand_braces(pattern[: m.start()] + alt + pattern[m.end() :]))
    return out


@lru_cache(maxsize=1024)
def _compile_one(pattern: str) -> re.Pattern:
    i, n, out = 0, len(pattern), []
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")  # `**/` = zero or more directories
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def compile_glob(pattern: str) -> list[re.Pattern]:
    return [_compile_one(p) for p in _expand_braces(pattern)]


def matches(path: str, pattern: str) -> bool:
    return any(rx.match(path) for rx in compile_glob(pattern))


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(matches(path, p) for p in patterns)


def is_selected(path: str, include: list[str], exclude: list[str]) -> bool:
    """Included iff (no include list OR matches an include) AND matches no exclude."""
    if include and not match_any(path, include):
        return False
    if exclude and match_any(path, exclude):
        return False
    return True
