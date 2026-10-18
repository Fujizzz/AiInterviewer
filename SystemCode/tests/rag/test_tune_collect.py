"""Regression: tune.collect must not ask for the candidate namespace (origin/main crashed with
'refusing to build a candidate-namespace filter without candidate_id' in eval --tune and
--ident-study)."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("pgvector")

from rag.eval.gold import GoldQuery, GoldSet, Matcher  # noqa: E402
from rag.eval.tune import collect  # noqa: E402


class _Rows:
    def fetchall(self):
        return []


class _Conn:
    def execute(self, sql, params=None):
        return _Rows()


class _Retriever:
    conn = _Conn()

    def __init__(self):
        self.namespaces = None

    def _dense(self, intent, namespaces, n):
        self.namespaces = list(namespaces)
        return []


def test_collect_drops_the_candidate_namespace_for_the_question_planner_role():
    gold = GoldSet("draft", [GoldQuery("q1", "kafka exactly once", "conceptual", [Matcher()])])
    r = _Retriever()
    cache = collect(gold, "question_planner", r)
    assert set(cache) == {"q1"}
    assert r.namespaces and "candidate" not in r.namespaces
